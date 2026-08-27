from __future__ import annotations

from pathlib import Path

from ..ir.drawing import Drawing


class RasterPdfParser:
    """Extension point for future image preprocessing, primitive detection and OCR."""

    def parse(self, source: Path) -> Drawing:
        raise NotImplementedError("Raster PDF and OCR processing is not implemented yet")
