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
- 任意のTesseract OCR Adapterでアウトライン文字から編集可能なTEXTを追加
- 寸法図形近傍のOCR数値・変数を `dimension_text` としてDIMENSIONへ分離
- 複数の寸法OCR文字を主寸法・括弧内参考値・曖昧文字へ保守的に分類
- `□`を先頭の`0`と誤認した正方形寸法を正規化し、低信頼度候補は行単位OCRで再試行
- 直交する寸法線と補助線の共有をグループ別roleとして保持し、共有端点寸法を解決
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
- 任意指定されたTesseract OCRバンドルを専用インストール先へ同梱

完了後、新しいコマンドプロンプトまたはPowerShellを開いて実行します。

```powershell
pdf2dxf input.pdf output.dxf
```

PowerShellからインストール先を指定する場合は次のように実行できます。

```powershell
powershell -ExecutionPolicy Bypass -File .\install-windows.ps1 -InstallDir "D:\Tools\pdf2dxf"
```

PATHへ追加しない場合は `-NoPath` を指定します。

日本語OCRも自己完結させる場合は、64-bit Windows用Tesseract一式を用意し、
`tesseract.exe` と `tessdata\eng.traineddata`、`tessdata\jpn.traineddata` を含む
フォルダーを指定します。インストーラーは実行と言語データを検証してから組み込みます。

```powershell
powershell -ExecutionPolicy Bypass -File .\install-windows.ps1 -OcrBundleDir ".\tesseract-portable"
pdf2dxf input.pdf output.dxf --ocr --ocr-language jpn+eng
```

同じ `InstallDir` を更新する場合、既に組み込まれたOCRバンドルは自動的に引き継がれます。
OCRを使わない場合は従来どおり追加指定なしでインストールできます。

アンインストールは次のコマンドです。

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

# ネイティブ文字のないページから数値・英字をOCR（Tesseractが必要）
pdf2dxf drawing.pdf output.dxf --ocr --ocr-language eng --debug debug

# 日本語言語データを導入済みの場合
pdf2dxf drawing.pdf output.dxf --ocr --ocr-language jpn+eng
```

主なオプションは `pdf2dxf --help` で確認できます。既定の出力単位はmmで、PDFの1 ptを正確に `25.4 / 72 mm` として換算します。複数ページは既定で横に並びます。ページ番号とViewはDrawing IR属性であり、DXFレイヤーとは分離されています。

OCRは明示的に `--ocr` を指定した場合だけ実行します。既定は300dpi、採用信頼度70で、
ネイティブ文字が存在するページは重複防止のためスキップします。`eng`だけでも数字・英字の
寸法を取得できますが、日本語全文にはTesseractの`jpn`言語データが必要です。
実行ファイルがPATHにない場合は`--tesseract-command`でパスを指定できます。
寸法近傍で`0`に続く3桁以上の値が得られた場合は、元OCR文字列をmetadataへ保持したまま
正方形寸法記号`□`へ正規化します。低信頼度候補は該当行だけをPSM 7で再認識し、
明確な数値が1つ得られた場合だけ追加します。

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
- `ocr_entities.json`: OCR文字、座標、サイズ、信頼度、元ピクセルbbox（OCR実行時）

ラスター検出用のページ画像や検出オーバーレイは、Raster Parser実装時に同じディレクトリへ追加する予定です。

## 制限事項

- OCRは文字抽出に対応しましたが、線画そのものを画像から復元するRaster Geometry Parserは未実装です。
- アウトライン化された文字は`--ocr`指定時に追加TEXTとして取得できます。元の文字輪郭は証拠保持のため残るので、現段階では見た目が重複する場合があります。
- 塗りつぶし、線種、線幅、クリッピングマスクはDXFへ保持せず、輪郭線を出力します。
- 意味分類は保守的な初期ルールです。投影図種別、寸法解釈、View間Feature対応、Constraint Solverは今後のPhaseです。
- 分割線からの中心線認識は、軸平行・等間隔・長短交互の明確なパターンだけを対象とします。斜め中心線など不確実なものは形状線のまま残します。
- 両側の定義点と単一の数値文字が揃った線形寸法をネイティブ `DIMENSION`へ変換します。等倍寸法は単独で、縮尺寸法は同一View内の独立した2寸法以上が同じ標準倍率を1%以内で支持した場合だけ昇格します。
- 推定倍率は中立なCAD Model属性として保持し、DXF Exporter境界でezdxfの`DIMLFAC`へ変換します。情報不足・縮尺不明の寸法はDrawing IRの`dimension_analysis.unresolved_reasons`へ理由を記録します。現在の自動縮尺推定は主要用途のmm出力に限定しています。
- 文字値を復元できない寸法図形でも、同径小円のペア、点間の線、直交する補助線をそれぞれ `dimension_marker`、`dimension_line`、`dimension_extension_line` としてDrawing IRに保持し、`DIMENSION` Layerへ分離します。この段階では推測したネイティブ `DIMENSION` Entityにはしません。
- OCR文字が寸法図形の近傍にあり、数値・直径記号・`W2`等の限定パターンに一致する場合は`dimension_text`へ分類します。同一基線上で主寸法の後に完全な括弧対が並ぶ場合だけ`primary`と`reference`へ分け、それ以外の複数トークンは`ambiguous`として誤結合しません。
- `□`補正は寸法図形の近傍にある`0`＋3桁以上のOCR文字だけを対象とします。行再OCRでも数値、小数点、信頼度の条件を満たさない場合は推測補正しません。
- 1本の線が別の寸法では寸法線、別の寸法では補助線になる場合、`dimension_line_graphics`と`dimension_extension_graphics`を別々に保持します。共有Entityは、それを利用する全グループが解決した場合だけ元のグラフィック出力を抑制します。
- Git不要Windowsインストーラーは、利用者が指定した64-bit Tesseractバンドルを検証して自己完結インストールへ取り込めます。Tesseract配布物自体はリポジトリに同梱しません。
- HATCH、BLOCK、INSERTのIR/CAD Modelは未実装です。ExporterのEntity Handler登録へ追加できる構造です。
- 円/円弧フィッティングが閾値を満たさないベジェは、誤認識を避けて `LWPOLYLINE` として残します。

## テスト

外部テストランナーなしで実行できます。

```bash
python -m unittest discover -s tests -v
```

テストでは生成した電子PDFを使い、LINE、CIRCLE、ARC、TEXT、MTEXT、DIMENSION、
意味レイヤー、CENTER/HIDDEN LineType、mm/inch/pt単位、図面枠/表題欄分離、
回転ページ座標、ゼロ長線除外、分割中心線、寸法図形と加工形状の分離、View縮尺付きネイティブ寸法昇格、共有寸法線・補助線、OCR座標変換・信頼度フィルター、寸法文字役割分類、高密度View分離、
Drawing IR JSON、Debug出力、既存CLI互換性を検証します。DXFはezdxfで再読込し、
監査とEntity単位のラウンドトリップ検証を行います。
