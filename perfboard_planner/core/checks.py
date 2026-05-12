from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence, Tuple

from .geometry import board_contains, component_pin_absolute
from .models import Component, Via, Wire


@dataclass(frozen=True)
class LayoutWarning:
    code: str
    message: str
    side: str = "both"
    row: int | None = None
    col: int | None = None
    item_kind: str = ""
    item_index: int | None = None


def basic_layout_warnings(rows: int, cols: int, components: Sequence[Component], wires: Sequence[Wire], vias: Sequence[Via]) -> List[LayoutWarning]:
    warnings: List[LayoutWarning] = []
    for ci, comp in enumerate(components):
        for pin in comp.pins:
            r, c = component_pin_absolute(comp, pin)
            if not board_contains(r, c, rows, cols):
                warnings.append(LayoutWarning("pin-outside-board", f"{comp.name}.{pin.name} is outside the board.", comp.side, r, c, "component", ci))
    for wi, wire in enumerate(wires):
        for r, c in wire.points:
            if not board_contains(r, c, rows, cols):
                warnings.append(LayoutWarning("wire-point-outside-board", f"A wire point is outside the board at row {r + 1}, col {c + 1}.", wire.side, r, c, "wire", wi))
    for vi, via in enumerate(vias):
        if not board_contains(via.row, via.col, rows, cols):
            warnings.append(LayoutWarning("via-outside-board", f"Via is outside the board at row {via.row + 1}, col {via.col + 1}.", "both", via.row, via.col, "via", vi))
    return warnings
