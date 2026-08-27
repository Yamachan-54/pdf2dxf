from __future__ import annotations

from dataclasses import dataclass
from math import atan2, degrees, hypot

from . import Point


@dataclass(frozen=True)
class CircleFit:
    center: Point
    radius: float
    max_error: float
    rms_error: float


def fit_circle(points: list[Point]) -> CircleFit | None:
    if len(points) < 3:
        return None
    p1 = points[0]
    p2 = max(points[1:], key=lambda point: (point.x - p1.x) ** 2 + (point.y - p1.y) ** 2)
    p3 = max(
        points,
        key=lambda point: abs((p2.x - p1.x) * (point.y - p1.y) - (p2.y - p1.y) * (point.x - p1.x)),
    )
    determinant = 2 * (
        p1.x * (p2.y - p3.y) + p2.x * (p3.y - p1.y) + p3.x * (p1.y - p2.y)
    )
    if abs(determinant) < 1e-10:
        return None
    p1s = p1.x * p1.x + p1.y * p1.y
    p2s = p2.x * p2.x + p2.y * p2.y
    p3s = p3.x * p3.x + p3.y * p3.y
    cx = (p1s * (p2.y - p3.y) + p2s * (p3.y - p1.y) + p3s * (p1.y - p2.y)) / determinant
    cy = (p1s * (p3.x - p2.x) + p2s * (p1.x - p3.x) + p3s * (p2.x - p1.x)) / determinant
    center = Point(cx, cy)
    distances = [hypot(point.x - cx, point.y - cy) for point in points]
    radius = sum(distances) / len(distances)
    errors = [abs(distance - radius) for distance in distances]
    rms = (sum(error * error for error in errors) / len(errors)) ** 0.5
    return CircleFit(center, radius, max(errors), rms)


def angle(center: Point, point: Point) -> float:
    return degrees(atan2(point.y - center.y, point.x - center.x)) % 360.0


def signed_turn(center: Point, points: list[Point]) -> float:
    total = 0.0
    previous = atan2(points[0].y - center.y, points[0].x - center.x)
    for point in points[1:]:
        current = atan2(point.y - center.y, point.x - center.x)
        delta = current - previous
        while delta <= -3.141592653589793:
            delta += 6.283185307179586
        while delta > 3.141592653589793:
            delta -= 6.283185307179586
        total += delta
        previous = current
    return total
