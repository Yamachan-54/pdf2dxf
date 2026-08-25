from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .dxf import DxfLine, rgb_to_true_color, write_ascii_dxf
from .geometry import Point, Segment, cubic_bezier, suggested_curve_steps


UNIT_SETTINGS = {
    "mm": (25.4 / 72.0, 4),
    "inch": (1.0 / 72.0, 1),
    "pt": (1.0, 0),
}


@dataclass(frozen=True)
class ConversionResult:
    pages: int
    entities: int
    empty_pages: tuple[int, ...]


class ConversionError(RuntimeError):
    pass


def convert_pdf(
    source: Path,
    destination: Path,
    *,
    pages: Iterable[int] | None = None,
    unit: str = "mm",
    scale: float = 1.0,
    layout: str = "horizontal",
    page_gap: float = 10.0,
    curve_steps: int | None = None,
) -> ConversionResult:
    if unit not in UNIT_SETTINGS:
        raise ConversionError(f"Unsupported unit: {unit}")
    if scale <= 0:
        raise ConversionError("Scale must be greater than zero")
    if curve_steps is not None and curve_steps < 2:
        raise ConversionError("Curve steps must be 2 or greater")

    try:
        import pymupdf  # type: ignore
    except ImportError as exc:
        raise ConversionError(
            "PyMuPDF is required. Install the project with: pip install -e ."
        ) from exc

    try:
        document = pymupdf.open(source)
    except Exception as exc:
        raise ConversionError(f"Could not open PDF: {exc}") from exc

    unit_scale, units_code = UNIT_SETTINGS[unit]
    factor = unit_scale * scale
    selected = list(pages) if pages is not None else list(range(len(document)))
    for page_index in selected:
        if page_index < 0 or page_index >= len(document):
            document.close()
            raise ConversionError(f"Page {page_index + 1} is outside this PDF")

    output: list[DxfLine] = []
    empty_pages: list[int] = []
    offset_x = 0.0
    offset_y = 0.0

    try:
        for page_index in selected:
            page = document[page_index]
            page_height = float(page.rect.height)
            before = len(output)
            layer = f"PDF_PAGE_{page_index + 1}"
            for drawing in page.get_drawings():
                color = rgb_to_true_color(drawing.get("color"))
                for segment in _drawing_segments(drawing, curve_steps):
                    output.append(
                        DxfLine(
                            _transform(segment, factor, page_height, offset_x, offset_y),
                            layer,
                            color,
                        )
                    )
            if len(output) == before:
                empty_pages.append(page_index + 1)

            width = float(page.rect.width) * factor
            height = page_height * factor
            if layout == "horizontal":
                offset_x += width + page_gap
            elif layout == "vertical":
                offset_y -= height + page_gap
            elif layout != "overlay":
                raise ConversionError(f"Unsupported layout: {layout}")
    finally:
        document.close()

    destination.parent.mkdir(parents=True, exist_ok=True)
    write_ascii_dxf(destination, output, units_code)
    return ConversionResult(len(selected), len(output), tuple(empty_pages))


def _point(value: Any) -> Point:
    return Point(float(value.x), float(value.y))


def _drawing_segments(drawing: dict[str, Any], curve_steps: int | None) -> list[Segment]:
    result: list[Segment] = []
    for item in drawing.get("items", []):
        operation = item[0]
        if operation == "l":
            result.append(Segment(_point(item[1]), _point(item[2])))
        elif operation == "re":
            rect = item[1]
            points = [
                Point(rect.x0, rect.y0),
                Point(rect.x1, rect.y0),
                Point(rect.x1, rect.y1),
                Point(rect.x0, rect.y1),
            ]
            result.extend(_closed_segments(points))
        elif operation == "qu":
            quad = item[1]
            points = [_point(quad.ul), _point(quad.ur), _point(quad.lr), _point(quad.ll)]
            result.extend(_closed_segments(points))
        elif operation == "c":
            points = tuple(_point(value) for value in item[1:5])
            steps = curve_steps or suggested_curve_steps(points)  # type: ignore[arg-type]
            result.extend(cubic_bezier(*points, steps))  # type: ignore[arg-type]
    return result


def _closed_segments(points: list[Point]) -> list[Segment]:
    return [Segment(a, b) for a, b in zip(points, points[1:] + points[:1])]


def _transform(
    segment: Segment,
    factor: float,
    page_height: float,
    offset_x: float,
    offset_y: float,
) -> Segment:
    def apply(point: Point) -> Point:
        return Point(
            point.x * factor + offset_x,
            (page_height - point.y) * factor + offset_y,
        )

    return Segment(apply(segment.start), apply(segment.end))
