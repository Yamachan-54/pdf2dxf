from __future__ import annotations

from dataclasses import replace
from math import degrees, hypot

from ..config import ReconstructionConfig
from ..ir.drawing import Drawing
from ..ir.entities import (
    ArcGeometry,
    BezierGeometry,
    CircleGeometry,
    Entity,
    PolylineGeometry,
    SemanticType,
)
from . import Point, sample_cubic_bezier, suggested_curve_steps
from .fitting import angle, fit_circle, signed_turn


class PrimitiveReconstructor:
    def __init__(self, config: ReconstructionConfig | None = None) -> None:
        self.config = config or ReconstructionConfig()

    def reconstruct(self, drawing: Drawing) -> Drawing:
        output: list[Entity] = []
        entities = drawing.entities
        index = 0
        while index < len(entities):
            entity = entities[index]
            if not isinstance(entity.geometry, BezierGeometry):
                output.append(entity)
                index += 1
                continue
            chain = [entity]
            cursor = index + 1
            while cursor < len(entities):
                candidate = entities[cursor]
                if not isinstance(candidate.geometry, BezierGeometry):
                    break
                if candidate.geometry.path_id != entity.geometry.path_id or candidate.page != entity.page:
                    break
                if not self._near(chain[-1].geometry.control_points[-1], candidate.geometry.control_points[0]):
                    break
                chain.append(candidate)
                cursor += 1
            output.append(self._reconstruct_chain(chain))
            index = cursor
        output = self._reconstruct_line_chains(output)
        output = self._deduplicate_lines(output)
        drawing.entities = self._merge_collinear_lines(output)
        return drawing

    @staticmethod
    def _deduplicate_lines(entities: list[Entity]) -> list[Entity]:
        from ..ir.entities import LineGeometry

        result: list[Entity] = []
        seen: set[tuple[object, ...]] = set()
        for entity in entities:
            geometry = entity.geometry
            if not isinstance(geometry, LineGeometry):
                result.append(entity)
                continue
            first = (round(geometry.start.x, 8), round(geometry.start.y, 8))
            second = (round(geometry.end.x, 8), round(geometry.end.y, 8))
            key = (
                entity.page,
                entity.metadata.get("path_id"),
                entity.style,
                *sorted((first, second)),
            )
            if key not in seen:
                seen.add(key)
                result.append(entity)
        return result

    def _reconstruct_line_chains(self, entities: list[Entity]) -> list[Entity]:
        from ..ir.entities import LineGeometry

        output: list[Entity] = []
        index = 0
        while index < len(entities):
            first = entities[index]
            if not isinstance(first.geometry, LineGeometry):
                output.append(first)
                index += 1
                continue
            chain = [first]
            cursor = index + 1
            while cursor < len(entities):
                candidate = entities[cursor]
                if not isinstance(candidate.geometry, LineGeometry):
                    break
                if candidate.page != first.page or candidate.style != first.style:
                    break
                if candidate.metadata.get("path_id") != first.metadata.get("path_id"):
                    break
                if not self._near(chain[-1].geometry.end, candidate.geometry.start):
                    break
                chain.append(candidate)
                cursor += 1
            rebuilt = self._fit_line_chain(chain)
            output.extend(rebuilt)
            index = cursor
        return output

    def _fit_line_chain(self, chain: list[Entity]) -> list[Entity]:
        from ..ir.entities import LineGeometry

        if len(chain) < 4:
            return chain
        geometries = [entity.geometry for entity in chain]
        assert all(isinstance(geometry, LineGeometry) for geometry in geometries)
        points = [geometries[0].start] + [geometry.end for geometry in geometries]  # type: ignore[union-attr]
        closed = self._near(points[0], points[-1])
        fit_points = points[:-1] if closed else points
        fit = fit_circle(fit_points)
        if fit is None or fit.radius <= 0:
            return chain
        tolerance = max(
            self.config.circle_absolute_error if closed else self.config.arc_absolute_error,
            fit.radius * (self.config.circle_relative_error if closed else self.config.arc_relative_error),
        )
        if fit.max_error > tolerance:
            return chain
        if closed:
            xs, ys = [point.x for point in fit_points], [point.y for point in fit_points]
            width, height = max(xs) - min(xs), max(ys) - min(ys)
            aspect_error = abs(width - height) / max(width, height, 1e-12)
            if aspect_error > self.config.circle_aspect_tolerance:
                return chain
            return [
                replace(
                    chain[0], primitive="circle", geometry=CircleGeometry(fit.center, fit.radius),
                    semantic_type=SemanticType.HOLE,
                    confidence=max(0.0, 1.0 - fit.max_error / max(tolerance, 1e-12)),
                    metadata={**chain[0].metadata, "reconstruction": "circle_from_lines", "source_entities": [item.id for item in chain]},
                )
            ]
        turn = signed_turn(fit.center, points)
        sweep = abs(degrees(turn))
        if self.config.minimum_arc_sweep_degrees <= sweep < 359.0:
            start, end = points[0], points[-1]
            if turn < 0:
                start, end = end, start
            return [
                replace(
                    chain[0], primitive="arc", geometry=ArcGeometry(fit.center, fit.radius, angle(fit.center, start), angle(fit.center, end)),
                    semantic_type=SemanticType.OUTER_CONTOUR,
                    confidence=max(0.0, 1.0 - fit.max_error / max(tolerance, 1e-12)),
                    metadata={**chain[0].metadata, "reconstruction": "arc_from_lines", "source_entities": [item.id for item in chain]},
                )
            ]
        return chain

    def _merge_collinear_lines(self, entities: list[Entity]) -> list[Entity]:
        from ..ir.entities import LineGeometry

        merged: list[Entity] = []
        for entity in entities:
            if merged and self._can_merge_lines(merged[-1], entity):
                previous = merged[-1]
                assert isinstance(previous.geometry, LineGeometry)
                assert isinstance(entity.geometry, LineGeometry)
                merged[-1] = replace(
                    previous,
                    geometry=LineGeometry(previous.geometry.start, entity.geometry.end),
                    metadata={
                        **previous.metadata,
                        "reconstruction": "merged_line",
                        "source_entities": [previous.id, entity.id],
                    },
                )
            else:
                merged.append(entity)
        return merged

    def _can_merge_lines(self, first: Entity, second: Entity) -> bool:
        from ..ir.entities import LineGeometry

        if not isinstance(first.geometry, LineGeometry) or not isinstance(second.geometry, LineGeometry):
            return False
        if first.page != second.page or first.style != second.style:
            return False
        if first.metadata.get("path_id") != second.metadata.get("path_id"):
            return False
        if not self._near(first.geometry.end, second.geometry.start):
            return False
        ax = first.geometry.end.x - first.geometry.start.x
        ay = first.geometry.end.y - first.geometry.start.y
        bx = second.geometry.end.x - second.geometry.start.x
        by = second.geometry.end.y - second.geometry.start.y
        length_product = hypot(ax, ay) * hypot(bx, by)
        if length_product <= 1e-15:
            return False
        cross_ratio = abs(ax * by - ay * bx) / length_product
        same_direction = ax * bx + ay * by > 0
        return same_direction and cross_ratio <= self.config.line_collinearity_tolerance

    def _reconstruct_chain(self, chain: list[Entity]) -> Entity:
        sampled: list[Point] = []
        for entity in chain:
            geometry = entity.geometry
            assert isinstance(geometry, BezierGeometry)
            steps = self.config.default_curve_steps or suggested_curve_steps(geometry.control_points)
            points = sample_cubic_bezier(*geometry.control_points, steps)
            sampled.extend(points if not sampled else points[1:])
        first, last = sampled[0], sampled[-1]
        closed = self._near(first, last)
        fit_points = sampled[:-1] if closed else sampled
        fit = fit_circle(fit_points)
        if fit is not None and fit.radius > 0:
            tolerance = max(
                self.config.circle_absolute_error if closed else self.config.arc_absolute_error,
                fit.radius * (self.config.circle_relative_error if closed else self.config.arc_relative_error),
            )
            if closed:
                xs = [point.x for point in fit_points]
                ys = [point.y for point in fit_points]
                width, height = max(xs) - min(xs), max(ys) - min(ys)
                aspect_error = abs(width - height) / max(width, height, 1e-12)
                if fit.max_error <= tolerance and aspect_error <= self.config.circle_aspect_tolerance:
                    return replace(
                        chain[0], primitive="circle", geometry=CircleGeometry(fit.center, fit.radius),
                        semantic_type=SemanticType.HOLE, confidence=max(0.0, 1.0 - fit.max_error / max(tolerance, 1e-12)),
                        metadata={**chain[0].metadata, "reconstruction": "circle", "source_entities": [item.id for item in chain]},
                    )
            else:
                turn = signed_turn(fit.center, sampled)
                sweep = abs(degrees(turn))
                if fit.max_error <= tolerance and self.config.minimum_arc_sweep_degrees <= sweep < 359.0:
                    start, end = sampled[0], sampled[-1]
                    if turn < 0:
                        start, end = end, start
                    return replace(
                        chain[0], primitive="arc",
                        geometry=ArcGeometry(fit.center, fit.radius, angle(fit.center, start), angle(fit.center, end)),
                        semantic_type=SemanticType.OUTER_CONTOUR,
                        confidence=max(0.0, 1.0 - fit.max_error / max(tolerance, 1e-12)),
                        metadata={**chain[0].metadata, "reconstruction": "arc", "source_entities": [item.id for item in chain]},
                    )
        return replace(
            chain[0], primitive="polyline", geometry=PolylineGeometry(tuple(sampled), closed),
            semantic_type=SemanticType.OUTER_CONTOUR, confidence=0.5,
            metadata={**chain[0].metadata, "reconstruction": "polyline_fallback", "source_entities": [item.id for item in chain]},
        )

    def _near(self, first: Point, second: Point) -> bool:
        return hypot(first.x - second.x, first.y - second.y) <= self.config.endpoint_tolerance
