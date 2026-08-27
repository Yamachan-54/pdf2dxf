# pdf2dxf Architecture and Implementation Plan

## 1. Current architecture

The original implementation had four main modules: `cli.py` parsed arguments,
`converter.py` opened PDFs and orchestrated conversion, `geometry.py` subdivided
cubic Beziers, and `dxf.py` wrote dependency-free ASCII DXF. The converter knew
both PyMuPDF's data model and the DXF-only `DxfLine` model.

## 2. Current conversion flow

The flow was `PDF -> page.get_drawings() -> Segment -> DxfLine -> ASCII DXF`.
Lines, rectangles and quadrilaterals became LINE entities. Every cubic Bezier was
sampled into LINE entities. PDF text was not read.

## 3. Problems

- There was no intermediate representation for source evidence or semantics.
- Circles and arcs lost their analytic geometry and became short lines.
- Native text was discarded; outlined text was indistinguishable from geometry.
- Page number was encoded as a DXF layer rather than modeled independently.
- Sheet borders, title blocks, views, features, dimensions and constraints had no
  representation.
- Extraction, reconstruction and export could not be tested independently.

## 4. Gap against the requested system

The original system was a vector tracer, not a drawing interpreter. It lacked
Drawing IR, a CAD model, meaningful DXF entities and layers, native text,
primitive reconstruction, sheet/view structure, confidence and debug artifacts.
Raster/OCR and constraint solving also had no extension points.

## 5. New architecture

The implemented electronic-PDF path is:

`VectorPdfParser -> Drawing IR -> SheetAnalyzer -> ViewDetector ->`
`PrimitiveReconstructor -> SemanticClassifier -> CAD Model -> DxfExporter`.

The parser preserves native lines, rectangles, quads, cubic Beziers and text in
one IR. Reconstruction changes IR entities, never PDF objects. The raster parser,
relation analyzer and constraint solver are interfaces/stubs that can later emit
or consume the same IR without changing the exporter.

## 6. Module structure

- `input/vector_pdf.py`: PyMuPDF adapter and source-coordinate normalization.
- `input/raster_pdf.py`: future raster/OCR parser interface.
- `ir/`: serializable drawing, sheet, view, entity, feature and constraint types.
- `sheet/analyzer.py`: conservative border and title-block candidates.
- `views/detector.py`: connected-component spatial clustering into view regions.
- `geometry/fitting.py`: line/circle/arc fitting helpers.
- `geometry/reconstruction.py`: safe primitive reconstruction.
- `interpreter/classifier.py`: semantic type to meaning, independent of DXF.
- `cad/model.py`: export-oriented analytic CAD entities.
- `exporters/dxf.py`: ezdxf adapter and semantic layer/resource mapping.
- `pipeline.py`: stage orchestration, JSON/debug output and page layout.
- `converter.py`: backward-compatible public API only.

## 7. Drawing IR design

`Drawing` owns sheets, views, entities, features, constraints, metadata and the
unit. Every entity has a stable id, primitive, semantic type, view id,
confidence, source evidence, page id, style and geometry. Geometry is represented
by typed dataclasses (`LineGeometry`, `CircleGeometry`, `ArcGeometry`,
`PolylineGeometry`, `TextGeometry`, `DimensionGeometry`, `BezierGeometry`) and is
JSON serializable. Unknown evidence remains `unknown` rather than being forced
into an unsafe classification.

## 8. CAD model design

The CAD model contains `CadLine`, `CadCircle`, `CadArc`, `CadPolyline` and
`CadText`. It deliberately contains no PyMuPDF values. It retains the originating
IR id, semantic type, view id, confidence, color and page so exporters other than
DXF can be added.

## 9. DXF entity design

The ezdxf exporter writes AutoCAD R2000 DXF entities: LINE, CIRCLE, ARC,
LWPOLYLINE, TEXT, MTEXT and resolved linear DIMENSION. It uses analytic
circle/arc geometry and only falls back to LWPOLYLINE when fitting is not
trustworthy. The CAD/export boundary is ready for HATCH, INSERT and BLOCK without
changing PDF parsing.

## 10. Layer design

IR semantic types are mapped at export time to `GEOMETRY`, `HIDDEN`, `CENTER`,
`DIMENSION`, `TEXT`, `HATCH`, and `REFERENCE`. View and page are separate IR
attributes. An exporter policy can later opt into view-prefixed layer names.
Sheet borders and title-block lines are retained in IR but exported to REFERENCE,
never GEOMETRY.

## 11. Phased implementation plan

1. Introduce Drawing IR, CAD model, exporter and compatibility facade.
2. Read native text and reconstruct lines, circles and arcs conservatively.
3. Add semantic classification and layer policy.
4. Detect sheet border/title-block candidates while retaining metadata/evidence.
5. Cluster drawing entities into unknown view regions.
6. Interpret dimensions and relations.
7. Add raster preprocessing, OpenCV detectors and OCR adapters.
8. Add an optional constraint solver behind the solver protocol.

Phases 1-5 are represented in the current architecture; Phases 1-4 have initial
electronic-PDF implementations. Dimension/raster/solver types and boundaries are
present but intentionally do not claim automatic interpretation.

## 12. Test plan

Unit tests cover page parsing, geometry sampling/fitting, IR serialization,
semantic layer mapping and each DXF entity encoder. Generated vector PDFs verify
native LINE, CIRCLE, ARC and TEXT reconstruction, border separation, view
assignment, `--dump-ir`, debug JSON and regression CLI behavior. A real tracked
PDF is used only as a smoke test because its content is not a stable unit fixture.

## 13. Dependencies

PyMuPDF is isolated in `VectorPdfParser` and supplies native vector paths and
text. ezdxf is isolated in `exporters/dxf.py` and owns DXF document resources,
entity creation, serialization and round-trip parsing. Both are required runtime
dependencies and are installed by the self-contained Windows installer. Future
optional adapters may use OpenCV for raster primitives, an OCR engine for text,
and Z3 or a numerical optimizer for constraints; none is required by the current
electronic-PDF path.

## 14. ezdxf migration analysis

### Current implementation

- Drawing IR (`ir/`) owns semantic and source-preserving geometry and has no DXF
  dependency.
- The CAD model (`cad/model.py`) contains analytic `CadLine`, `CadCircle`,
  `CadArc`, `CadPolyline`, and `CadText` values. It also has no DXF dependency.
- `pipeline.py` builds the CAD model and calls the exporter after all analysis.
- `exporters/dxf.py` is the normal exporter and currently serializes R2000
  (`AC1015`) ASCII tags itself.
- `dxf.py` is the pre-architecture LINE-only writer retained solely by an old
  compatibility test; it is not used by the conversion pipeline.
- Semantic-to-layer mapping exists only in the exporter, which is the correct
  dependency direction.
- `DimensionGeometry` exists in IR, but it contains semantic references rather
  than the definition points and dimension-line location needed to generate a
  trustworthy native DXF DIMENSION. HATCH and BLOCK/INSERT CAD models do not yet
  exist.

### Impact and migration plan

1. Keep IR and CAD model independent of ezdxf. Add a generic `CadExporter`
   protocol at the exporter boundary.
2. Add ezdxf as a required dependency and keep R2000 as the centralized default
   because it matches the existing output, supports the required entities,
   native DIMENSION/HATCH, lineweight and broad CAD compatibility.
3. Replace the implementation of `DxfExporter` with an ezdxf adapter. Split
   entity conversion into a registry of small handlers instead of a growing
   `isinstance` chain.
4. Create semantic layers and safe `CENTER`/`HIDDEN` linetype definitions with
   `CONTINUOUS` fallback. Define the standard text and dimension styles through
   ezdxf document resources.
5. Represent single-line and multiline text as distinct `CadText` and `CadMText`
   CAD entity types. Preserve alignment/style fields without importing ezdxf
   enums into the model.
6. Leave unresolved dimensions out of DXF rather than fabricate geometry. Record
   every skipped entity and every CAD-to-DXF mapping in exporter debug records.
7. Remove the obsolete ASCII writer after all old and new tests use ezdxf
   round-trip assertions. Keep the color conversion helper in a neutral module.
8. Update the Python-free Windows installer to install ezdxf and its required
   dependencies into the private runtime, then parse-check the PowerShell script.

### Implemented migration result

The normal path now uses `DxfExporter -> ezdxf -> DXF`. The generic
`CadExporter` protocol and export trace types live in `exporters/base.py`; only
`exporters/dxf.py` imports ezdxf. Entity handlers are registered per CAD type.
R2000 remains the centralized default and `$INSUNITS` comes from `CadModel.unit`.
The legacy ASCII writer was removed after round-trip tests replaced its final
compatibility test. Resolved linear dimensions can be emitted as native
DIMENSION; unresolved dimensions are omitted and recorded with a reason. HATCH,
BLOCK, and INSERT remain future CAD model additions.
