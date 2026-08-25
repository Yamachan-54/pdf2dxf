from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from .geometry import Segment


_INVALID_LAYER_CHARS = re.compile(r'[<>/\\":;?*|=`]')


def safe_layer_name(name: str) -> str:
    cleaned = _INVALID_LAYER_CHARS.sub("_", name).strip()[:255]
    return cleaned or "0"


@dataclass(frozen=True)
class DxfLine:
    segment: Segment
    layer: str
    true_color: int | None = None


def rgb_to_true_color(rgb: tuple[float, float, float] | None) -> int | None:
    if rgb is None:
        return None
    channels = [max(0, min(255, round(value * 255))) for value in rgb]
    return (channels[0] << 16) | (channels[1] << 8) | channels[2]


def _pair(code: int, value: object) -> str:
    return f"{code}\n{value}\n"


def write_ascii_dxf(path: Path, lines: list[DxfLine], units_code: int) -> None:
    """Write a dependency-free ASCII DXF (AutoCAD R2000 / AC1015)."""
    layers = sorted({safe_layer_name(line.layer) for line in lines} | {"0"})
    chunks = [
        _pair(0, "SECTION"),
        _pair(2, "HEADER"),
        _pair(9, "$ACADVER"),
        _pair(1, "AC1015"),
        _pair(9, "$INSUNITS"),
        _pair(70, units_code),
        _pair(0, "ENDSEC"),
        _pair(0, "SECTION"),
        _pair(2, "TABLES"),
        _pair(0, "TABLE"),
        _pair(2, "LAYER"),
        _pair(70, len(layers)),
    ]
    for layer in layers:
        chunks.extend(
            [
                _pair(0, "LAYER"),
                _pair(2, layer),
                _pair(70, 0),
                _pair(62, 7),
                _pair(6, "CONTINUOUS"),
            ]
        )
    chunks.extend(
        [
            _pair(0, "ENDTAB"),
            _pair(0, "ENDSEC"),
            _pair(0, "SECTION"),
            _pair(2, "ENTITIES"),
        ]
    )
    for line in lines:
        start, end = line.segment.start, line.segment.end
        chunks.extend(
            [
                _pair(0, "LINE"),
                _pair(100, "AcDbEntity"),
                _pair(8, safe_layer_name(line.layer)),
            ]
        )
        if line.true_color is not None:
            chunks.append(_pair(420, line.true_color))
        chunks.extend(
            [
                _pair(100, "AcDbLine"),
                _pair(10, _number(start.x)),
                _pair(20, _number(start.y)),
                _pair(30, 0),
                _pair(11, _number(end.x)),
                _pair(21, _number(end.y)),
                _pair(31, 0),
            ]
        )
    chunks.extend([_pair(0, "ENDSEC"), _pair(0, "EOF")])
    path.write_text("".join(chunks), encoding="ascii", newline="\n")


def _number(value: float) -> str:
    if abs(value) < 1e-10:
        value = 0.0
    return f"{value:.8f}".rstrip("0").rstrip(".")

