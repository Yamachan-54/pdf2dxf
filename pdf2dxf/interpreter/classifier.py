from __future__ import annotations

import re

from ..ir.drawing import Drawing
from ..ir.entities import SemanticType, TextGeometry


class SemanticClassifier:
    """Conservative rule baseline; uncertain evidence remains low-confidence."""

    def classify(self, drawing: Drawing) -> Drawing:
        for entity in drawing.entities:
            if entity.semantic_type != SemanticType.UNKNOWN:
                continue
            if isinstance(entity.geometry, TextGeometry):
                entity.semantic_type = SemanticType.TEXT
                entity.confidence = 1.0
            elif entity.primitive in {"line", "polyline", "arc"}:
                dash = (entity.style.dash_pattern or "").strip()
                if dash and dash not in {"[] 0", "[]0"}:
                    if _is_center_pattern(dash):
                        entity.semantic_type = SemanticType.CENTER_LINE
                        entity.confidence = 0.8
                    else:
                        entity.semantic_type = SemanticType.HIDDEN_LINE
                        entity.confidence = 0.7
                else:
                    entity.semantic_type = SemanticType.OUTER_CONTOUR
                    entity.confidence = min(entity.confidence, 0.6)
            elif entity.primitive == "circle":
                entity.semantic_type = SemanticType.HOLE
                entity.confidence = max(entity.confidence, 0.8)
        return drawing


def _is_center_pattern(dash_pattern: str) -> bool:
    match = re.search(r"\[([^]]+)\]", dash_pattern)
    if match is None:
        return False
    values = [abs(float(value)) for value in re.findall(r"[-+]?\d*\.?\d+", match.group(1))]
    if len(values) < 4 or min(values) <= 0:
        return False
    longest = max(values)
    shortest = min(values)
    return longest / shortest >= 3.0 and values.index(longest) % 2 == 0
