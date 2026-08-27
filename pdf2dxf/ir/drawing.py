from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import json
from pathlib import Path
from typing import Any

from .constraints import Constraint
from .entities import Entity
from .features import Feature
from .views import View


@dataclass
class Sheet:
    id: str
    page: int
    bbox: tuple[float, float, float, float]
    border_entity_ids: tuple[str, ...] = ()
    title_block_entity_ids: tuple[str, ...] = ()
    revision_table_entity_ids: tuple[str, ...] = ()
    note_entity_ids: tuple[str, ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


@dataclass
class Drawing:
    unit: str
    sheets: list[Sheet] = field(default_factory=list)
    views: list[View] = field(default_factory=list)
    entities: list[Entity] = field(default_factory=list)
    features: list[Feature] = field(default_factory=list)
    constraints: list[Constraint] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)
    schema_version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        return _json_value(asdict(self))

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def write_json(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json() + "\n", encoding="utf-8")
