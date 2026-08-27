from __future__ import annotations

import json
from math import cos, pi, sin
from pathlib import Path
import re
import tempfile
import unittest

import ezdxf

from pdf2dxf.cad.model import (
    CadArc, CadCircle, CadDimension, CadLine, CadModel, CadMText,
    CadPolyline, CadText,
)
from pdf2dxf.cli import main
from pdf2dxf.converter import convert_pdf
from pdf2dxf.config import DxfExportConfig
from pdf2dxf.exporters.dxf import DxfExporter, LayerPolicy
from pdf2dxf.geometry import Point, Segment
from pdf2dxf.geometry.reconstruction import PrimitiveReconstructor
from pdf2dxf.ir.drawing import Drawing
from pdf2dxf.ir.entities import (
    Entity, LineGeometry, SemanticType, SourceEvidence, Style,
)


def _make_feature_pdf(path: Path) -> None:
    import pymupdf

    document = pymupdf.open()
    page = document.new_page(width=300, height=300)
    shape = page.new_shape()
    shape.draw_rect((3, 3, 297, 297))
    shape.finish(color=(0, 0, 0))
    shape.commit()

    shape = page.new_shape()
    shape.draw_line((60, 120), (140, 120))
    shape.finish(color=(0, 0, 0), dashes="[6 2 1 2] 0")
    shape.commit()

    shape = page.new_shape()
    shape.draw_rect((225, 240, 260, 260))
    shape.draw_rect((260, 240, 295, 260))
    shape.draw_rect((225, 260, 295, 280))
    shape.finish(color=(0, 0, 0))
    shape.commit()

    shape = page.new_shape()
    shape.draw_line((30, 50), (70, 50))
    shape.draw_line((70, 50), (120, 50))
    shape.draw_circle((100, 120), 25)
    shape.finish(color=(0, 0, 0))
    shape.commit()

    kappa = 0.5522847498307936
    shape = page.new_shape()
    shape.draw_bezier((180, 120), (180, 120 + 30 * kappa), (180 + 30 * (1 - kappa), 150), (210, 150))
    shape.finish(color=(0, 0, 0))
    shape.commit()
    page.insert_text((30, 220), "100 mm", fontsize=12)
    document.save(path)
    document.close()


def _decode_r2000_unicode(text: str) -> str:
    return re.sub(
        r"\\U\+([0-9a-fA-F]{4})",
        lambda match: chr(int(match.group(1), 16)),
        text,
    )


class PipelineTests(unittest.TestCase):
    def test_native_entities_ir_layers_and_debug_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "feature.pdf"
            target = root / "feature.dxf"
            ir_path = root / "drawing.json"
            debug = root / "debug"
            _make_feature_pdf(source)

            result = convert_pdf(source, target, unit="pt", dump_ir=ir_path, debug_dir=debug)

            self.assertEqual(result.pages, 1)
            document = ezdxf.readfile(target)
            auditor = document.audit()
            self.assertFalse(auditor.has_errors)
            types = [entity.dxftype() for entity in document.modelspace()]
            self.assertIn("LINE", types)
            self.assertIn("CIRCLE", types)
            self.assertIn("ARC", types)
            self.assertIn("TEXT", types)
            for layer in ("GEOMETRY", "CENTER", "HIDDEN", "DIMENSION", "TEXT", "REFERENCE"):
                self.assertIn(layer, document.layers)
            self.assertEqual(document.units, 0)
            self.assertEqual(types.count("CIRCLE"), 1)
            center_lines = [
                entity for entity in document.modelspace().query("LINE")
                if entity.dxf.layer == "CENTER"
            ]
            self.assertEqual(len(center_lines), 1)
            self.assertEqual(center_lines[0].dxf.linetype.upper(), "BYLAYER")
            self.assertEqual(document.layers.get("CENTER").dxf.linetype.upper(), "CENTER")

            ir = json.loads(ir_path.read_text(encoding="utf-8"))
            primitives = {entity["primitive"] for entity in ir["entities"]}
            self.assertTrue({"line", "circle", "arc", "text"}.issubset(primitives))
            border = next(entity for entity in ir["entities"] if entity["semantic_type"] == "sheet_border")
            self.assertEqual(border["primitive"], "polyline")
            self.assertIn(border["id"], ir["sheets"][0]["border_entity_ids"])
            self.assertNotIn(border["id"], [item for view in ir["views"] for item in view["entity_ids"]])
            self.assertEqual(len(ir["sheets"][0]["title_block_entity_ids"]), 3)
            title_ids = set(ir["sheets"][0]["title_block_entity_ids"])
            self.assertEqual(
                {entity["semantic_type"] for entity in ir["entities"] if entity["id"] in title_ids},
                {"title_block_line"},
            )
            for name in (
                "extracted_ir.json", "reconstruction.json",
                "semantic_entities.json", "drawing_ir.json", "dxf_export.json",
            ):
                self.assertTrue((debug / name).is_file(), name)
            export_trace = json.loads((debug / "dxf_export.json").read_text(encoding="utf-8"))
            circle_trace = next(trace for trace in export_trace if trace["dxf_type"] == "CIRCLE")
            self.assertEqual(circle_trace["cad_type"], "CadCircle")
            self.assertEqual(circle_trace["layer"], "GEOMETRY")
            self.assertEqual(circle_trace["status"], "exported")

    def test_cli_dump_ir_and_default_debug_directory_argument(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "feature.pdf"
            target = root / "feature.dxf"
            dump = root / "ir.json"
            debug = root / "analysis"
            _make_feature_pdf(source)
            result = main([str(source), str(target), "--unit", "pt", "--dump-ir", str(dump), "--debug", str(debug)])
            self.assertEqual(result, 0)
            self.assertTrue(target.is_file())
            self.assertTrue(dump.is_file())
            self.assertTrue((debug / "drawing_ir.json").is_file())

    def test_collinear_native_lines_are_merged(self) -> None:
        style = Style()
        source = SourceEvidence("test", 1)
        drawing = Drawing(
            "mm",
            entities=[
                Entity("L1", "line", LineGeometry(Point(0, 0), Point(5, 0)), source=source, style=style, page=1, metadata={"path_id": "P1"}),
                Entity("L2", "line", LineGeometry(Point(5, 0), Point(10, 0)), source=source, style=style, page=1, metadata={"path_id": "P1"}),
            ],
        )
        reconstructed = PrimitiveReconstructor().reconstruct(drawing)
        self.assertEqual(len(reconstructed.entities), 1)
        geometry = reconstructed.entities[0].geometry
        self.assertEqual(geometry, LineGeometry(Point(0, 0), Point(10, 0)))

    def test_closed_short_line_chain_is_reconstructed_as_circle(self) -> None:
        source = SourceEvidence("test", 1)
        style = Style()
        points = [Point(10 * cos(2 * pi * index / 24), 10 * sin(2 * pi * index / 24)) for index in range(24)]
        entities = [
            Entity(
                f"L{index}", "line", LineGeometry(start, end), source=source, style=style,
                page=1, metadata={"path_id": "CIRCLE_PATH"},
            )
            for index, (start, end) in enumerate(zip(points, points[1:] + points[:1]))
        ]
        drawing = PrimitiveReconstructor().reconstruct(Drawing("mm", entities=entities))
        self.assertEqual(len(drawing.entities), 1)
        self.assertEqual(drawing.entities[0].primitive, "circle")
        self.assertEqual(drawing.entities[0].semantic_type, SemanticType.HOLE)

    def test_ezdxf_round_trip_entities_layers_linetypes_and_dimension(self) -> None:
        model = CadModel(
            "mm",
            [
                CadLine(
                    "L1", SemanticType.CENTER_LINE, None, 0.9, None, 1,
                    Segment(Point(0, 0), Point(10, 0)),
                ),
                CadLine(
                    "L2", SemanticType.HIDDEN_LINE, None, 0.8, None, 1,
                    Segment(Point(0, 1), Point(10, 1)),
                ),
                CadCircle(
                    "C1", SemanticType.HOLE, None, 0.99, None, 1,
                    Point(5, 5), 10,
                ),
                CadArc(
                    "A1", SemanticType.OUTER_CONTOUR, None, 0.95, None, 1,
                    Point(20, 20), 5, 15, 120,
                ),
                CadPolyline(
                    "P1", SemanticType.OUTER_CONTOUR, None, 1.0, None, 1,
                    (Point(0, 0), Point(1, 0), Point(1, 1)), True,
                ),
                CadText(
                    "T1", SemanticType.TEXT, None, 1.0, None, 1, "φ20",
                    Point(1, 2), 2.5, 15, "LEFT", "Standard",
                ),
                CadMText(
                    "M1", SemanticType.DIMENSION_TEXT, None, 1.0, None, 1,
                    "A\nB", Point(1, 5), 2.5, 0, 10, "TOP_LEFT", "Standard",
                ),
                CadDimension(
                    "D1", SemanticType.DIMENSION_LINE, None, 0.95, None, 1,
                    "linear", 10.0, ("L1",), Point(0, 0), Point(10, 0),
                    Point(0, 3), 0,
                ),
                CadDimension(
                    "D2", SemanticType.DIMENSION_LINE, None, 0.4, None, 1,
                    "linear", 10.0, ("UNKNOWN",),
                ),
            ],
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "text.dxf"
            result = DxfExporter(LayerPolicy()).export(model, path)
            document = ezdxf.readfile(path)
            auditor = document.audit()
            self.assertFalse(auditor.has_errors)
            self.assertEqual(document.dxfversion, "AC1015")
            self.assertEqual(document.units, 4)
            modelspace = document.modelspace()
            counts = {
                entity_type: len(modelspace.query(entity_type))
                for entity_type in (
                    "LINE", "CIRCLE", "ARC", "LWPOLYLINE", "TEXT", "MTEXT",
                    "DIMENSION",
                )
            }
            self.assertEqual(
                counts,
                {
                    "LINE": 2, "CIRCLE": 1, "ARC": 1, "LWPOLYLINE": 1,
                    "TEXT": 1, "MTEXT": 1, "DIMENSION": 1,
                },
            )
            self.assertEqual(modelspace.query("CIRCLE").first.dxf.radius, 10)
            self.assertEqual(modelspace.query("ARC").first.dxf.start_angle, 15)
            self.assertEqual(
                _decode_r2000_unicode(modelspace.query("TEXT").first.dxf.text),
                "φ20",
            )
            self.assertEqual(modelspace.query("TEXT").first.dxf.layer, "TEXT")
            self.assertEqual(modelspace.query("TEXT").first.dxf.style, "Standard")
            self.assertEqual(tuple(modelspace.query("TEXT").first.dxf.insert)[:2], (1.0, 2.0))
            self.assertEqual(modelspace.query("MTEXT").first.dxf.layer, "DIMENSION")
            self.assertEqual(modelspace.query("MTEXT").first.dxf.attachment_point, 1)
            self.assertEqual(modelspace.query("MTEXT").first.plain_text(), "A\nB")
            self.assertEqual(modelspace.query("DIMENSION").first.dxf.layer, "DIMENSION")
            self.assertEqual(document.layers.get("CENTER").dxf.linetype.upper(), "CENTER")
            self.assertEqual(document.layers.get("HIDDEN").dxf.linetype.upper(), "HIDDEN")
            center_line = next(entity for entity in modelspace.query("LINE") if entity.dxf.layer == "CENTER")
            hidden_line = next(entity for entity in modelspace.query("LINE") if entity.dxf.layer == "HIDDEN")
            self.assertEqual(center_line.dxf.linetype.upper(), "BYLAYER")
            self.assertEqual(hidden_line.dxf.linetype.upper(), "BYLAYER")
            self.assertEqual(result.exported_count, 8)
            self.assertEqual(result.skipped_count, 1)
            unresolved = next(trace for trace in result.traces if trace.status == "unresolved")
            self.assertEqual(unresolved.source_id, "D2")
            self.assertIn("missing", unresolved.reason or "")

    def test_ezdxf_units_round_trip(self) -> None:
        for unit, expected in (("mm", 4), ("inch", 1), ("pt", 0)):
            with self.subTest(unit=unit), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / f"{unit}.dxf"
                DxfExporter().export(CadModel(unit), path)
                self.assertEqual(ezdxf.readfile(path).units, expected)

    def test_missing_custom_linetype_falls_back_to_continuous(self) -> None:
        model = CadModel(
            "mm",
            [
                CadLine(
                    "L1", SemanticType.CENTER_LINE, None, 1.0, None, 1,
                    Segment(Point(0, 0), Point(1, 0)),
                )
            ],
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fallback.dxf"
            config = DxfExportConfig(center_linetype="UNDEFINED_CENTER")
            DxfExporter(config=config).export(model, path)
            document = ezdxf.readfile(path)
            self.assertEqual(
                document.layers.get("CENTER").dxf.linetype.upper(),
                "CONTINUOUS",
            )


if __name__ == "__main__":
    unittest.main()
