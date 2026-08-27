from __future__ import annotations

from dataclasses import dataclass
from math import atan2, degrees
from pathlib import Path
from typing import Any, Iterable

from ..cad.color import rgb_to_true_color
from ..geometry import Point
from ..ir.drawing import Drawing, Sheet
from ..ir.entities import (
    BezierGeometry,
    Entity,
    LineGeometry,
    PolylineGeometry,
    SemanticType,
    SourceEvidence,
    Style,
    TextGeometry,
)


@dataclass(frozen=True)
class PagePlacement:
    page_index: int
    offset_x: float
    offset_y: float


class VectorPdfParser:
    """PyMuPDF adapter that emits source-preserving Drawing IR."""

    def parse(
        self,
        source: Path,
        *,
        pages: Iterable[int] | None,
        unit: str,
        factor: float,
        layout: str,
        page_gap: float,
    ) -> Drawing:
        try:
            import pymupdf  # type: ignore
        except ImportError as exc:
            raise RuntimeError("PyMuPDF is required. Install the project with: pip install -e .") from exc
        try:
            document = pymupdf.open(source)
        except Exception as exc:
            raise RuntimeError(f"Could not open PDF: {exc}") from exc

        selected = list(pages) if pages is not None else list(range(len(document)))
        if any(index < 0 or index >= len(document) for index in selected):
            document.close()
            bad = next(index for index in selected if index < 0 or index >= len(document))
            raise RuntimeError(f"Page {bad + 1} is outside this PDF")
        drawing = Drawing(unit=unit, metadata={"source": str(source), "parser": "vector_pdf"})
        offset_x = offset_y = 0.0
        entity_number = 1
        try:
            for page_index in selected:
                page = document[page_index]
                height_pt = float(page.rect.height)
                width = float(page.rect.width) * factor
                height = height_pt * factor
                page_number = page_index + 1
                drawing.sheets.append(
                    Sheet(f"SHEET_{page_number:03d}", page_number, (offset_x, offset_y, offset_x + width, offset_y + height))
                )
                for object_index, native in enumerate(page.get_drawings()):
                    style = Style(
                        true_color=rgb_to_true_color(native.get("color")),
                        line_width=float(native.get("width") or 0.0) * factor,
                        dash_pattern=str(native.get("dashes") or ""),
                    )
                    path_id = f"P{page_number:03d}_PATH_{object_index:05d}"
                    items = native.get("items", [])
                    for item_index, item in enumerate(items):
                        operation = item[0]
                        evidence = SourceEvidence("vector_pdf", page_number, object_index, item_index, operation)
                        geometry: object | None = None
                        primitive = operation
                        if operation == "l":
                            geometry = LineGeometry(
                                self._point(item[1], factor, height_pt, offset_x, offset_y),
                                self._point(item[2], factor, height_pt, offset_x, offset_y),
                            )
                            primitive = "line"
                        elif operation == "re":
                            rect = item[1]
                            geometry = PolylineGeometry(
                                tuple(
                                    self._xy(x, y, factor, height_pt, offset_x, offset_y)
                                    for x, y in ((rect.x0, rect.y0), (rect.x1, rect.y0), (rect.x1, rect.y1), (rect.x0, rect.y1))
                                ),
                                True,
                            )
                            primitive = "polyline"
                        elif operation == "qu":
                            quad = item[1]
                            geometry = PolylineGeometry(
                                tuple(self._point(value, factor, height_pt, offset_x, offset_y) for value in (quad.ul, quad.ur, quad.lr, quad.ll)),
                                True,
                            )
                            primitive = "polyline"
                        elif operation == "c":
                            control_points = tuple(
                                self._point(value, factor, height_pt, offset_x, offset_y)
                                for value in item[1:5]
                            )
                            geometry = BezierGeometry(control_points, path_id)  # type: ignore[arg-type]
                            primitive = "bezier"
                        if geometry is not None:
                            drawing.entities.append(
                                Entity(
                                    f"E{entity_number:07d}", primitive, geometry, SemanticType.UNKNOWN,
                                    confidence=1.0, source=evidence, style=style, page=page_number,
                                    metadata={"path_id": path_id, "closed_path": bool(native.get("closePath"))},
                                )
                            )
                            entity_number += 1
                for block_index, block in enumerate(page.get_text("dict").get("blocks", [])):
                    if block.get("type") != 0:
                        continue
                    for line_index, line in enumerate(block.get("lines", [])):
                        direction = line.get("dir", (1.0, 0.0))
                        rotation = degrees(atan2(-float(direction[1]), float(direction[0])))
                        for span_index, span in enumerate(line.get("spans", [])):
                            text = str(span.get("text", ""))
                            if not text:
                                continue
                            insertion = self._xy(*span["origin"], factor, height_pt, offset_x, offset_y)
                            bbox = span.get("bbox")
                            width_hint = (float(bbox[2]) - float(bbox[0])) * factor if bbox else None
                            drawing.entities.append(
                                Entity(
                                    f"E{entity_number:07d}", "text",
                                    TextGeometry(text, insertion, float(span.get("size", 1.0)) * factor, rotation, width_hint),
                                    SemanticType.TEXT, confidence=1.0,
                                    source=SourceEvidence("vector_pdf", page_number, block_index, line_index, "text_span"),
                                    style=Style(true_color=self._text_color(span.get("color"))), page=page_number,
                                    metadata={"font": span.get("font"), "span_index": span_index},
                                )
                            )
                            entity_number += 1
                if layout == "horizontal":
                    offset_x += width + page_gap
                elif layout == "vertical":
                    offset_y -= height + page_gap
                elif layout != "overlay":
                    raise RuntimeError(f"Unsupported layout: {layout}")
        finally:
            document.close()
        return drawing

    @staticmethod
    def _xy(x: float, y: float, factor: float, page_height: float, offset_x: float, offset_y: float) -> Point:
        return Point(float(x) * factor + offset_x, (page_height - float(y)) * factor + offset_y)

    @classmethod
    def _point(cls, value: Any, factor: float, page_height: float, offset_x: float, offset_y: float) -> Point:
        return cls._xy(value.x, value.y, factor, page_height, offset_x, offset_y)

    @staticmethod
    def _text_color(value: int | None) -> int | None:
        if value is None:
            return None
        return int(value) & 0xFFFFFF
