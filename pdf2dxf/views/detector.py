from __future__ import annotations

from ..geometry import Point
from ..ir.drawing import Drawing
from ..ir.entities import (
    ArcGeometry, BezierGeometry, CircleGeometry, DimensionGeometry, Entity,
    LineGeometry, PolylineGeometry, SemanticType, TextGeometry,
)
from ..ir.views import View, ViewType


_NON_VIEW = {
    SemanticType.SHEET_BORDER,
    SemanticType.TITLE_BLOCK_LINE,
    SemanticType.REVISION_TABLE_LINE,
    SemanticType.TEXT,
    SemanticType.DIMENSION_TEXT,
}


class ViewDetector:
    """Spatially separates geometry; projection type intentionally stays UNKNOWN."""

    def detect(self, drawing: Drawing) -> Drawing:
        drawing.views = []
        for sheet in drawing.sheets:
            candidates = [
                entity for entity in drawing.entities
                if entity.page == sheet.page and entity.semantic_type not in _NON_VIEW
            ]
            if not candidates:
                continue
            sheet_width = sheet.bbox[2] - sheet.bbox[0]
            sheet_height = sheet.bbox[3] - sheet.bbox[1]
            padding = min(sheet_width, sheet_height) * 0.015
            groups = self._components(candidates, padding)
            for group in groups:
                view_id = f"VIEW_{len(drawing.views) + 1:03d}"
                bbox = _merged_bbox([_entity_bbox(entity) for entity in group])
                view = View(view_id, ViewType.UNKNOWN, bbox, 0.5, sheet.page, tuple(entity.id for entity in group))
                drawing.views.append(view)
                for entity in group:
                    entity.view = view_id
        return drawing

    @staticmethod
    def _components(entities: list[Entity], padding: float) -> list[list[Entity]]:
        if len(entities) > 2000:
            return ViewDetector._grid_components(entities, max(padding * 4.0, 1e-9))
        boxes = [_entity_bbox(entity) for entity in entities]
        parents = list(range(len(entities)))

        def root(index: int) -> int:
            while parents[index] != index:
                parents[index] = parents[parents[index]]
                index = parents[index]
            return index

        def union(first: int, second: int) -> None:
            left, right = root(first), root(second)
            if left != right:
                parents[right] = left

        for first in range(len(entities)):
            for second in range(first + 1, len(entities)):
                if _boxes_touch(boxes[first], boxes[second], padding):
                    union(first, second)
        grouped: dict[int, list[Entity]] = {}
        for index, entity in enumerate(entities):
            grouped.setdefault(root(index), []).append(entity)
        return list(grouped.values())

    @staticmethod
    def _grid_components(entities: list[Entity], cell_size: float) -> list[list[Entity]]:
        """Linear-size coarse clustering for dense engineering drawings."""
        cells: dict[tuple[int, int], list[Entity]] = {}
        for entity in entities:
            x0, y0, x1, y1 = _entity_bbox(entity)
            cell = (int(((x0 + x1) / 2) // cell_size), int(((y0 + y1) / 2) // cell_size))
            cells.setdefault(cell, []).append(entity)
        parents = {cell: cell for cell in cells}

        def root(cell: tuple[int, int]) -> tuple[int, int]:
            while parents[cell] != cell:
                parents[cell] = parents[parents[cell]]
                cell = parents[cell]
            return cell

        def union(first: tuple[int, int], second: tuple[int, int]) -> None:
            left, right = root(first), root(second)
            if left != right:
                parents[right] = left

        for x, y in cells:
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    neighbor = (x + dx, y + dy)
                    if neighbor in cells:
                        union((x, y), neighbor)
        grouped: dict[tuple[int, int], list[Entity]] = {}
        for cell, members in cells.items():
            grouped.setdefault(root(cell), []).extend(members)
        return list(grouped.values())


def _entity_bbox(entity: Entity) -> tuple[float, float, float, float]:
    geometry = entity.geometry
    points: list[Point]
    if isinstance(geometry, LineGeometry):
        points = [geometry.start, geometry.end]
    elif isinstance(geometry, CircleGeometry):
        return (geometry.center.x - geometry.radius, geometry.center.y - geometry.radius, geometry.center.x + geometry.radius, geometry.center.y + geometry.radius)
    elif isinstance(geometry, ArcGeometry):
        return (geometry.center.x - geometry.radius, geometry.center.y - geometry.radius, geometry.center.x + geometry.radius, geometry.center.y + geometry.radius)
    elif isinstance(geometry, PolylineGeometry):
        points = list(geometry.points)
    elif isinstance(geometry, BezierGeometry):
        points = list(geometry.control_points)
    elif isinstance(geometry, TextGeometry):
        width = geometry.width or geometry.height
        return (geometry.insertion.x, geometry.insertion.y, geometry.insertion.x + width, geometry.insertion.y + geometry.height)
    elif isinstance(geometry, DimensionGeometry):
        return (0.0, 0.0, 0.0, 0.0)
    else:
        return (0.0, 0.0, 0.0, 0.0)
    xs, ys = [point.x for point in points], [point.y for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def _boxes_touch(first: tuple[float, float, float, float], second: tuple[float, float, float, float], padding: float) -> bool:
    return not (
        first[2] + padding < second[0] or second[2] + padding < first[0] or
        first[3] + padding < second[1] or second[3] + padding < first[1]
    )


def _merged_bbox(boxes: list[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    return min(box[0] for box in boxes), min(box[1] for box in boxes), max(box[2] for box in boxes), max(box[3] for box in boxes)
