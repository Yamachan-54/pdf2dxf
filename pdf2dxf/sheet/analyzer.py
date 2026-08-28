from __future__ import annotations

from ..config import SheetAnalysisConfig
from ..ir.drawing import Drawing, Sheet
from ..ir.entities import (
    ArcGeometry, BezierGeometry, CircleGeometry, Entity, LineGeometry,
    PolylineGeometry, SemanticType, TextGeometry,
)


class SheetAnalyzer:
    def __init__(self, config: SheetAnalysisConfig | None = None) -> None:
        self.config = config or SheetAnalysisConfig()

    def analyze(self, drawing: Drawing) -> Drawing:
        for sheet in drawing.sheets:
            entities = [entity for entity in drawing.entities if entity.page == sheet.page]
            border_ids = [entity.id for entity in entities if self._is_border(entity, sheet)]
            for entity in entities:
                if entity.id in border_ids:
                    entity.semantic_type = SemanticType.SHEET_BORDER
                    entity.confidence = 0.95
            title_candidates = [
                entity for entity in entities
                if entity.id not in border_ids and self._in_title_region(entity, sheet)
            ]
            separator = self._full_height_title_separator(entities, sheet)
            if separator is not None:
                title_candidates.extend(
                    entity for entity in entities
                    if entity.id not in border_ids
                    and _entity_center(entity)[0] >= separator
                )
                sheet.metadata["drawing_area_bbox"] = (
                    sheet.bbox[0], sheet.bbox[1], separator, sheet.bbox[3]
                )
                sheet.metadata["title_block_separator_x"] = separator
            title_candidates = list({entity.id: entity for entity in title_candidates}.values())
            title_ids: list[str] = []
            if len(title_candidates) >= self.config.title_block_minimum_cells:
                title_ids = [entity.id for entity in title_candidates]
                for entity in title_candidates:
                    entity.semantic_type = SemanticType.TITLE_BLOCK_LINE
                    entity.confidence = 0.7
            sheet.border_entity_ids = tuple(border_ids)
            sheet.title_block_entity_ids = tuple(title_ids)
        return drawing

    @staticmethod
    def _full_height_title_separator(
        entities: list[Entity], sheet: Sheet
    ) -> float | None:
        sx0, sy0, sx1, sy1 = sheet.bbox
        width, height = sx1 - sx0, sy1 - sy0
        candidates: list[float] = []
        for entity in entities:
            geometry = entity.geometry
            if not isinstance(geometry, LineGeometry):
                continue
            dx = abs(geometry.end.x - geometry.start.x)
            dy = abs(geometry.end.y - geometry.start.y)
            x = (geometry.start.x + geometry.end.x) / 2.0
            if (
                dx <= width * 1e-5
                and dy >= height * 0.75
                and sx0 + width * 0.65 <= x <= sx1 - width * 0.05
            ):
                candidates.append(x)
        return min(candidates) if candidates else None

    def _is_border(self, entity: Entity, sheet: Sheet) -> bool:
        geometry = entity.geometry
        if not isinstance(geometry, PolylineGeometry) or not geometry.closed:
            return False
        bbox = _polyline_bbox(geometry)
        sx0, sy0, sx1, sy1 = sheet.bbox
        width, height = sx1 - sx0, sy1 - sy0
        bx0, by0, bx1, by1 = bbox
        area_ratio = ((bx1 - bx0) * (by1 - by0)) / max(width * height, 1e-12)
        margin_x = width * self.config.border_margin_ratio
        margin_y = height * self.config.border_margin_ratio
        near_edges = (
            abs(bx0 - sx0) <= margin_x and abs(bx1 - sx1) <= margin_x and
            abs(by0 - sy0) <= margin_y and abs(by1 - sy1) <= margin_y
        )
        return near_edges and area_ratio >= self.config.border_minimum_area_ratio

    def _in_title_region(self, entity: Entity, sheet: Sheet) -> bool:
        geometry = entity.geometry
        if not isinstance(geometry, PolylineGeometry) or not geometry.closed:
            return False
        x0, y0, x1, y1 = _polyline_bbox(geometry)
        sx0, sy0, sx1, sy1 = sheet.bbox
        width, height = sx1 - sx0, sy1 - sy0
        center_x, center_y = (x0 + x1) / 2, (y0 + y1) / 2
        return center_x >= sx1 - width * self.config.title_block_right_ratio and center_y <= sy0 + height * self.config.title_block_bottom_ratio


def _polyline_bbox(geometry: PolylineGeometry) -> tuple[float, float, float, float]:
    xs = [point.x for point in geometry.points]
    ys = [point.y for point in geometry.points]
    return min(xs), min(ys), max(xs), max(ys)


def _entity_center(entity: Entity) -> tuple[float, float]:
    geometry = entity.geometry
    if isinstance(geometry, LineGeometry):
        return (
            (geometry.start.x + geometry.end.x) / 2,
            (geometry.start.y + geometry.end.y) / 2,
        )
    if isinstance(geometry, (CircleGeometry, ArcGeometry)):
        return geometry.center.x, geometry.center.y
    if isinstance(geometry, PolylineGeometry):
        xs = [point.x for point in geometry.points]
        ys = [point.y for point in geometry.points]
    elif isinstance(geometry, BezierGeometry):
        xs = [point.x for point in geometry.control_points]
        ys = [point.y for point in geometry.control_points]
    elif isinstance(geometry, TextGeometry):
        return geometry.insertion.x, geometry.insertion.y
    else:
        return 0.0, 0.0
    return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
