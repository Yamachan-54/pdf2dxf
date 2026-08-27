from __future__ import annotations

from ..config import SheetAnalysisConfig
from ..ir.drawing import Drawing, Sheet
from ..ir.entities import Entity, PolylineGeometry, SemanticType


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
            title_ids: list[str] = []
            if len(title_candidates) >= self.config.title_block_minimum_cells:
                title_ids = [entity.id for entity in title_candidates]
                for entity in title_candidates:
                    entity.semantic_type = SemanticType.TITLE_BLOCK_LINE
                    entity.confidence = 0.7
            sheet.border_entity_ids = tuple(border_ids)
            sheet.title_block_entity_ids = tuple(title_ids)
        return drawing

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
