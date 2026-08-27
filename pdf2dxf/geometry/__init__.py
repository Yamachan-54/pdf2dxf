from __future__ import annotations

from dataclasses import dataclass
from math import hypot


@dataclass(frozen=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True)
class Segment:
    start: Point
    end: Point


def cubic_bezier(
    p0: Point, p1: Point, p2: Point, p3: Point, steps: int
) -> list[Segment]:
    points = sample_cubic_bezier(p0, p1, p2, p3, steps)
    return [Segment(a, b) for a, b in zip(points, points[1:])]


def sample_cubic_bezier(
    p0: Point, p1: Point, p2: Point, p3: Point, steps: int
) -> list[Point]:
    points: list[Point] = []
    for index in range(steps + 1):
        t = index / steps
        u = 1.0 - t
        points.append(
            Point(
                u**3 * p0.x + 3 * u * u * t * p1.x + 3 * u * t * t * p2.x + t**3 * p3.x,
                u**3 * p0.y + 3 * u * u * t * p1.y + 3 * u * t * t * p2.y + t**3 * p3.y,
            )
        )
    return points


def suggested_curve_steps(points: tuple[Point, Point, Point, Point]) -> int:
    length = sum(hypot(b.x - a.x, b.y - a.y) for a, b in zip(points, points[1:]))
    return max(8, min(128, int(length / 6) + 1))


__all__ = ["Point", "Segment", "cubic_bezier", "sample_cubic_bezier", "suggested_curve_steps"]
