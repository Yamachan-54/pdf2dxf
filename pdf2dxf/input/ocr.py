from __future__ import annotations

import csv
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any, Iterable, Protocol

from ..geometry import Point
from ..ir.drawing import Drawing, Sheet
from ..ir.entities import (
    Entity, SemanticType, SourceEvidence, Style, TextGeometry,
)


class OcrAdapter(Protocol):
    def augment(
        self, source: Path, drawing: Drawing, *, pages: Iterable[int] | None
    ) -> Drawing:
        ...


@dataclass(frozen=True)
class TesseractOcrConfig:
    language: str = "eng"
    dpi: int = 300
    minimum_confidence: float = 70.0
    executable: str = "tesseract"
    page_segmentation_mode: int = 11
    skip_pages_with_native_text: bool = True


class TesseractOcrAdapter:
    """Optional OCR adapter; Tesseract details do not leak into Drawing IR."""

    def __init__(self, config: TesseractOcrConfig | None = None) -> None:
        self.config = config or TesseractOcrConfig()

    def augment(
        self, source: Path, drawing: Drawing, *, pages: Iterable[int] | None
    ) -> Drawing:
        executable = shutil.which(self.config.executable)
        if executable is None:
            raise RuntimeError(
                f"OCR command was not found: {self.config.executable}. "
                "Install Tesseract OCR or omit --ocr."
            )
        self._validate_languages(executable)
        try:
            import pymupdf  # type: ignore
        except ImportError as exc:
            raise RuntimeError("PyMuPDF is required for OCR page rendering") from exc

        document = pymupdf.open(source)
        selected = list(pages) if pages is not None else list(range(len(document)))
        sheets = {sheet.page: sheet for sheet in drawing.sheets}
        native_text_pages = {
            entity.page for entity in drawing.entities
            if isinstance(entity.geometry, TextGeometry)
            and entity.source is not None
            and entity.source.parser == "vector_pdf"
        }
        added = 0
        processed_pages: list[int] = []
        try:
            with tempfile.TemporaryDirectory(prefix="pdf2dxf-ocr-") as directory:
                temporary = Path(directory)
                for page_index in selected:
                    page_number = page_index + 1
                    if (
                        self.config.skip_pages_with_native_text
                        and page_number in native_text_pages
                    ):
                        continue
                    sheet = sheets.get(page_number)
                    if sheet is None:
                        continue
                    page = document[page_index]
                    zoom = self.config.dpi / 72.0
                    pixmap = page.get_pixmap(
                        matrix=pymupdf.Matrix(zoom, zoom), alpha=False
                    )
                    image_path = temporary / f"page-{page_number:04d}.png"
                    pixmap.save(image_path)
                    result = subprocess.run(
                        [
                            executable, str(image_path), "stdout", "-l",
                            self.config.language, "--psm",
                            str(self.config.page_segmentation_mode), "tsv",
                        ],
                        check=False,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                    )
                    if result.returncode != 0:
                        message = result.stderr.strip() or "unknown Tesseract error"
                        raise RuntimeError(
                            f"OCR failed on page {page_number}: {message}"
                        )
                    entities = self.entities_from_tsv(
                        result.stdout, page_number=page_number, sheet=sheet,
                        image_width=pixmap.width, image_height=pixmap.height,
                    )
                    recovered = self._recover_square_dimension_words(
                        result.stdout, page=page, zoom=zoom,
                        matrix=pymupdf.Matrix(zoom, zoom),
                        temporary=temporary, executable=executable,
                        page_number=page_number, sheet=sheet,
                        image_width=pixmap.width, image_height=pixmap.height,
                        first_entity_number=len(entities) + 1,
                    )
                    entities.extend(recovered)
                    drawing.entities.extend(entities)
                    added += len(entities)
                    processed_pages.append(page_number)
        finally:
            document.close()

        drawing.metadata["ocr"] = {
            "adapter": "tesseract",
            "language": self.config.language,
            "dpi": self.config.dpi,
            "minimum_confidence": self.config.minimum_confidence,
            "processed_pages": processed_pages,
            "added_text_entities": added,
        }
        return drawing

    def _validate_languages(self, executable: str) -> None:
        result = subprocess.run(
            [executable, "--list-langs"], check=False, capture_output=True,
            text=True, encoding="utf-8",
        )
        available = {
            line.strip() for line in result.stdout.splitlines()
            if line.strip() and not line.startswith("List of available")
        }
        requested = set(self.config.language.split("+"))
        missing = requested - available
        if result.returncode != 0 or missing:
            detail = ", ".join(sorted(missing)) or result.stderr.strip()
            raise RuntimeError(
                f"Requested Tesseract language data is unavailable: {detail}"
            )

    def entities_from_tsv(
        self, tsv: str, *, page_number: int, sheet: Sheet,
        image_width: int, image_height: int,
    ) -> list[Entity]:
        if image_width <= 0 or image_height <= 0:
            raise ValueError("OCR image dimensions must be positive")
        sx0, sy0, sx1, sy1 = sheet.bbox
        scale_x = (sx1 - sx0) / image_width
        scale_y = (sy1 - sy0) / image_height
        entities: list[Entity] = []
        reader = csv.DictReader(StringIO(tsv), delimiter="\t")
        for row in reader:
            if row.get("level") != "5":
                continue
            text = (row.get("text") or "").strip()
            try:
                confidence = float(row.get("conf", "-1"))
                left = int(row.get("left", "0"))
                top = int(row.get("top", "0"))
                width = int(row.get("width", "0"))
                height = int(row.get("height", "0"))
            except ValueError:
                continue
            if (
                not text or confidence < self.config.minimum_confidence
                or width <= 0 or height <= 0
            ):
                continue
            insertion = Point(
                sx0 + left * scale_x,
                sy1 - (top + height) * scale_y,
            )
            entity_number = len(entities) + 1
            entities.append(
                Entity(
                    f"OCR_P{page_number:03d}_{entity_number:05d}", "text",
                    TextGeometry(
                        text, insertion, height * scale_y,
                        width=width * scale_x,
                    ),
                    SemanticType.TEXT,
                    confidence=confidence / 100.0,
                    source=SourceEvidence(
                        "tesseract_ocr", page_number,
                        _integer(row.get("block_num")),
                        _integer(row.get("word_num")), "ocr_word",
                    ),
                    style=Style(), page=page_number,
                    metadata={
                        "ocr_language": self.config.language,
                        "ocr_confidence": confidence,
                        "pixel_bbox": [left, top, width, height],
                        "line_num": _integer(row.get("line_num")),
                    },
                )
            )
        return entities

    def _recover_square_dimension_words(
        self,
        tsv: str,
        *,
        page: Any,
        zoom: float,
        matrix: Any,
        temporary: Path,
        executable: str,
        page_number: int,
        sheet: Sheet,
        image_width: int,
        image_height: int,
        first_entity_number: int,
    ) -> list[Entity]:
        """Retry low-confidence square-dimension candidates one line at a time."""
        rows = list(csv.DictReader(StringIO(tsv), delimiter="\t"))
        line_rows = {
            _ocr_line_key(row): row for row in rows if row.get("level") == "4"
        }
        candidates: dict[tuple[str, str, str], dict[str, str]] = {}
        for row in rows:
            if row.get("level") != "5":
                continue
            text = (row.get("text") or "").strip()
            try:
                confidence = float(row.get("conf", "-1"))
            except ValueError:
                continue
            if (
                confidence >= self.config.minimum_confidence
                or not re.fullmatch(r"0[0-9.,]{3,}", text)
            ):
                continue
            candidates.setdefault(_ocr_line_key(row), row)

        sx0, sy0, sx1, sy1 = sheet.bbox
        scale_x = (sx1 - sx0) / image_width
        scale_y = (sy1 - sy0) / image_height
        minimum_retry_confidence = max(50.0, self.config.minimum_confidence - 20.0)
        recovered: list[Entity] = []
        for retry_index, (line_key, original_row) in enumerate(candidates.items(), 1):
            line = line_rows.get(line_key)
            if line is None:
                continue
            try:
                line_left = int(line.get("left", "0"))
                line_top = int(line.get("top", "0"))
                line_width = int(line.get("width", "0"))
                line_height = int(line.get("height", "0"))
            except ValueError:
                continue
            padding = max(10, int(line_height * 0.35))
            crop_left = max(0, line_left - padding)
            crop_top = max(0, line_top - padding)
            crop_right = min(image_width, line_left + line_width + padding)
            crop_bottom = min(image_height, line_top + line_height + padding)
            if crop_right <= crop_left or crop_bottom <= crop_top:
                continue
            clip = page.rect.__class__(
                crop_left / zoom, crop_top / zoom,
                crop_right / zoom, crop_bottom / zoom,
            )
            crop = page.get_pixmap(
                matrix=matrix, clip=clip, alpha=False,
            )
            if crop.width <= 0 or crop.height <= 0:
                continue
            crop_path = temporary / f"page-{page_number:04d}-line-{retry_index:04d}.png"
            crop.save(crop_path)
            result = subprocess.run(
                [
                    executable, str(crop_path), "stdout", "-l",
                    self.config.language, "--psm", "7", "tsv",
                ],
                check=False, capture_output=True, text=True, encoding="utf-8",
            )
            if result.returncode != 0:
                continue
            matches = []
            for row in csv.DictReader(StringIO(result.stdout), delimiter="\t"):
                text = (row.get("text") or "").strip()
                try:
                    confidence = float(row.get("conf", "-1"))
                    left = crop_left + int(row.get("left", "0"))
                    top = crop_top + int(row.get("top", "0"))
                    width = int(row.get("width", "0"))
                    height = int(row.get("height", "0"))
                except ValueError:
                    continue
                if (
                    row.get("level") == "5"
                    and confidence >= minimum_retry_confidence
                    and re.fullmatch(r"0[1-9][0-9]{2,}(?:[.,][0-9]+)?", text)
                    and width > 0 and height > 0
                ):
                    matches.append((text, confidence, left, top, width, height))
            if len(matches) != 1:
                continue
            text, confidence, left, top, width, height = matches[0]
            entity_number = first_entity_number + len(recovered)
            recovered.append(
                Entity(
                    f"OCR_P{page_number:03d}_{entity_number:05d}", "text",
                    TextGeometry(
                        text,
                        Point(sx0 + left * scale_x, sy1 - (top + height) * scale_y),
                        height * scale_y, width=width * scale_x,
                    ),
                    SemanticType.TEXT, confidence=confidence / 100.0,
                    source=SourceEvidence(
                        "tesseract_ocr", page_number,
                        _integer(original_row.get("block_num")),
                        _integer(original_row.get("word_num")),
                        "ocr_word_line_retry",
                    ),
                    style=Style(), page=page_number,
                    metadata={
                        "ocr_language": self.config.language,
                        "ocr_confidence": confidence,
                        "pixel_bbox": [left, top, width, height],
                        "line_num": _integer(original_row.get("line_num")),
                        "ocr_recovery": "line_psm_7",
                        "first_pass_text": (original_row.get("text") or "").strip(),
                    },
                )
            )
        return recovered


def _integer(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _ocr_line_key(row: dict[str, str]) -> tuple[str, str, str]:
    return (
        row.get("block_num", ""), row.get("par_num", ""),
        row.get("line_num", ""),
    )
