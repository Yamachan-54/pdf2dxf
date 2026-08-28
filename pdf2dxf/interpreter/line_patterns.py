from __future__ import annotations

from collections import defaultdict
from math import hypot, sqrt

from ..ir.drawing import Drawing
from ..ir.entities import Entity, LineGeometry, SemanticType


def detect_segmented_centerlines(drawing: Drawing) -> list[list[Entity]]:
    """Find explicit long-dot chains when the PDF has expanded a dash pattern.

    Some PDF producers serialize a centerline as independent solid paths.  This
    detector is intentionally conservative: it accepts only axis-aligned,
    evenly-spaced, strictly alternating long/short chains of useful drawing
    scale.  It returns the source line for each visible dash rather than
    replacing geometry, preserving Drawing IR evidence.
    """
    sheet_sizes = {
        sheet.page: min(
            sheet.bbox[2] - sheet.bbox[0], sheet.bbox[3] - sheet.bbox[1]
        )
        for sheet in drawing.sheets
    }
    groups: dict[tuple[int, str, int], list[tuple[float, float, Entity]]] = (
        defaultdict(list)
    )
    for entity in drawing.entities:
        geometry = entity.geometry
        if (
            entity.semantic_type != SemanticType.UNKNOWN
            or not isinstance(geometry, LineGeometry)
            or (entity.style.dash_pattern or "").strip() not in {"", "[] 0", "[]0"}
        ):
            continue
        dx = geometry.end.x - geometry.start.x
        dy = geometry.end.y - geometry.start.y
        length = hypot(dx, dy)
        if length <= 1e-12:
            continue
        coordinate_tolerance = max(sheet_sizes.get(entity.page, 1.0) * 1e-6, 1e-6)
        if abs(dy) <= max(length * 1e-6, coordinate_tolerance):
            coordinate = (geometry.start.y + geometry.end.y) / 2.0
            groups[(entity.page, "H", round(coordinate / coordinate_tolerance))].append(
                (min(geometry.start.x, geometry.end.x), max(geometry.start.x, geometry.end.x), entity)
            )
        elif abs(dx) <= max(length * 1e-6, coordinate_tolerance):
            coordinate = (geometry.start.x + geometry.end.x) / 2.0
            groups[(entity.page, "V", round(coordinate / coordinate_tolerance))].append(
                (min(geometry.start.y, geometry.end.y), max(geometry.start.y, geometry.end.y), entity)
            )

    detected: list[list[Entity]] = []
    for (page, _axis, _coordinate), intervals in groups.items():
        scale = sheet_sizes.get(page, 1.0)
        tolerance = max(scale * 1e-6, 1e-6)
        merged = _merge_intervals(intervals, tolerance)
        for sequence in _split_at_large_gaps(merged, scale * 0.02):
            entities = _alternating_pattern_entities(sequence, scale, tolerance)
            if entities:
                detected.append(entities)
    return detected


def _merge_intervals(
    intervals: list[tuple[float, float, Entity]], tolerance: float
) -> list[tuple[float, float, list[Entity]]]:
    merged: list[tuple[float, float, list[Entity]]] = []
    for start, end, entity in sorted(intervals, key=lambda item: item[0]):
        if merged and start <= merged[-1][1] + tolerance:
            previous_start, previous_end, members = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end), members + [entity])
        else:
            merged.append((start, end, [entity]))
    return merged


def _split_at_large_gaps(
    intervals: list[tuple[float, float, list[Entity]]], maximum_gap: float
) -> list[list[tuple[float, float, list[Entity]]]]:
    sequences: list[list[tuple[float, float, list[Entity]]]] = []
    for interval in intervals:
        if not sequences or interval[0] - sequences[-1][-1][1] > maximum_gap:
            sequences.append([interval])
        else:
            sequences[-1].append(interval)
    return sequences


def _alternating_pattern_entities(
    intervals: list[tuple[float, float, list[Entity]]],
    scale: float,
    tolerance: float,
) -> list[Entity]:
    if not 5 <= len(intervals) <= 100:
        return []
    lengths = [end - start for start, end, _members in intervals]
    span = intervals[-1][1] - intervals[0][0]
    if span < scale * 0.05:
        return []
    ordered = sorted(lengths)
    ratios = [
        ordered[index + 1] / max(ordered[index], tolerance)
        for index in range(len(ordered) - 1)
    ]
    split_index = max(range(len(ratios)), key=ratios.__getitem__)
    if ratios[split_index] < 4.0:
        return []
    threshold = sqrt(ordered[split_index] * ordered[split_index + 1])
    kinds = [length > threshold for length in lengths]
    if not all(kinds[index] != kinds[index + 1] for index in range(len(kinds) - 1)):
        return []
    long_lengths = [length for length, is_long in zip(lengths, kinds) if is_long]
    short_lengths = [length for length, is_long in zip(lengths, kinds) if not is_long]
    if len(long_lengths) < 3 or len(short_lengths) < 2:
        return []
    if sorted(long_lengths)[len(long_lengths) // 2] < scale * 0.01:
        return []
    if max(short_lengths) > scale * 0.01:
        return []
    gaps = [
        intervals[index + 1][0] - intervals[index][1]
        for index in range(len(intervals) - 1)
    ]
    if min(gaps) <= tolerance or max(gaps) / min(gaps) > 1.5:
        return []

    # Overlapping non-pattern geometry can share the same coordinate.  Keep only
    # the longest source line in each visible dash interval.
    return [
        max(
            members,
            key=lambda entity: hypot(
                entity.geometry.end.x - entity.geometry.start.x,
                entity.geometry.end.y - entity.geometry.start.y,
            ),
        )
        for _start, _end, members in intervals
    ]
