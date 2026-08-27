from __future__ import annotations

from typing import Protocol

from ..ir.drawing import Drawing


class GeometrySolver(Protocol):
    def solve(self, drawing: Drawing) -> Drawing: ...


class PassthroughSolver:
    """Default solver keeps PDF geometry; explicit constraints can be added later."""

    def solve(self, drawing: Drawing) -> Drawing:
        return drawing
