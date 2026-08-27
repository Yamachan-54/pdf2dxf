from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Constraint:
    id: str
    type: str
    entity_ids: tuple[str, ...]
    value: float | None = None
    confidence: float = 0.0
    metadata: dict[str, object] = field(default_factory=dict)
