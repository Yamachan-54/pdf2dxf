from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Feature:
    id: str
    type: str
    entity_ids: tuple[str, ...]
    confidence: float
    metadata: dict[str, object] = field(default_factory=dict)
