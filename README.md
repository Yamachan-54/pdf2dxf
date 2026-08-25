# pdf2dxf

PDFに含まれるベクター図形を、CADで読み込めるASCII DXFへ変換するコマンドラインツールです。
線、矩形、四辺形、3次ベジェ曲線に対応し、ページごとにDXFレイヤーを作ります。

## Windowsへのインストール（Python不要）

64-bit版Windows 10/11に対応しています。プロジェクト一式をZIPから展開し、
`install-windows.cmd` をダブルクリックしてください。管理者権限は不要です。

インストーラーは次の処理を自動で行います。

- 専用の組み込みPythonを `%LOCALAPPDATA%\Programs\pdf2dxf` に配置
- PyMuPDFをダウンロードしてSHA-256を検証
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

インストール時にPython本体とPyMuPDFを取得するため、インターネット接続が必要です。

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

# 曲線を固定精度でポリライン化
pdf2dxf drawing.pdf --curve-steps 48
```

主なオプションは `pdf2dxf --help` で確認できます。既定の出力単位はmmで、PDFの1 ptを正確に `25.4 / 72 mm` として換算します。複数ページは既定で横に並び、各ページは `PDF_PAGE_1` のようなレイヤーに分かれます。

## 制限事項

- PDF内のベクターパスを変換します。スキャン画像や写真の輪郭抽出（ラスターの自動トレース）は行いません。
- 文字はDXF文字へ変換しません。文字がPDF内でアウトライン化されていればパスとして変換されます。
- 塗りつぶし、線種、線幅、クリッピングマスクはDXFへ保持せず、輪郭線を出力します。
- ベジェ曲線はCAD互換性を優先して短いLINEエンティティへ近似します。

## テスト

外部テストランナーなしで実行できます。

```bash
python -m unittest discover -s tests -v
```
