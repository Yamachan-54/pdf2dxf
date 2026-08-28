from __future__ import annotations

from pathlib import Path

from ..ir.drawing import Drawing


class RasterPdfParser:
    """Extension point for future raster primitive and linework detection."""

    def parse(self, source: Path) -> Drawing:
        raise NotImplementedError("Raster geometry processing is not implemented yet")
