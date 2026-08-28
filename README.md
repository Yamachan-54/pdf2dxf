# pdf2dxf

PDF図面を解析し、CADで再編集できる構造化ASCII DXFへ変換するコマンドラインツールです。
電子PDFのネイティブ図形・文字をDrawing IRへ取り込み、解析、プリミティブ復元、意味分類、
CAD Model生成、DXF出力を独立した段階で処理します。
DXFのシリアライズ、Layer、LineType、Text Style、Dimension Style管理には
`ezdxf`を使用し、Drawing IRとCAD Modelはezdxfに依存しません。

現在は次に対応しています。

- 直線を `LINE`、閉じた円形ベジェを `CIRCLE`、円弧を `ARC` として復元
- 復元できない曲線を安全に `LWPOLYLINE` へフォールバック
- PDFネイティブ文字を `TEXT`（CAD Model/DXF Exporterは `MTEXT` にも対応）として保持
- 連続する同一直線上の短い線を1本の `LINE` へ統合
- PDFの `/Rotate` を正規化し、回転ページも表示上のSheet座標へ変換
- PDF側で分割された長線・短点の交互パターンを保守的に中心線として復元
- ペアになった小円端点と接続線から寸法図形を検出し、加工穴・加工線と分離
- ゼロ長 `LINE` を除外し、除外数をDrawing IRの再構成統計へ記録
- Drawing IR、Sheet、View、Feature、Dimension、Constraintの拡張可能なデータモデル
- 図面外枠・全高セパレータ付き表題欄を製品形状と分離
- 文字輪郭の多い高密度図面では主要形状をアンカーにXY分割してViewを検出
- 意味とViewを分離し、意味をDXFレイヤーへ出力時にマッピング
- 解決済みの線形寸法をネイティブ `DIMENSION` として出力
- `CadExporter`境界とCAD Entity単位のezdxf Handler

詳しい現状分析と設計は [docs/architecture.md](docs/architecture.md) を参照してください。

## Windowsへのインストール（Python不要）

64-bit版Windows 10/11に対応しています。プロジェクト一式をZIPから展開し、
`install-windows.cmd` をダブルクリックしてください。管理者権限は不要です。

インストーラーは次の処理を自動で行います。

- 専用の組み込みPythonを `%LOCALAPPDATA%\Programs\pdf2dxf` に配置
- PyMuPDF、ezdxfと必須依存をダウンロードしてSHA-256を検証
- `pdf2dxf` コマンドをユーザーPATHへ追加
- 既存のシステムPython環境には一切変更を加えない

完了後、新しいコマンドプロンプトまたはPowerShellを開いて実行します。

```powershell
pdf2dxf input.pdf output.dxf
```

PowerShellからインストール先を指定する場合は次のように実行できます。

```powershell
powershell -ExecutionPolicy Bypass -File .\install-windows.ps1 -InstallDir "D:\Tools\pdf2dxf"
```

PATHへ追加しない場合は `-NoPath` を指定します。アンインストールは次のコマンドです。

```powershell
powershell -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\Programs\pdf2dxf\uninstall-windows.ps1"
```

インストール時にPython本体、PyMuPDF、ezdxfと必須依存を取得するため、インターネット接続が必要です。

## Python環境がある場合のインストール

Python 3.10以上が必要です。

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .
```

## 使い方

```bash
pdf2dxf input.pdf output.dxf
```

出力名を省略すると、入力PDFと同じ場所に同名の `.dxf` を作成します。

```bash
# 1ページ目と3〜5ページ目だけを変換
pdf2dxf drawing.pdf --pages 1,3-5

# PDF上の寸法をインチで出力
pdf2dxf drawing.pdf --unit inch

# 全体を2倍し、ページを縦に並べる
pdf2dxf drawing.pdf --scale 2 --layout vertical --page-gap 20

# 曲線フィッティング/フォールバック時のサンプリング数を固定
pdf2dxf drawing.pdf --curve-steps 48

# 変換途中のDrawing IRをJSONで保存
pdf2dxf drawing.pdf output.dxf --dump-ir drawing-ir.json

# 抽出・再構築・意味解析のデバッグJSONを保存
pdf2dxf drawing.pdf output.dxf --debug debug
```

主なオプションは `pdf2dxf --help` で確認できます。既定の出力単位はmmで、PDFの1 ptを正確に `25.4 / 72 mm` として換算します。複数ページは既定で横に並びます。ページ番号とViewはDrawing IR属性であり、DXFレイヤーとは分離されています。

## DXFレイヤー

Drawing IRの `semantic_type` は、DXF出力時に次のレイヤーへマッピングされます。

| DXF Layer | 内容 |
| --- | --- |
| `GEOMETRY` | 外形、内形、穴、形状線 |
| `HIDDEN` | 隠れ線 |
| `CENTER` | 中心線 |
| `DIMENSION` | 寸法、寸法線、寸法補助線、寸法端点記号 |
| `TEXT` | 通常文字、注記 |
| `HATCH` | ハッチング |
| `REFERENCE` | 図面枠、表題欄、補助図形、未確定要素 |

図面枠と表題欄候補は削除せずDrawing IRに保持し、`GEOMETRY`には混在させません。

`CENTER` Layerには `CENTER` LineType、`HIDDEN` Layerには `HIDDEN` LineTypeを
定義します。個々のEntityは `BYLAYER`を使用します。線種作成に失敗した場合は
`Continuous`へ安全にフォールバックします。

DXF Versionは広い互換性とネイティブEntity対応のバランスからR2000
（`AC1015`）を既定値として一元管理しています。

## Debug出力

`--debug [DIR]` は現在の電子PDF処理について次を出力します。

- `extracted_ir.json`: PDFから抽出直後
- `reconstruction.json`: 円・円弧・連続線の再構築後
- `semantic_entities.json`: 意味、View、Confidenceの一覧
- `drawing_ir.json`: 最終Drawing IR
- `dxf_export.json`: IR ID、CAD型、DXF型、Layer、LineType、Handle、出力状態の対応

ラスター検出用のページ画像や検出オーバーレイは、Raster Parser実装時に同じディレクトリへ追加する予定です。

## 制限事項

- 現在は電子PDFのベクター/ネイティブ文字を対象とします。スキャン画像のOpenCV解析やOCRはまだ実装していません。
- アウトライン化された文字はネイティブ文字として取得できません。将来のOCR Adapterへ接続する構造のみ用意しています。
- 塗りつぶし、線種、線幅、クリッピングマスクはDXFへ保持せず、輪郭線を出力します。
- 意味分類は保守的な初期ルールです。投影図種別、寸法解釈、View間Feature対応、Constraint Solverは今後のPhaseです。
- 分割線からの中心線認識は、軸平行・等間隔・長短交互の明確なパターンだけを対象とします。斜め中心線など不確実なものは形状線のまま残します。
- 定義点と寸法線位置が揃った線形寸法だけをネイティブ `DIMENSION`へ変換します。情報不足の寸法は誤ったEntityを作らず、`dxf_export.json`へ `unresolved`として記録します。
- 文字値を復元できない寸法図形でも、同径小円のペア、点間の線、直交する補助線をそれぞれ `dimension_marker`、`dimension_line`、`dimension_extension_line` としてDrawing IRに保持し、`DIMENSION` Layerへ分離します。この段階では推測したネイティブ `DIMENSION` Entityにはしません。
- HATCH、BLOCK、INSERTのIR/CAD Modelは未実装です。ExporterのEntity Handler登録へ追加できる構造です。
- 円/円弧フィッティングが閾値を満たさないベジェは、誤認識を避けて `LWPOLYLINE` として残します。

## テスト

外部テストランナーなしで実行できます。

```bash
python -m unittest discover -s tests -v
```

テストでは生成した電子PDFを使い、LINE、CIRCLE、ARC、TEXT、MTEXT、DIMENSION、
意味レイヤー、CENTER/HIDDEN LineType、mm/inch/pt単位、図面枠/表題欄分離、
回転ページ座標、ゼロ長線除外、分割中心線、寸法図形と加工形状の分離、高密度View分離、
Drawing IR JSON、Debug出力、既存CLI互換性を検証します。DXFはezdxfで再読込し、
監査とEntity単位のラウンドトリップ検証を行います。
