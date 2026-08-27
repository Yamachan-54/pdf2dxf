from __future__ import annotations


def rgb_to_true_color(rgb: tuple[float, float, float] | None) -> int | None:
    if rgb is None:
        return None
    channels = [max(0, min(255, round(value * 255))) for value in rgb]
    return (channels[0] << 16) | (channels[1] << 8) | channels[2]
