from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Iterable

from .cad.model import CadModel, build_cad_model
from .config import ReconstructionConfig
from .exporters.dxf import DxfExporter
from .exporters.base import CadExporter, ExportResult
from .geometry.reconstruction import PrimitiveReconstructor
from .geometry.solver import PassthroughSolver
from .input.vector_pdf import VectorPdfParser
from .interpreter.classifier import SemanticClassifier
from .ir.drawing import Drawing
from .sheet.analyzer import SheetAnalyzer
from .views.detector import ViewDetector


UNIT_SETTINGS = {"mm": (25.4 / 72.0, 4), "inch": (1.0 / 72.0, 1), "pt": (1.0, 0)}


@dataclass(frozen=True)
class PipelineOutput:
    drawing: Drawing
    cad_model: CadModel
    selected_pages: tuple[int, ...]
    empty_pages: tuple[int, ...]
    export_result: ExportResult


class ConversionPipeline:
    def __init__(
        self,
        *,
        curve_steps: int | None = None,
        exporter: CadExporter | None = None,
    ) -> None:
        config = ReconstructionConfig(default_curve_steps=curve_steps)
        self.parser = VectorPdfParser()
        self.reconstructor = PrimitiveReconstructor(config)
        self.classifier = SemanticClassifier()
        self.sheet_analyzer = SheetAnalyzer()
        self.view_detector = ViewDetector()
        self.solver = PassthroughSolver()
        self.exporter = exporter or DxfExporter()

    def run(
        self, source: Path, destination: Path, *, pages: Iterable[int] | None,
        unit: str, scale: float, layout: str, page_gap: float,
        dump_ir: Path | None = None, debug_dir: Path | None = None,
    ) -> PipelineOutput:
        unit_scale, _units_code = UNIT_SETTINGS[unit]
        drawing = self.parser.parse(
            source, pages=pages, unit=unit, factor=unit_scale * scale,
            layout=layout, page_gap=page_gap,
        )
        extracted_json = drawing.to_json()
        page_numbers = tuple(sheet.page for sheet in drawing.sheets)
        pages_with_input = {entity.page for entity in drawing.entities}
        empty_pages = tuple(page for page in page_numbers if page not in pages_with_input)
        drawing = self.reconstructor.reconstruct(drawing)
        reconstructed_json = drawing.to_json()
        drawing = self.classifier.classify(drawing)
        drawing = self.sheet_analyzer.analyze(drawing)
        drawing = self.view_detector.detect(drawing)
        drawing = self.solver.solve(drawing)
        cad_model = build_cad_model(drawing)
        export_result = self.exporter.export(cad_model, destination)
        if dump_ir is not None:
            drawing.write_json(dump_ir)
        if debug_dir is not None:
            self._write_debug(
                debug_dir, extracted_json, reconstructed_json, drawing,
                export_result,
            )
        return PipelineOutput(
            drawing, cad_model, page_numbers, empty_pages, export_result
        )

    @staticmethod
    def _write_debug(
        debug_dir: Path,
        extracted_json: str,
        reconstructed_json: str,
        drawing: Drawing,
        export_result: ExportResult,
    ) -> None:
        debug_dir.mkdir(parents=True, exist_ok=True)
        (debug_dir / "extracted_ir.json").write_text(extracted_json + "\n", encoding="utf-8")
        (debug_dir / "reconstruction.json").write_text(reconstructed_json + "\n", encoding="utf-8")
        drawing.write_json(debug_dir / "drawing_ir.json")
        semantic = [
            {"id": entity.id, "primitive": entity.primitive, "semantic_type": entity.semantic_type.value,
             "view": entity.view, "confidence": entity.confidence}
            for entity in drawing.entities
        ]
        (debug_dir / "semantic_entities.json").write_text(
            json.dumps(semantic, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (debug_dir / "dxf_export.json").write_text(
            json.dumps(
                [trace.__dict__ for trace in export_result.traces],
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
