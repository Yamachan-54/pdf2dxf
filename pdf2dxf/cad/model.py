from __future__ import annotations

from dataclasses import dataclass, field

from ..geometry import Point, Segment
from ..ir.drawing import Drawing
from ..ir.entities import (
    ArcGeometry, CircleGeometry, DimensionGeometry, LineGeometry,
    PolylineGeometry, SemanticType, TextGeometry,
)


@dataclass(frozen=True)
class CadEntity:
    source_id: str
    semantic_type: SemanticType
    view: str | None
    confidence: float
    true_color: int | None
    page: int


@dataclass(frozen=True)
class CadLine(CadEntity):
    segment: Segment


@dataclass(frozen=True)
class CadCircle(CadEntity):
    center: Point
    radius: float


@dataclass(frozen=True)
class CadArc(CadEntity):
    center: Point
    radius: float
    start_angle: float
    end_angle: float


@dataclass(frozen=True)
class CadPolyline(CadEntity):
    points: tuple[Point, ...]
    closed: bool


@dataclass(frozen=True)
class CadText(CadEntity):
    text: str
    insertion: Point
    height: float
    rotation: float
    alignment: str = "LEFT"
    style: str = "Standard"


@dataclass(frozen=True)
class CadMText(CadEntity):
    text: str
    insertion: Point
    height: float
    rotation: float
    width: float | None = None
    alignment: str = "TOP_LEFT"
    style: str = "Standard"


@dataclass(frozen=True)
class CadDimension(CadEntity):
    dimension_type: str
    value: float | None
    references: tuple[str, ...]
    first_point: Point | None = None
    second_point: Point | None = None
    dimension_line_point: Point | None = None
    angle: float = 0.0

    @property
    def resolved(self) -> bool:
        return (
            self.dimension_type == "linear"
            and self.first_point is not None
            and self.second_point is not None
            and self.dimension_line_point is not None
        )


CadEntityType = (
    CadLine | CadCircle | CadArc | CadPolyline | CadText | CadMText |
    CadDimension
)


@dataclass
class CadModel:
    unit: str
    entities: list[CadEntityType] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)


def build_cad_model(drawing: Drawing) -> CadModel:
    model = CadModel(drawing.unit, metadata={"drawing_schema": drawing.schema_version})
    for entity in drawing.entities:
        if entity.metadata.get("suppress_cad_export") is True:
            continue
        common = (
            entity.id, entity.semantic_type, entity.view, entity.confidence,
            entity.style.true_color, entity.page,
        )
        geometry = entity.geometry
        if isinstance(geometry, LineGeometry):
            model.entities.append(CadLine(*common, Segment(geometry.start, geometry.end)))
        elif isinstance(geometry, CircleGeometry):
            model.entities.append(CadCircle(*common, geometry.center, geometry.radius))
        elif isinstance(geometry, ArcGeometry):
            model.entities.append(CadArc(*common, geometry.center, geometry.radius, geometry.start_angle, geometry.end_angle))
        elif isinstance(geometry, PolylineGeometry):
            model.entities.append(CadPolyline(*common, geometry.points, geometry.closed))
        elif isinstance(geometry, TextGeometry):
            if geometry.multiline or "\n" in geometry.text:
                alignment = geometry.alignment if geometry.alignment != "LEFT" else "TOP_LEFT"
                model.entities.append(
                    CadMText(
                        *common, geometry.text, geometry.insertion, geometry.height,
                        geometry.rotation, geometry.width, alignment, geometry.style,
                    )
                )
            else:
                model.entities.append(
                    CadText(
                        *common, geometry.text, geometry.insertion, geometry.height,
                        geometry.rotation, geometry.alignment, geometry.style,
                    )
                )
        elif isinstance(geometry, DimensionGeometry):
            model.entities.append(
                CadDimension(
                    *common, geometry.dimension_type, geometry.value,
                    geometry.references, geometry.first_point,
                    geometry.second_point, geometry.dimension_line_point,
                    geometry.angle,
                )
            )
    return model
