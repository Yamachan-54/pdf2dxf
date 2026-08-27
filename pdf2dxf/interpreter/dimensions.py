from __future__ import annotations

from ..ir.drawing import Drawing


class DimensionInterpreter:
    def analyze(self, drawing: Drawing) -> Drawing:
        """Future hook for lines, arrows and text to DimensionGeometry."""
        return drawing
