from __future__ import annotations

from collections import defaultdict
from math import hypot
import re

from ..ir.drawing import Drawing
from ..ir.entities import (
    CircleGeometry, DimensionGeometry, Entity, LineGeometry, SemanticType,
    TextGeometry,
)
from ..geometry import Point


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

        dimension_texts = self._associate_dimension_texts(
            drawing, groups, by_id, page_scales
        )
        promoted, unresolved_reasons = self._promote_native_dimensions(
            drawing, groups, by_id
        )

        drawing.metadata["dimension_analysis"] = {
            "native_dimension_entities": promoted,
            "unresolved_graphic_groups": len(groups) - promoted,
            "unresolved_reasons": unresolved_reasons,
            "marker_entities": len(marker_groups),
            "dimension_line_entities": len(dimension_lines),
            "extension_line_entities": len(extension_lines),
            "dimension_text_entities": dimension_texts,
        }
        return drawing

    @staticmethod
    def _promote_native_dimensions(
        drawing: Drawing,
        groups: list[tuple[str, str, str, str]],
        by_id: dict[str, Entity],
    ) -> tuple[int, dict[str, str]]:
        """Promote only complete, unambiguous, 1:1 linear dimensions.

        PDF coordinates describe paper space. A printed value must not become
        native DIMENSION geometry until it agrees with the recovered
        definition-point distance. Scaled views remain graphical dimensions
        until view-scale inference is available.
        """
        promoted = 0
        unresolved: dict[str, str] = {}
        additions: list[Entity] = []
        for group_id, first_id, second_id, axis in groups:
            members = [
                entity for entity in drawing.entities
                if group_id in entity.metadata.get("dimension_graphics", [])
            ]
            texts = [
                entity for entity in members
                if entity.semantic_type == SemanticType.DIMENSION_TEXT
            ]
            if len(texts) != 1:
                unresolved[group_id] = "dimension_text_not_single"
                continue
            value = texts[0].metadata.get("parsed_dimension_value")
            if not isinstance(value, (int, float)) or value <= 0:
                unresolved[group_id] = "dimension_text_not_numeric"
                continue
            if any(
                entity.metadata.get("dimension_graphics") != [group_id]
                for entity in members
            ):
                unresolved[group_id] = "shared_dimension_graphics"
                continue

            definition_points: list[Point] = []
            for marker_id in (first_id, second_id):
                candidates = _definition_points(by_id[marker_id], members)
                if len(candidates) != 1:
                    unresolved[group_id] = "definition_points_incomplete"
                    break
                definition_points.append(candidates[0])
            if len(definition_points) != 2:
                continue

            measured = (
                abs(definition_points[1].x - definition_points[0].x)
                if axis == "H"
                else abs(definition_points[1].y - definition_points[0].y)
            )
            tolerance = max(0.1, float(value) * 0.01)
            if abs(measured - float(value)) > tolerance:
                unresolved[group_id] = "paper_measurement_differs_from_text"
                continue

            first_marker = by_id[first_id].geometry
            assert isinstance(first_marker, CircleGeometry)
            references = tuple(entity.id for entity in members)
            dimension_id = f"NATIVE_{group_id}"
            additions.append(
                Entity(
                    dimension_id, "dimension",
                    DimensionGeometry(
                        "linear", float(value), references,
                        orientation="horizontal" if axis == "H" else "vertical",
                        first_point=definition_points[0],
                        second_point=definition_points[1],
                        dimension_line_point=first_marker.center,
                        angle=0.0 if axis == "H" else 90.0,
                    ),
                    SemanticType.DIMENSION_LINE,
                    confidence=min(entity.confidence for entity in members),
                    page=by_id[first_id].page,
                    metadata={
                        "semantic_evidence": "resolved_linear_dimension",
                        "dimension_graphic": group_id,
                        "dimension_text_entity": texts[0].id,
                        "paper_measurement": measured,
                    },
                )
            )
            for entity in members:
                entity.metadata["resolved_dimension"] = dimension_id
                entity.metadata["suppress_cad_export"] = True
            promoted += 1
        drawing.entities.extend(additions)
        return promoted, unresolved

    @staticmethod
    def _associate_dimension_texts(
        drawing: Drawing,
        groups: list[tuple[str, str, str, str]],
        by_id: dict[str, Entity],
        page_scales: dict[int, float],
    ) -> int:
        segments: list[tuple[str, int, tuple[float, float], tuple[float, float]]] = []
        for group_id, first_id, second_id, _axis in groups:
            first_geometry = by_id[first_id].geometry
            second_geometry = by_id[second_id].geometry
            assert isinstance(first_geometry, CircleGeometry)
            assert isinstance(second_geometry, CircleGeometry)
            segments.append(
                (
                    group_id, by_id[first_id].page,
                    (first_geometry.center.x, first_geometry.center.y),
                    (second_geometry.center.x, second_geometry.center.y),
                )
            )

        count = 0
        for entity in drawing.entities:
            geometry = entity.geometry
            if (
                entity.semantic_type != SemanticType.TEXT
                or not isinstance(geometry, TextGeometry)
                or not _looks_like_dimension_text(geometry.text)
            ):
                continue
            center = (
                geometry.insertion.x + (geometry.width or geometry.height) / 2.0,
                geometry.insertion.y + geometry.height / 2.0,
            )
            nearby = [
                (
                    _point_segment_distance(center, start, end), group_id
                )
                for group_id, page, start, end in segments
                if page == entity.page
            ]
            if not nearby:
                continue
            distance, group_id = min(nearby)
            maximum_distance = page_scales.get(entity.page, 1.0) * 0.02
            if distance > maximum_distance:
                continue
            entity.semantic_type = SemanticType.DIMENSION_TEXT
            entity.metadata["semantic_evidence"] = "text_near_dimension_graphic"
            entity.metadata["dimension_graphics"] = [group_id]
            value = _numeric_dimension_value(geometry.text)
            if value is not None:
                entity.metadata["parsed_dimension_value"] = value
            count += 1
        return count

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


def _definition_points(marker: Entity, members: list[Entity]) -> list[Point]:
    marker_geometry = marker.geometry
    assert isinstance(marker_geometry, CircleGeometry)
    points: list[Point] = []
    for entity in members:
        if entity.semantic_type != SemanticType.DIMENSION_EXTENSION_LINE:
            continue
        geometry = entity.geometry
        assert isinstance(geometry, LineGeometry)
        start_distance = hypot(
            geometry.start.x - marker_geometry.center.x,
            geometry.start.y - marker_geometry.center.y,
        )
        end_distance = hypot(
            geometry.end.x - marker_geometry.center.x,
            geometry.end.y - marker_geometry.center.y,
        )
        if min(start_distance, end_distance) > marker_geometry.radius * 1.25:
            continue
        points.append(geometry.end if start_distance < end_distance else geometry.start)
    return points


def _looks_like_dimension_text(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    return bool(
        re.fullmatch(
            r"[\(\[（]?[φΦØ⌀□]?[0-9]+(?:[.,][0-9]+)?[\)\]）]?",
            compact,
        )
        or re.fullmatch(r"[A-Za-z][0-9]+", compact)
        or re.fullmatch(r"[0-9]+(?:[.,][0-9]+)?[xX×][0-9]+(?:[.,][0-9]+)?", compact)
    )


def _numeric_dimension_value(text: str) -> float | None:
    compact = re.sub(r"\s+", "", text)
    if not re.fullmatch(
        r"[\(\[（]?[φΦØ⌀□]?[0-9]+(?:[.,][0-9]+)?[\)\]）]?",
        compact,
    ):
        return None
    compact = re.sub(r"[^0-9.,]", "", compact).replace(",", ".")
    try:
        return float(compact)
    except ValueError:
        return None


def _point_segment_distance(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    px, py = point
    ax, ay = start
    bx, by = end
    dx, dy = bx - ax, by - ay
    length_squared = dx * dx + dy * dy
    if length_squared <= 1e-15:
        return hypot(px - ax, py - ay)
    position = max(
        0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_squared)
    )
    return hypot(px - (ax + position * dx), py - (ay + position * dy))
