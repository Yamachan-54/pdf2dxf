from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..cad.model import CadModel


@dataclass(frozen=True)
class ExportTrace:
    source_id: str
    ir_id: str
    cad_type: str
    dxf_type: str | None
    layer: str
    linetype: str
    confidence: float
    status: str
    handle: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class ExportResult:
    traces: tuple[ExportTrace, ...]

    @property
    def exported_count(self) -> int:
        return sum(trace.status == "exported" for trace in self.traces)

    @property
    def skipped_count(self) -> int:
        return sum(trace.status == "unresolved" for trace in self.traces)


class CadExporter(Protocol):
    def export(self, model: CadModel, destination: Path) -> ExportResult: ...
