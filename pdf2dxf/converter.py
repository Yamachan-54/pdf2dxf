from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .pipeline import ConversionPipeline, UNIT_SETTINGS


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
    dump_ir: Path | None = None,
    debug_dir: Path | None = None,
) -> ConversionResult:
    if unit not in UNIT_SETTINGS:
        raise ConversionError(f"Unsupported unit: {unit}")
    if scale <= 0:
        raise ConversionError("Scale must be greater than zero")
    if curve_steps is not None and curve_steps < 2:
        raise ConversionError("Curve steps must be 2 or greater")
    if layout not in {"horizontal", "vertical", "overlay"}:
        raise ConversionError(f"Unsupported layout: {layout}")
    try:
        output = ConversionPipeline(curve_steps=curve_steps).run(
            source, destination, pages=pages, unit=unit, scale=scale, layout=layout,
            page_gap=page_gap, dump_ir=dump_ir, debug_dir=debug_dir,
        )
    except RuntimeError as exc:
        raise ConversionError(str(exc)) from exc
    return ConversionResult(
        len(output.selected_pages),
        output.export_result.exported_count,
        output.empty_pages,
    )
