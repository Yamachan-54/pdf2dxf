from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Callable

import ezdxf
from ezdxf.enums import MTextEntityAlignment, TextEntityAlignment

from ..cad.model import (
    CadArc,
    CadCircle,
    CadDimension,
    CadEntityType,
    CadLine,
    CadModel,
    CadMText,
    CadPolyline,
    CadText,
)
from ..config import DxfExportConfig
from ..ir.entities import SemanticType
from .base import ExportResult, ExportTrace


_INVALID_LAYER_CHARS = re.compile(r'[<>/\\":;?*|=`]')
_BASE_LAYERS = {
    "GEOMETRY",
    "HIDDEN",
    "CENTER",
    "DIMENSION",
    "TEXT",
    "HATCH",
    "REFERENCE",
}
_SEMANTIC_LAYERS = {
    SemanticType.OUTER_CONTOUR: "GEOMETRY",
    SemanticType.INNER_CONTOUR: "GEOMETRY",
    SemanticType.HOLE: "GEOMETRY",
    SemanticType.CENTER_LINE: "CENTER",
    SemanticType.HIDDEN_LINE: "HIDDEN",
    SemanticType.DIMENSION_LINE: "DIMENSION",
    SemanticType.DIMENSION_EXTENSION_LINE: "DIMENSION",
    SemanticType.DIMENSION_TEXT: "DIMENSION",
    SemanticType.CONSTRUCTION_LINE: "REFERENCE",
    SemanticType.TEXT: "TEXT",
    SemanticType.HATCH: "HATCH",
    SemanticType.SHEET_BORDER: "REFERENCE",
    SemanticType.TITLE_BLOCK_LINE: "REFERENCE",
    SemanticType.REVISION_TABLE_LINE: "REFERENCE",
    SemanticType.UNKNOWN: "REFERENCE",
}
_UNIT_CODES = {"mm": 4, "inch": 1, "pt": 0}
_LINETYPE_PATTERNS = {
    "CENTER": (
        "Center ____ _ ____ _ ____ _ ____",
        [2.0, 1.25, -0.25, 0.25, -0.25],
    ),
    "HIDDEN": ("Hidden __ __ __ __ __ __ __", [1.0, 0.5, -0.5]),
}


def safe_layer_name(name: str) -> str:
    cleaned = _INVALID_LAYER_CHARS.sub("_", name).strip()[:255]
    return cleaned or "0"


@dataclass(frozen=True)
class LayerPolicy:
    include_view_prefix: bool = False

    def layer_for(self, semantic_type: SemanticType, view: str | None = None) -> str:
        base = _SEMANTIC_LAYERS.get(semantic_type, "REFERENCE")
        if self.include_view_prefix and view:
            return safe_layer_name(f"{view}_{base}")
        return base


class _EntityWriter:
    """Small entity handlers at the only boundary that depends on ezdxf types."""

    def __init__(
        self,
        modelspace: Any,
        layer_policy: LayerPolicy,
        config: DxfExportConfig,
    ) -> None:
        self.modelspace = modelspace
        self.layer_policy = layer_policy
        self.config = config
        self._handlers: dict[type[object], Callable[[Any, dict[str, object]], Any]] = {
            CadLine: self._line,
            CadCircle: self._circle,
            CadArc: self._arc,
            CadPolyline: self._polyline,
            CadText: self._text,
            CadMText: self._mtext,
            CadDimension: self._dimension,
        }

    def write(self, entity: CadEntityType) -> ExportTrace:
        layer = self.layer_policy.layer_for(entity.semantic_type, entity.view)
        linetype = "BYLAYER"
        attributes: dict[str, object] = {"layer": layer, "linetype": linetype}
        if entity.true_color is not None:
            attributes["true_color"] = entity.true_color
        handler = self._handlers.get(type(entity))
        cad_type = type(entity).__name__
        if handler is None:
            return self._trace(
                entity, cad_type, None, layer, linetype, "unresolved",
                reason="no ezdxf handler registered",
            )
        if isinstance(entity, CadDimension) and not entity.resolved:
            return self._trace(
                entity, cad_type, None, layer, linetype, "unresolved",
                reason="dimension definition points or dimension-line point are missing",
            )
        dxf_entity = handler(entity, attributes)
        return self._trace(
            entity,
            cad_type,
            dxf_entity.dxftype(),
            layer,
            linetype,
            "exported",
            handle=dxf_entity.dxf.handle,
        )

    @staticmethod
    def _trace(
        entity: CadEntityType,
        cad_type: str,
        dxf_type: str | None,
        layer: str,
        linetype: str,
        status: str,
        *,
        handle: str | None = None,
        reason: str | None = None,
    ) -> ExportTrace:
        return ExportTrace(
            source_id=entity.source_id,
            ir_id=entity.source_id,
            cad_type=cad_type,
            dxf_type=dxf_type,
            layer=layer,
            linetype=linetype,
            confidence=entity.confidence,
            status=status,
            handle=handle,
            reason=reason,
        )

    def _line(self, entity: CadLine, attributes: dict[str, object]) -> Any:
        return self.modelspace.add_line(
            (entity.segment.start.x, entity.segment.start.y),
            (entity.segment.end.x, entity.segment.end.y),
            dxfattribs=attributes,
        )

    def _circle(self, entity: CadCircle, attributes: dict[str, object]) -> Any:
        return self.modelspace.add_circle(
            (entity.center.x, entity.center.y),
            entity.radius,
            dxfattribs=attributes,
        )

    def _arc(self, entity: CadArc, attributes: dict[str, object]) -> Any:
        return self.modelspace.add_arc(
            (entity.center.x, entity.center.y),
            entity.radius,
            entity.start_angle,
            entity.end_angle,
            dxfattribs=attributes,
        )

    def _polyline(self, entity: CadPolyline, attributes: dict[str, object]) -> Any:
        return self.modelspace.add_lwpolyline(
            [(point.x, point.y) for point in entity.points],
            format="xy",
            close=entity.closed,
            dxfattribs=attributes,
        )

    def _text(self, entity: CadText, attributes: dict[str, object]) -> Any:
        attributes["style"] = self._safe_style(entity.style)
        text = self.modelspace.add_text(
            entity.text,
            height=entity.height,
            rotation=entity.rotation,
            dxfattribs=attributes,
        )
        alignment = _text_alignment(entity.alignment)
        text.set_placement((entity.insertion.x, entity.insertion.y), align=alignment)
        return text

    def _mtext(self, entity: CadMText, attributes: dict[str, object]) -> Any:
        attributes.update(
            {
                "style": self._safe_style(entity.style),
                "char_height": entity.height,
            }
        )
        mtext = self.modelspace.add_mtext(entity.text, dxfattribs=attributes)
        if entity.width is not None and entity.width > 0:
            mtext.dxf.width = entity.width
        mtext.set_location(
            (entity.insertion.x, entity.insertion.y),
            rotation=entity.rotation,
            attachment_point=_mtext_alignment(entity.alignment).value,
        )
        return mtext

    def _dimension(self, entity: CadDimension, attributes: dict[str, object]) -> Any:
        assert entity.first_point is not None
        assert entity.second_point is not None
        assert entity.dimension_line_point is not None
        dimension = self.modelspace.add_linear_dim(
            base=(entity.dimension_line_point.x, entity.dimension_line_point.y),
            p1=(entity.first_point.x, entity.first_point.y),
            p2=(entity.second_point.x, entity.second_point.y),
            angle=entity.angle,
            dimstyle=self.config.dimension_style,
            dxfattribs=attributes,
        )
        dimension.render()
        return dimension.dimension

    def _safe_style(self, style: str) -> str:
        document = self.modelspace.doc
        return style if style in document.styles else self.config.text_style


class DxfExporter:
    def __init__(
        self,
        layer_policy: LayerPolicy | None = None,
        config: DxfExportConfig | None = None,
    ) -> None:
        self.layer_policy = layer_policy or LayerPolicy()
        self.config = config or DxfExportConfig()

    def export(self, model: CadModel, destination: Path) -> ExportResult:
        units_code = _UNIT_CODES.get(model.unit)
        if units_code is None:
            raise ValueError(f"Unsupported CAD model unit: {model.unit}")
        document = ezdxf.new(
            self.config.dxf_version,
            setup=["styles", "dimstyles"],
            units=units_code,
        )
        self._define_resources(document, model)
        writer = _EntityWriter(
            document.modelspace(), self.layer_policy, self.config
        )
        traces = tuple(writer.write(entity) for entity in model.entities)
        destination.parent.mkdir(parents=True, exist_ok=True)
        document.saveas(destination)
        return ExportResult(traces)

    def _define_resources(self, document: Any, model: CadModel) -> None:
        center = self._ensure_linetype(document, self.config.center_linetype)
        hidden = self._ensure_linetype(document, self.config.hidden_linetype)
        layer_linetypes = {
            "CENTER": center,
            "HIDDEN": hidden,
        }
        layers = set(_BASE_LAYERS)
        layers.update(
            self.layer_policy.layer_for(entity.semantic_type, entity.view)
            for entity in model.entities
        )
        for layer in sorted(layers):
            if layer not in document.layers:
                document.layers.add(
                    layer,
                    color=7,
                    linetype=layer_linetypes.get(_base_layer(layer), "Continuous"),
                )

    @staticmethod
    def _ensure_linetype(document: Any, name: str) -> str:
        if name in document.linetypes:
            return name
        definition = _LINETYPE_PATTERNS.get(name)
        if definition is None:
            return "Continuous"
        description, pattern = definition
        try:
            document.linetypes.add(name, description=description, pattern=pattern)
            return name
        except Exception:
            return "Continuous"


def _base_layer(layer: str) -> str:
    for name in _BASE_LAYERS:
        if layer == name or layer.endswith(f"_{name}"):
            return name
    return "REFERENCE"


def _text_alignment(value: str) -> TextEntityAlignment:
    normalized = value.upper()
    return {
        "LEFT": TextEntityAlignment.LEFT,
        "CENTER": TextEntityAlignment.CENTER,
        "RIGHT": TextEntityAlignment.RIGHT,
        "ALIGNED": TextEntityAlignment.ALIGNED,
        "MIDDLE": TextEntityAlignment.MIDDLE,
        "FIT": TextEntityAlignment.FIT,
        "MIDDLE_CENTER": TextEntityAlignment.MIDDLE_CENTER,
        "MIDDLE_LEFT": TextEntityAlignment.MIDDLE_LEFT,
        "MIDDLE_RIGHT": TextEntityAlignment.MIDDLE_RIGHT,
    }.get(normalized, TextEntityAlignment.LEFT)


def _mtext_alignment(value: str) -> MTextEntityAlignment:
    normalized = value.upper()
    return {
        "TOP_LEFT": MTextEntityAlignment.TOP_LEFT,
        "TOP_CENTER": MTextEntityAlignment.TOP_CENTER,
        "TOP_RIGHT": MTextEntityAlignment.TOP_RIGHT,
        "MIDDLE_LEFT": MTextEntityAlignment.MIDDLE_LEFT,
        "MIDDLE_CENTER": MTextEntityAlignment.MIDDLE_CENTER,
        "MIDDLE_RIGHT": MTextEntityAlignment.MIDDLE_RIGHT,
        "BOTTOM_LEFT": MTextEntityAlignment.BOTTOM_LEFT,
        "BOTTOM_CENTER": MTextEntityAlignment.BOTTOM_CENTER,
        "BOTTOM_RIGHT": MTextEntityAlignment.BOTTOM_RIGHT,
    }.get(normalized, MTextEntityAlignment.TOP_LEFT)
