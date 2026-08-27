from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..geometry import Point


class SemanticType(str, Enum):
    OUTER_CONTOUR = "outer_contour"
    INNER_CONTOUR = "inner_contour"
    HOLE = "hole"
    CENTER_LINE = "center_line"
    HIDDEN_LINE = "hidden_line"
    DIMENSION_LINE = "dimension_line"
    DIMENSION_EXTENSION_LINE = "dimension_extension_line"
    CONSTRUCTION_LINE = "construction_line"
    TEXT = "text"
    DIMENSION_TEXT = "dimension_text"
    HATCH = "hatch"
    SHEET_BORDER = "sheet_border"
    TITLE_BLOCK_LINE = "title_block_line"
    REVISION_TABLE_LINE = "revision_table_line"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SourceEvidence:
    parser: str
    page: int
    object_index: int | None = None
    item_index: int | None = None
    native_type: str | None = None


@dataclass(frozen=True)
class Style:
    true_color: int | None = None
    line_width: float | None = None
    dash_pattern: str | None = None


@dataclass(frozen=True)
class LineGeometry:
    start: Point
    end: Point


@dataclass(frozen=True)
class CircleGeometry:
    center: Point
    radius: float


@dataclass(frozen=True)
class ArcGeometry:
    center: Point
    radius: float
    start_angle: float
    end_angle: float


@dataclass(frozen=True)
class PolylineGeometry:
    points: tuple[Point, ...]
    closed: bool = False


@dataclass(frozen=True)
class TextGeometry:
    text: str
    insertion: Point
    height: float
    rotation: float = 0.0
    width: float | None = None
    multiline: bool = False
    alignment: str = "LEFT"
    style: str = "Standard"


@dataclass(frozen=True)
class BezierGeometry:
    control_points: tuple[Point, Point, Point, Point]
    path_id: str


@dataclass(frozen=True)
class DimensionGeometry:
    dimension_type: str
    value: float | None
    references: tuple[str, ...]
    orientation: str | None = None
    first_point: Point | None = None
    second_point: Point | None = None
    dimension_line_point: Point | None = None
    angle: float = 0.0


Geometry = (
    LineGeometry | CircleGeometry | ArcGeometry | PolylineGeometry |
    TextGeometry | BezierGeometry | DimensionGeometry
)


@dataclass
class Entity:
    id: str
    primitive: str
    geometry: Geometry
    semantic_type: SemanticType = SemanticType.UNKNOWN
    view: str | None = None
    confidence: float = 0.0
    source: SourceEvidence | None = None
    style: Style = field(default_factory=Style)
    page: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)
