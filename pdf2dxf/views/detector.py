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
            if len(candidates) > 500:
                region = sheet.metadata.get("drawing_area_bbox", sheet.bbox)
                groups = self._dense_drawing_components(
                    candidates, tuple(float(value) for value in region)
                )
            else:
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
    def _dense_drawing_components(
        entities: list[Entity], sheet_bbox: tuple[float, float, float, float]
    ) -> list[list[Entity]]:
        """Use major geometry as view anchors in outline-text-heavy PDFs."""
        sx0, sy0, sx1, sy1 = sheet_bbox
        width, height = sx1 - sx0, sy1 - sy0
        scale = min(width, height)
        horizontal_margin = scale * 0.06
        bottom_margin = scale * 0.25
        top_margin = scale * 0.09
        anchors = []
        for entity in entities:
            bbox = _entity_bbox(entity)
            center = _bbox_center(bbox)
            extent = ((bbox[2] - bbox[0]) ** 2 + (bbox[3] - bbox[1]) ** 2) ** 0.5
            if (
                extent >= scale * 0.03
                and sx0 + horizontal_margin <= center[0] <= sx1 - horizontal_margin
                and sy0 + bottom_margin <= center[1] <= sy1 - top_margin
            ):
                anchors.append(entity)
        if len(anchors) < 20:
            return ViewDetector._grid_components(entities, max(scale * 0.06, 1e-9))

        anchor_groups = ViewDetector._xy_cut(
            anchors, width, height, scale * 0.045, depth=0
        )
        if len(anchor_groups) <= 1:
            return ViewDetector._grid_components(entities, max(scale * 0.06, 1e-9))

        cores = [
            _merged_bbox([_center_bbox(entity) for entity in group])
            for group in anchor_groups
        ]
        assigned: list[list[Entity]] = [[] for _group in anchor_groups]
        assignment_distance = scale * 0.03
        for entity in entities:
            center = _bbox_center(_entity_bbox(entity))
            distances = [_point_box_distance(center, core) for core in cores]
            nearest = min(range(len(distances)), key=distances.__getitem__)
            if distances[nearest] <= assignment_distance:
                assigned[nearest].append(entity)
        return [group for group in assigned if group]

    @staticmethod
    def _xy_cut(
        entities: list[Entity], sheet_width: float, sheet_height: float,
        minimum_gap: float, *, depth: int,
    ) -> list[list[Entity]]:
        if depth >= 2 or len(entities) < 20:
            return [entities]
        centers = [(_bbox_center(_entity_bbox(entity)), entity) for entity in entities]
        choices: list[tuple[float, str, float, float]] = []
        for axis, dimension in ((0, sheet_width), (1, sheet_height)):
            values = sorted({round(center[axis], 6) for center, _entity in centers})
            if len(values) < 2:
                continue
            for index in range(len(values) - 1):
                split = (values[index + 1] + values[index]) / 2
                left_count = sum(center[axis] <= split for center, _entity in centers)
                right_count = len(centers) - left_count
                if min(left_count, right_count) < 10:
                    continue
                gap = values[index + 1] - values[index]
                balance = min(left_count, right_count) / len(centers)
                choices.append(
                    (
                        gap / max(dimension, 1e-12) * balance ** 0.5,
                        "x" if axis == 0 else "y", split, gap,
                    )
                )
        if not choices:
            return [entities]
        preferred_axis = "y" if depth == 0 else "x"
        preferred = [
            choice for choice in choices
            if choice[1] == preferred_axis
            and choice[3] >= minimum_gap
        ]
        _score, axis, split, gap = max(preferred or choices)
        axis_index = 0 if axis == "x" else 1
        if gap < minimum_gap:
            return [entities]
        left = [entity for center, entity in centers if center[axis_index] <= split]
        right = [entity for center, entity in centers if center[axis_index] > split]
        return (
            ViewDetector._xy_cut(
                left, sheet_width, sheet_height, minimum_gap, depth=depth + 1
            )
            + ViewDetector._xy_cut(
                right, sheet_width, sheet_height, minimum_gap, depth=depth + 1
            )
        )

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


def _bbox_center(
    bbox: tuple[float, float, float, float]
) -> tuple[float, float]:
    return (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2


def _center_bbox(entity: Entity) -> tuple[float, float, float, float]:
    x, y = _bbox_center(_entity_bbox(entity))
    return x, y, x, y


def _point_box_distance(
    point: tuple[float, float], bbox: tuple[float, float, float, float]
) -> float:
    x, y = point
    dx = max(bbox[0] - x, 0.0, x - bbox[2])
    dy = max(bbox[1] - y, 0.0, y - bbox[3])
    return (dx * dx + dy * dy) ** 0.5
