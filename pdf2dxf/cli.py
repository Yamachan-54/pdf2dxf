from __future__ import annotations

import argparse
from pathlib import Path
import sys

from . import __version__
from .converter import ConversionError, convert_pdf


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdf2dxf",
        description="PDFのベクター図形をASCII DXFへ変換します。",
    )
    parser.add_argument("input", type=Path, help="入力PDF")
    parser.add_argument("output", type=Path, nargs="?", help="出力DXF（省略時は入力名.dxf）")
    parser.add_argument("--pages", help="対象ページ。例: 1,3-5（省略時は全ページ）")
    parser.add_argument("--unit", choices=("mm", "inch", "pt"), default="mm")
    parser.add_argument("--scale", type=float, default=1.0, help="追加の倍率（既定: 1）")
    parser.add_argument(
        "--layout",
        choices=("horizontal", "vertical", "overlay"),
        default="horizontal",
        help="複数ページの配置（既定: horizontal）",
    )
    parser.add_argument("--page-gap", type=float, default=10.0, help="ページ間隔（出力単位）")
    parser.add_argument("--curve-steps", type=int, help="ベジェ曲線1本あたりの分割数")
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def parse_pages(spec: str | None) -> list[int] | None:
    if not spec:
        return None
    result: set[int] = set()
    try:
        for part in spec.split(","):
            bounds = part.strip().split("-")
            if len(bounds) == 1:
                values = [int(bounds[0])]
            elif len(bounds) == 2:
                start, end = map(int, bounds)
                if start > end:
                    raise ValueError
                values = range(start, end + 1)
            else:
                raise ValueError
            if any(value < 1 for value in values):
                raise ValueError
            result.update(value - 1 for value in values)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("ページ指定は 1,3-5 の形式で入力してください") from exc
    return sorted(result)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    source: Path = args.input
    destination: Path = args.output or source.with_suffix(".dxf")
    if not source.is_file():
        parser.error(f"入力ファイルがありません: {source}")
    try:
        selected_pages = parse_pages(args.pages)
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))

    try:
        result = convert_pdf(
            source,
            destination,
            pages=selected_pages,
            unit=args.unit,
            scale=args.scale,
            layout=args.layout,
            page_gap=args.page_gap,
            curve_steps=args.curve_steps,
        )
    except ConversionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"{destination}: {result.pages}ページ、{result.entities}エンティティを変換しました")
    if result.empty_pages:
        pages = ", ".join(map(str, result.empty_pages))
        print(
            f"warning: ページ {pages} にベクター図形がありません（スキャンPDFの可能性があります）",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

