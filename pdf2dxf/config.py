from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReconstructionConfig:
    endpoint_tolerance: float = 1e-5
    line_collinearity_tolerance: float = 1e-5
    circle_relative_error: float = 0.002
    circle_absolute_error: float = 0.02
    circle_aspect_tolerance: float = 0.02
    arc_relative_error: float = 0.002
    arc_absolute_error: float = 0.02
    minimum_arc_sweep_degrees: float = 8.0
    default_curve_steps: int | None = None


@dataclass(frozen=True)
class SheetAnalysisConfig:
    border_margin_ratio: float = 0.035
    border_minimum_area_ratio: float = 0.70
    title_block_right_ratio: float = 0.45
    title_block_bottom_ratio: float = 0.35
    title_block_minimum_cells: int = 3


@dataclass(frozen=True)
class DxfExportConfig:
    dxf_version: str = "R2000"
    text_style: str = "Standard"
    dimension_style: str = "EZDXF"
    center_linetype: str = "CENTER"
    hidden_linetype: str = "HIDDEN"
