from __future__ import annotations

import json
from collections import Counter
from math import cos, pi, sin
from pathlib import Path
import re
import tempfile
import unittest

import ezdxf

from pdf2dxf.cad.model import (
    CadArc, CadCircle, CadDimension, CadLine, CadModel, CadMText,
    CadPolyline, CadText, build_cad_model,
)
from pdf2dxf.cli import main
from pdf2dxf.converter import ConversionError, convert_pdf
from pdf2dxf.config import DxfExportConfig
from pdf2dxf.exporters.dxf import DxfExporter, LayerPolicy
from pdf2dxf.geometry import Point, Segment
from pdf2dxf.geometry.reconstruction import PrimitiveReconstructor
from pdf2dxf.input.vector_pdf import VectorPdfParser
from pdf2dxf.input.ocr import TesseractOcrAdapter, TesseractOcrConfig
from pdf2dxf.interpreter.classifier import SemanticClassifier
from pdf2dxf.interpreter.dimensions import DimensionInterpreter
from pdf2dxf.ir.drawing import Drawing, Sheet
from pdf2dxf.ir.entities import (
    CircleGeometry, DimensionGeometry, Entity, LineGeometry, SemanticType,
    SourceEvidence, Style, TextGeometry,
)
from pdf2dxf.sheet.analyzer import SheetAnalyzer
from pdf2dxf.views.detector import ViewDetector


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

    def test_degenerate_native_lines_are_dropped_and_reported(self) -> None:
        source = SourceEvidence("test", 1)
        drawing = Drawing(
            "mm",
            entities=[
                Entity(
                    "ZERO", "line", LineGeometry(Point(3, 4), Point(3, 4)),
                    source=source, page=1,
                ),
                Entity(
                    "VALID", "line", LineGeometry(Point(0, 0), Point(5, 0)),
                    source=source, page=1,
                ),
            ],
        )

        reconstructed = PrimitiveReconstructor().reconstruct(drawing)

        self.assertEqual([entity.id for entity in reconstructed.entities], ["VALID"])
        self.assertEqual(
            reconstructed.metadata["reconstruction_stats"]["dropped_degenerate_lines"],
            1,
        )

    def test_rotated_pdf_coordinates_are_normalized_to_displayed_page(self) -> None:
        import pymupdf

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "rotated.pdf"
            document = pymupdf.open()
            page = document.new_page(width=100, height=200)
            shape = page.new_shape()
            shape.draw_line((10, 20), (30, 20))
            shape.finish(color=(0, 0, 0))
            shape.commit()
            page.set_rotation(90)
            document.save(source)
            document.close()

            drawing = VectorPdfParser().parse(
                source, pages=None, unit="pt", factor=1.0,
                layout="overlay", page_gap=0.0,
            )

        self.assertEqual(drawing.sheets[0].bbox, (0.0, 0.0, 200.0, 100.0))
        self.assertEqual(drawing.sheets[0].metadata["pdf_rotation"], 90)
        geometry = drawing.entities[0].geometry
        self.assertIsInstance(geometry, LineGeometry)
        self.assertEqual(geometry, LineGeometry(Point(180, 90), Point(180, 70)))
        for point in (geometry.start, geometry.end):
            self.assertGreaterEqual(point.x, 0)
            self.assertLessEqual(point.x, 200)
            self.assertGreaterEqual(point.y, 0)
            self.assertLessEqual(point.y, 100)

    def test_explicit_long_dot_chain_is_classified_as_centerline(self) -> None:
        lengths = [12.0, 0.5, 10.0, 0.5, 10.0, 0.5, 8.0]
        entities = []
        cursor = 20.0
        for index, length in enumerate(lengths):
            entities.append(
                Entity(
                    f"CENTER_{index}", "line",
                    LineGeometry(Point(cursor, 50), Point(cursor + length, 50)),
                    source=SourceEvidence("test", 1), page=1,
                )
            )
            cursor += length + 1.0
        # Equal-length repeated marks are not a long-dot center pattern.
        cursor = 20.0
        for index in range(5):
            entities.append(
                Entity(
                    f"OTHER_{index}", "line",
                    LineGeometry(Point(cursor, 70), Point(cursor + 3, 70)),
                    source=SourceEvidence("test", 1), page=1,
                )
            )
            cursor += 4.0
        drawing = Drawing(
            "mm", sheets=[Sheet("SHEET_001", 1, (0, 0, 200, 100))],
            entities=entities,
        )

        classified = SemanticClassifier().classify(drawing)

        center = [entity for entity in classified.entities if entity.id.startswith("CENTER_")]
        other = [entity for entity in classified.entities if entity.id.startswith("OTHER_")]
        self.assertEqual({entity.semantic_type for entity in center}, {SemanticType.CENTER_LINE})
        self.assertEqual({entity.metadata["semantic_evidence"] for entity in center}, {"segmented_centerline"})
        self.assertEqual({entity.semantic_type for entity in other}, {SemanticType.OUTER_CONTOUR})

    def test_full_height_title_separator_defines_drawing_area(self) -> None:
        entities = [
            Entity(
                "DRAWING", "line", LineGeometry(Point(20, 50), Point(80, 50)),
                page=1,
            ),
            Entity(
                "SEPARATOR", "line", LineGeometry(Point(160, 5), Point(160, 95)),
                page=1,
            ),
            Entity(
                "TITLE_1", "line", LineGeometry(Point(170, 20), Point(190, 20)),
                page=1,
            ),
            Entity(
                "TITLE_2", "line", LineGeometry(Point(170, 40), Point(190, 40)),
                page=1,
            ),
        ]
        drawing = Drawing(
            "mm", sheets=[Sheet("SHEET_001", 1, (0, 0, 200, 100))],
            entities=entities,
        )

        analyzed = SheetAnalyzer().analyze(drawing)

        sheet = analyzed.sheets[0]
        self.assertEqual(sheet.metadata["title_block_separator_x"], 160)
        self.assertEqual(sheet.metadata["drawing_area_bbox"], (0, 0, 160, 100))
        self.assertEqual(
            {entity.id for entity in analyzed.entities if entity.semantic_type == SemanticType.TITLE_BLOCK_LINE},
            {"SEPARATOR", "TITLE_1", "TITLE_2"},
        )
        self.assertEqual(analyzed.entities[0].semantic_type, SemanticType.UNKNOWN)

    def test_xy_cut_separates_four_dense_drawing_regions(self) -> None:
        entities = []
        for group_x in (20.0, 120.0):
            for group_y in (20.0, 80.0):
                for index in range(15):
                    x = group_x + index % 5
                    y = group_y + index // 5
                    entities.append(
                        Entity(
                            f"E{len(entities)}", "line",
                            LineGeometry(Point(x, y), Point(x + 10, y)), page=1,
                        )
                    )

        groups = ViewDetector._xy_cut(
            entities, sheet_width=200, sheet_height=120,
            minimum_gap=10, depth=0,
        )

        self.assertEqual(len(groups), 4)
        self.assertEqual(sorted(len(group) for group in groups), [15, 15, 15, 15])

    def test_dimension_dots_and_linework_are_separated_from_holes(self) -> None:
        marker_metadata = {
            "reconstruction": "circle_from_lines",
            "source_entities": [f"S{index}" for index in range(20)],
        }
        drawing = Drawing(
            "mm",
            sheets=[Sheet("SHEET_001", 1, (0, 0, 200, 200))],
            entities=[
                Entity(
                    "M1", "circle", CircleGeometry(Point(20, 50), 0.5),
                    page=1, metadata=dict(marker_metadata),
                ),
                Entity(
                    "M2", "circle", CircleGeometry(Point(120, 50), 0.5),
                    page=1, metadata=dict(marker_metadata),
                ),
                Entity(
                    "DIM", "line", LineGeometry(Point(20.5, 50), Point(119.5, 50)),
                    page=1,
                ),
                Entity(
                    "EXT1", "line", LineGeometry(Point(20, 50.5), Point(20, 70)),
                    page=1,
                ),
                Entity(
                    "EXT2", "line", LineGeometry(Point(120, 50.5), Point(120, 70)),
                    page=1,
                ),
                Entity(
                    "HOLE", "circle", CircleGeometry(Point(50, 120), 5), page=1,
                ),
                Entity(
                    "DIM_TEXT", "text",
                    TextGeometry("100", Point(67, 52), 2, width=6), page=1,
                ),
                Entity(
                    "OTHER_TEXT", "text",
                    TextGeometry("303", Point(67, 100), 2, width=6), page=1,
                ),
                Entity(
                    "VARIABLE_TEXT", "text",
                    TextGeometry("W2", Point(77, 52), 2, width=6), page=1,
                ),
            ],
        )

        drawing = SemanticClassifier().classify(drawing)
        interpreter = DimensionInterpreter()
        drawing = interpreter.resolve(interpreter.analyze(drawing))

        semantics = {entity.id: entity.semantic_type for entity in drawing.entities}
        self.assertEqual(semantics["M1"], SemanticType.DIMENSION_MARKER)
        self.assertEqual(semantics["M2"], SemanticType.DIMENSION_MARKER)
        self.assertEqual(semantics["DIM"], SemanticType.DIMENSION_LINE)
        self.assertEqual(semantics["EXT1"], SemanticType.DIMENSION_EXTENSION_LINE)
        self.assertEqual(semantics["EXT2"], SemanticType.DIMENSION_EXTENSION_LINE)
        self.assertEqual(semantics["HOLE"], SemanticType.HOLE)
        self.assertEqual(semantics["DIM_TEXT"], SemanticType.DIMENSION_TEXT)
        self.assertEqual(semantics["VARIABLE_TEXT"], SemanticType.DIMENSION_TEXT)
        self.assertEqual(semantics["OTHER_TEXT"], SemanticType.TEXT)
        self.assertEqual(
            next(
                entity for entity in drawing.entities if entity.id == "DIM_TEXT"
            ).metadata["parsed_dimension_value"],
            100.0,
        )
        self.assertNotIn(
            "parsed_dimension_value",
            next(
                entity for entity in drawing.entities
                if entity.id == "VARIABLE_TEXT"
            ).metadata,
        )
        self.assertEqual(
            drawing.metadata["dimension_analysis"],
            {
                "native_dimension_entities": 0,
                "unresolved_graphic_groups": 1,
                "unresolved_reasons": {
                    "DIMENSION_GRAPHIC_001": "dimension_text_ambiguous",
                },
                "view_measurement_scales": {},
                "marker_entities": 2,
                "dimension_line_entities": 1,
                "extension_line_entities": 2,
                "dimension_text_entities": 2,
                "dimension_text_roles": {
                    "primary": 0, "reference": 0, "ambiguous": 2,
                },
            },
        )

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "dimension-graphics.dxf"
            DxfExporter().export(build_cad_model(drawing), target)
            document = ezdxf.readfile(target)
            circle_layers = Counter(
                entity.dxf.layer for entity in document.modelspace().query("CIRCLE")
            )
            line_layers = Counter(
                entity.dxf.layer for entity in document.modelspace().query("LINE")
            )
            text_layers = Counter(
                entity.dxf.layer for entity in document.modelspace().query("TEXT")
            )
        self.assertEqual(circle_layers, Counter({"DIMENSION": 2, "GEOMETRY": 1}))
        self.assertEqual(line_layers, Counter({"DIMENSION": 3}))
        self.assertEqual(text_layers, Counter({"DIMENSION": 2, "TEXT": 1}))

    def test_complete_one_to_one_dimension_is_promoted_to_native_entity(self) -> None:
        marker_metadata = {
            "reconstruction": "circle_from_lines",
            "source_entities": [f"S{index}" for index in range(20)],
        }
        drawing = Drawing(
            "mm", sheets=[Sheet("SHEET_001", 1, (0, 0, 200, 200))],
            entities=[
                Entity(
                    "M1", "circle", CircleGeometry(Point(20, 50), 0.5),
                    page=1, metadata=dict(marker_metadata),
                ),
                Entity(
                    "M2", "circle", CircleGeometry(Point(120, 50), 0.5),
                    page=1, metadata=dict(marker_metadata),
                ),
                Entity(
                    "DIM", "line", LineGeometry(Point(20.5, 50), Point(119.5, 50)),
                    page=1,
                ),
                Entity(
                    "EXT1", "line", LineGeometry(Point(20, 50.5), Point(20, 70)),
                    page=1,
                ),
                Entity(
                    "EXT2", "line", LineGeometry(Point(120, 50.5), Point(120, 70)),
                    page=1,
                ),
                Entity(
                    "DIM_TEXT", "text",
                    TextGeometry("0100", Point(67, 52), 2, width=6), page=1,
                ),
            ],
        )

        interpreter = DimensionInterpreter()
        drawing = interpreter.resolve(
            interpreter.analyze(SemanticClassifier().classify(drawing))
        )

        native = [
            entity for entity in drawing.entities
            if isinstance(entity.geometry, DimensionGeometry)
        ]
        self.assertEqual(len(native), 1)
        self.assertEqual(native[0].geometry.first_point, Point(20, 70))
        self.assertEqual(native[0].geometry.second_point, Point(120, 70))
        self.assertEqual(native[0].geometry.value, 100.0)
        self.assertEqual(native[0].geometry.display_text, "□100")
        normalized_text = next(
            entity for entity in drawing.entities if entity.id == "DIM_TEXT"
        )
        self.assertEqual(normalized_text.geometry.text, "□100")
        self.assertEqual(normalized_text.metadata["ocr_raw_text"], "0100")
        self.assertEqual(normalized_text.metadata["dimension_symbol"], "square")
        self.assertEqual(drawing.metadata["dimension_analysis"]["native_dimension_entities"], 1)
        self.assertEqual(drawing.metadata["dimension_analysis"]["unresolved_graphic_groups"], 0)

        model = build_cad_model(drawing)
        self.assertEqual([type(entity).__name__ for entity in model.entities], ["CadDimension"])
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "native-dimension.dxf"
            DxfExporter().export(model, target)
            document = ezdxf.readfile(target)
            self.assertFalse(document.audit().has_errors)
            dimension = document.modelspace().query("DIMENSION").first
            self.assertIsNotNone(dimension)
            self.assertEqual(_decode_r2000_unicode(dimension.dxf.text), "□100")
            self.assertEqual(len(document.modelspace().query("CIRCLE LINE TEXT")), 0)

    def test_two_dimensions_in_one_view_confirm_three_to_one_scale(self) -> None:
        marker_metadata = {
            "reconstruction": "circle_from_lines",
            "source_entities": [f"S{index}" for index in range(20)],
        }
        entities = []
        for prefix, y, end_x, text in (
            ("A", 40.0, 100.0, "300"),
            ("B", 90.0, 50.0, "150"),
        ):
            entities.extend(
                [
                    Entity(
                        f"{prefix}_M1", "circle", CircleGeometry(Point(0, y), 0.5),
                        page=1, metadata=dict(marker_metadata),
                    ),
                    Entity(
                        f"{prefix}_M2", "circle", CircleGeometry(Point(end_x, y), 0.5),
                        page=1, metadata=dict(marker_metadata),
                    ),
                    Entity(
                        f"{prefix}_DIM", "line",
                        LineGeometry(Point(0.5, y), Point(end_x - 0.5, y)), page=1,
                    ),
                    Entity(
                        f"{prefix}_EXT1", "line",
                        LineGeometry(Point(0, y + 0.5), Point(0, y + 10)), page=1,
                    ),
                    Entity(
                        f"{prefix}_EXT2", "line",
                        LineGeometry(Point(end_x, y + 0.5), Point(end_x, y + 10)), page=1,
                    ),
                    Entity(
                        f"{prefix}_TEXT", "text",
                        TextGeometry(text, Point(end_x / 2 - 3, y + 1), 2, width=6),
                        page=1,
                    ),
                ]
            )
        drawing = Drawing(
            "mm", sheets=[Sheet("SHEET_001", 1, (-10, 0, 200, 200))],
            entities=entities,
        )
        interpreter = DimensionInterpreter()
        drawing = interpreter.analyze(SemanticClassifier().classify(drawing))
        for entity in drawing.entities:
            if entity.semantic_type != SemanticType.DIMENSION_TEXT:
                entity.view = "VIEW_001"
        drawing = interpreter.resolve(drawing)

        native = [
            entity for entity in drawing.entities
            if isinstance(entity.geometry, DimensionGeometry)
        ]
        self.assertEqual(len(native), 2)
        self.assertEqual({entity.geometry.measurement_scale for entity in native}, {3.0})
        scale = drawing.metadata["dimension_analysis"]["view_measurement_scales"]["VIEW_001"]
        self.assertEqual(scale["factor"], 3.0)
        self.assertEqual(len(scale["support_groups"]), 2)

        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "scaled-dimensions.dxf"
            DxfExporter().export(build_cad_model(drawing), target)
            document = ezdxf.readfile(target)
            self.assertFalse(document.audit().has_errors)
            dimensions = list(document.modelspace().query("DIMENSION"))
            self.assertEqual(len(dimensions), 2)
            rendered_text = {
                entity.dxf.text
                for dimension in dimensions
                for entity in document.blocks.get(dimension.dxf.geometry)
                if entity.dxftype() in {"TEXT", "MTEXT"}
            }
            self.assertEqual(rendered_text, {"150", "300"})

    def test_crossing_dimension_lines_can_serve_as_shared_extension_lines(self) -> None:
        marker_metadata = {
            "reconstruction": "circle_from_lines",
            "source_entities": [f"S{index}" for index in range(20)],
        }
        drawing = Drawing(
            "mm", sheets=[Sheet("SHEET_001", 1, (0, 0, 200, 200))],
            entities=[
                Entity(
                    "SHARED", "circle", CircleGeometry(Point(20, 50), 0.5),
                    page=1, metadata=dict(marker_metadata),
                ),
                Entity(
                    "H_MARK", "circle", CircleGeometry(Point(120, 50), 0.5),
                    page=1, metadata=dict(marker_metadata),
                ),
                Entity(
                    "V_MARK", "circle", CircleGeometry(Point(20, 80), 0.5),
                    page=1, metadata=dict(marker_metadata),
                ),
                Entity(
                    "H_DIM", "line", LineGeometry(Point(20.5, 50), Point(119.5, 50)),
                    page=1,
                ),
                Entity(
                    "V_DIM", "line", LineGeometry(Point(20, 50.5), Point(20, 79.5)),
                    page=1,
                ),
                Entity(
                    "H_EXT", "line", LineGeometry(Point(120, 50.5), Point(120, 70)),
                    page=1,
                ),
                Entity(
                    "V_EXT", "line", LineGeometry(Point(20.5, 80), Point(40, 80)),
                    page=1,
                ),
                Entity(
                    "H_TEXT", "text", TextGeometry("100", Point(67, 52), 2, width=6),
                    page=1,
                ),
                Entity(
                    "V_TEXT", "text", TextGeometry("30", Point(18, 64), 2, width=2),
                    page=1,
                ),
            ],
        )
        interpreter = DimensionInterpreter()
        drawing = interpreter.resolve(
            interpreter.analyze(SemanticClassifier().classify(drawing))
        )

        native = [
            entity for entity in drawing.entities
            if isinstance(entity.geometry, DimensionGeometry)
        ]
        self.assertEqual(len(native), 2)
        self.assertEqual({entity.geometry.value for entity in native}, {30.0, 100.0})
        self.assertEqual(
            next(entity for entity in drawing.entities if entity.id == "H_DIM").metadata[
                "dimension_extension_graphics"
            ],
            ["DIMENSION_GRAPHIC_002"],
        )
        self.assertEqual(
            next(entity for entity in drawing.entities if entity.id == "V_DIM").metadata[
                "dimension_extension_graphics"
            ],
            ["DIMENSION_GRAPHIC_001"],
        )
        self.assertTrue(
            next(entity for entity in drawing.entities if entity.id == "SHARED").metadata[
                "suppress_cad_export"
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "shared-dimensions.dxf"
            DxfExporter().export(build_cad_model(drawing), target)
            document = ezdxf.readfile(target)
            self.assertFalse(document.audit().has_errors)
            self.assertEqual(len(document.modelspace().query("DIMENSION")), 2)
            self.assertEqual(len(document.modelspace().query("CIRCLE LINE TEXT")), 0)

    def test_one_scaled_dimension_does_not_confirm_view_scale(self) -> None:
        marker_metadata = {
            "reconstruction": "circle_from_lines",
            "source_entities": [f"S{index}" for index in range(20)],
        }
        drawing = Drawing(
            "mm", sheets=[Sheet("SHEET_001", 1, (0, 0, 200, 200))],
            entities=[
                Entity(
                    "M1", "circle", CircleGeometry(Point(20, 50), 0.5),
                    page=1, metadata=dict(marker_metadata),
                ),
                Entity(
                    "M2", "circle", CircleGeometry(Point(120, 50), 0.5),
                    page=1, metadata=dict(marker_metadata),
                ),
                Entity(
                    "DIM", "line", LineGeometry(Point(20.5, 50), Point(119.5, 50)),
                    page=1,
                ),
                Entity(
                    "EXT1", "line", LineGeometry(Point(20, 50.5), Point(20, 70)),
                    page=1,
                ),
                Entity(
                    "EXT2", "line", LineGeometry(Point(120, 50.5), Point(120, 70)),
                    page=1,
                ),
                Entity(
                    "TEXT", "text", TextGeometry("300", Point(67, 52), 2, width=6),
                    page=1,
                ),
                Entity(
                    "REF1", "text", TextGeometry("(100", Point(76, 52), 2, width=6),
                    page=1,
                ),
                Entity(
                    "REF2", "text", TextGeometry("200)", Point(85, 52), 2, width=6),
                    page=1,
                ),
            ],
        )
        interpreter = DimensionInterpreter()
        drawing = interpreter.analyze(SemanticClassifier().classify(drawing))
        for entity in drawing.entities:
            if entity.semantic_type != SemanticType.DIMENSION_TEXT:
                entity.view = "VIEW_001"
        drawing = interpreter.resolve(drawing)

        self.assertFalse(
            any(isinstance(entity.geometry, DimensionGeometry) for entity in drawing.entities)
        )
        analysis = drawing.metadata["dimension_analysis"]
        self.assertEqual(analysis["native_dimension_entities"], 0)
        self.assertEqual(analysis["view_measurement_scales"], {})
        self.assertEqual(
            analysis["dimension_text_roles"],
            {"primary": 1, "reference": 2, "ambiguous": 0},
        )
        text_roles = {
            entity.id: entity.metadata.get("dimension_text_role")
            for entity in drawing.entities
            if entity.id in {"TEXT", "REF1", "REF2"}
        }
        self.assertEqual(
            text_roles,
            {"TEXT": "primary", "REF1": "reference", "REF2": "reference"},
        )
        primary = next(entity for entity in drawing.entities if entity.id == "TEXT")
        self.assertEqual(primary.metadata["dimension_reference_text"], "(100 200)")
        self.assertEqual(
            analysis["unresolved_reasons"]["DIMENSION_GRAPHIC_001"],
            "view_scale_not_confirmed",
        )

    def test_tesseract_tsv_is_mapped_to_sheet_text_geometry(self) -> None:
        tsv = (
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
            "5\t1\t2\t1\t3\t1\t100\t50\t200\t25\t95.5\t460\n"
            "5\t1\t2\t1\t3\t2\t320\t50\t100\t25\t60.0\tignored\n"
        )
        adapter = TesseractOcrAdapter(
            TesseractOcrConfig(minimum_confidence=70)
        )

        entities = adapter.entities_from_tsv(
            tsv, page_number=1,
            sheet=Sheet("SHEET_001", 1, (10, 20, 210, 120)),
            image_width=1000, image_height=500,
        )

        self.assertEqual(len(entities), 1)
        entity = entities[0]
        self.assertEqual(entity.id, "OCR_P001_00001")
        self.assertEqual(entity.semantic_type, SemanticType.TEXT)
        self.assertEqual(entity.source.parser, "tesseract_ocr")
        self.assertEqual(entity.geometry.text, "460")
        self.assertEqual(entity.geometry.insertion, Point(30, 105))
        self.assertEqual(entity.geometry.width, 40)
        self.assertEqual(entity.geometry.height, 5)
        self.assertAlmostEqual(entity.confidence, 0.955)

    def test_invalid_ocr_options_are_rejected_before_conversion(self) -> None:
        with self.assertRaisesRegex(ConversionError, "OCR DPI"):
            convert_pdf(Path("unused.pdf"), Path("unused.dxf"), ocr=True, ocr_dpi=71)
        with self.assertRaisesRegex(ConversionError, "OCR minimum confidence"):
            convert_pdf(
                Path("unused.pdf"), Path("unused.dxf"),
                ocr=True, ocr_min_confidence=101,
            )

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
            self.assertEqual(modelspace.query("DIMENSION").first.dxf.text, "10")
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
