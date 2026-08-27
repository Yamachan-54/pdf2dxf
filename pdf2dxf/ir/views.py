from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ViewType(str, Enum):
    FRONT = "front"
    REAR = "rear"
    TOP = "top"
    BOTTOM = "bottom"
    LEFT = "left"
    RIGHT = "right"
    SECTION = "section"
    DETAIL = "detail"
    AUXILIARY = "auxiliary"
    UNKNOWN = "unknown"


@dataclass
class View:
    id: str
    type: ViewType
    bbox: tuple[float, float, float, float]
    confidence: float
    page: int
    entity_ids: tuple[str, ...] = ()
