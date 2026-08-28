from __future__ import annotations

from collections import defaultdict
from math import hypot

from ..ir.drawing import Drawing
from ..ir.entities import CircleGeometry, Entity, LineGeometry, SemanticType


class DimensionInterpreter:
    def analyze(self, drawing: Drawing) -> Drawing:
        """Separate expanded dimension graphics from manufacturing geometry.

        This stage does not invent native DimensionGeometry without text/value
        evidence.  It recognizes paired dot markers and their linework so the
        unresolved graphic dimension is exported on DIMENSION instead of being
        mistaken for holes and machining lines.
        """
        page_scales = {
            sheet.page: min(
                sheet.bbox[2] - sheet.bbox[0],
                sheet.bbox[3] - sheet.bbox[1],
            )
            for sheet in drawing.sheets
        }
        markers = [
            entity for entity in drawing.entities
            if self._is_marker_candidate(entity, page_scales.get(entity.page, 1.0))
        ]
        axis_lines: dict[tuple[int, str], list[Entity]] = defaultdict(list)
        for entity in drawing.entities:
            axis = _line_axis(entity)
            if axis is not None:
                axis_lines[(entity.page, axis)].append(entity)

        marker_axes: dict[str, set[str]] = defaultdict(set)
        dimension_lines: dict[str, set[str]] = defaultdict(set)
        groups: list[tuple[str, str, str, str]] = []
        for first_index, first in enumerate(markers):
            for second in markers[first_index + 1:]:
                axis = _marker_pair_axis(first, second)
                if axis is None:
                    continue
                connecting = _connecting_lines(
                    axis_lines[(first.page, axis)], first, second
                )
                if not connecting:
                    continue
                group_id = f"DIMENSION_GRAPHIC_{len(groups) + 1:03d}"
                groups.append((group_id, first.id, second.id, axis))
                marker_axes[first.id].add(axis)
                marker_axes[second.id].add(axis)
                for line in connecting:
                    dimension_lines[line.id].add(group_id)

        by_id = {entity.id: entity for entity in drawing.entities}
        marker_groups: dict[str, set[str]] = defaultdict(set)
        for group_id, first_id, second_id, _axis in groups:
            marker_groups[first_id].add(group_id)
            marker_groups[second_id].add(group_id)
        for marker_id, group_ids in marker_groups.items():
            marker = by_id[marker_id]
            marker.semantic_type = SemanticType.DIMENSION_MARKER
            marker.confidence = 0.92
            marker.metadata["semantic_evidence"] = "paired_dimension_dot"
            marker.metadata["dimension_graphics"] = sorted(group_ids)

        for line_id, group_ids in dimension_lines.items():
            line = by_id[line_id]
            line.semantic_type = SemanticType.DIMENSION_LINE
            line.confidence = 0.88
            line.metadata["semantic_evidence"] = "between_dimension_markers"
            line.metadata["dimension_graphics"] = sorted(group_ids)

        extension_lines: dict[str, set[str]] = defaultdict(set)
        for marker_id, axes in marker_axes.items():
            marker = by_id[marker_id]
            for dimension_axis in axes:
                perpendicular = "V" if dimension_axis == "H" else "H"
                for line in axis_lines[(marker.page, perpendicular)]:
                    if line.id in dimension_lines:
                        continue
                    if _ends_at_marker(line, marker):
                        extension_lines[line.id].update(marker_groups[marker_id])
        for line_id, group_ids in extension_lines.items():
            line = by_id[line_id]
            line.semantic_type = SemanticType.DIMENSION_EXTENSION_LINE
            line.confidence = 0.84
            line.metadata["semantic_evidence"] = "perpendicular_to_dimension_marker"
            line.metadata["dimension_graphics"] = sorted(group_ids)

        drawing.metadata["dimension_analysis"] = {
            "unresolved_graphic_groups": len(groups),
            "marker_entities": len(marker_groups),
            "dimension_line_entities": len(dimension_lines),
            "extension_line_entities": len(extension_lines),
        }
        return drawing

    @staticmethod
    def _is_marker_candidate(entity: Entity, page_scale: float) -> bool:
        geometry = entity.geometry
        source_entities = entity.metadata.get("source_entities", [])
        return (
            isinstance(geometry, CircleGeometry)
            and entity.semantic_type == SemanticType.HOLE
            and entity.metadata.get("reconstruction") == "circle_from_lines"
            and isinstance(source_entities, list)
            and 8 <= len(source_entities) <= 32
            and geometry.radius <= page_scale * 0.0025
        )


def _line_axis(entity: Entity) -> str | None:
    geometry = entity.geometry
    if not isinstance(geometry, LineGeometry):
        return None
    dx = geometry.end.x - geometry.start.x
    dy = geometry.end.y - geometry.start.y
    length = hypot(dx, dy)
    if length <= 1e-12:
        return None
    tolerance = max(length * 1e-5, 1e-8)
    if abs(dy) <= tolerance:
        return "H"
    if abs(dx) <= tolerance:
        return "V"
    return None


def _marker_pair_axis(first: Entity, second: Entity) -> str | None:
    if first.page != second.page:
        return None
    first_geometry = first.geometry
    second_geometry = second.geometry
    assert isinstance(first_geometry, CircleGeometry)
    assert isinstance(second_geometry, CircleGeometry)
    maximum_radius = max(first_geometry.radius, second_geometry.radius)
    if abs(first_geometry.radius - second_geometry.radius) > maximum_radius * 0.20:
        return None
    dx = abs(second_geometry.center.x - first_geometry.center.x)
    dy = abs(second_geometry.center.y - first_geometry.center.y)
    alignment_tolerance = maximum_radius * 0.35
    minimum_separation = maximum_radius * 4.0
    if dy <= alignment_tolerance and dx >= minimum_separation:
        return "H"
    if dx <= alignment_tolerance and dy >= minimum_separation:
        return "V"
    return None


def _connecting_lines(
    lines: list[Entity], first: Entity, second: Entity
) -> list[Entity]:
    first_geometry = first.geometry
    second_geometry = second.geometry
    assert isinstance(first_geometry, CircleGeometry)
    assert isinstance(second_geometry, CircleGeometry)
    axis = _marker_pair_axis(first, second)
    if axis == "H":
        first_cross = first_geometry.center.y
        second_cross = second_geometry.center.y
        marker_start, marker_end = sorted(
            (first_geometry.center.x, second_geometry.center.x)
        )
    elif axis == "V":
        first_cross = first_geometry.center.x
        second_cross = second_geometry.center.x
        marker_start, marker_end = sorted(
            (first_geometry.center.y, second_geometry.center.y)
        )
    else:
        return []
    radius = max(first_geometry.radius, second_geometry.radius)
    candidates: list[tuple[float, float, Entity]] = []
    for line in lines:
        geometry = line.geometry
        assert isinstance(geometry, LineGeometry)
        if axis == "H":
            line_cross = (geometry.start.y + geometry.end.y) / 2.0
            line_start, line_end = sorted((geometry.start.x, geometry.end.x))
        else:
            line_cross = (geometry.start.x + geometry.end.x) / 2.0
            line_start, line_end = sorted((geometry.start.y, geometry.end.y))
        if max(
            abs(line_cross - first_cross), abs(line_cross - second_cross)
        ) <= radius * 0.40:
            candidates.append((line_start, line_end, line))

    endpoint_tolerance = radius * 2.5
    join_tolerance = radius * 0.15
    selected: list[Entity] = []
    coverage = marker_start - endpoint_tolerance
    for line_start, line_end, line in sorted(candidates, key=lambda item: item[0]):
        if line_end < marker_start - endpoint_tolerance:
            continue
        if not selected:
            if line_start > marker_start + endpoint_tolerance:
                continue
            selected.append(line)
            coverage = max(coverage, line_end)
        elif line_start <= coverage + join_tolerance:
            selected.append(line)
            coverage = max(coverage, line_end)
        else:
            break
        if coverage >= marker_end - endpoint_tolerance:
            return selected
    return []


def _ends_at_marker(line: Entity, marker: Entity) -> bool:
    geometry = line.geometry
    marker_geometry = marker.geometry
    assert isinstance(geometry, LineGeometry)
    assert isinstance(marker_geometry, CircleGeometry)
    length = hypot(
        geometry.end.x - geometry.start.x,
        geometry.end.y - geometry.start.y,
    )
    if length < marker_geometry.radius * 4.0:
        return False
    endpoint_distance = min(
        hypot(
            geometry.start.x - marker_geometry.center.x,
            geometry.start.y - marker_geometry.center.y,
        ),
        hypot(
            geometry.end.x - marker_geometry.center.x,
            geometry.end.y - marker_geometry.center.y,
        ),
    )
    return endpoint_distance <= marker_geometry.radius * 1.25
