from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Mapping, Sequence, Tuple

from .geometry import board_contains, component_pin_absolute, point_in_rect
from .models import Component, KeepoutZone, Layout, Via, Wire

GridPoint = Tuple[int, int]


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


def _component_body_cells(component: Component) -> set[GridPoint]:
    return {
        (component.row + r, component.col + c)
        for r in range(max(1, component.height))
        for c in range(max(1, component.width))
    }


def pin_connection_counts(layout: Layout, *, count_both_sides: bool = True) -> Dict[Tuple[int, str], int]:
    """Return count per component pin keyed by (component_index, pin_name).

    Counts explicit wire endpoints/bends that land on the pin hole, matching the
    visual/electrical rule: a wire passing through a hole is not connected unless
    the point is explicit. Internal jumpers count as one connection for each pin.
    Vias count when they land on the pin hole; optionally they count across both
    sides.
    """
    by_hole: dict[tuple[str, int, int], int] = defaultdict(int)
    for wire in layout.wires:
        for r, c in wire.points:
            by_hole[(wire.side, r, c)] += 1
    for via in layout.vias:
        by_hole[("front", via.row, via.col)] += 1
        by_hole[("back", via.row, via.col)] += 1

    counts: Dict[Tuple[int, str], int] = {}
    for ci, comp in enumerate(layout.components):
        jumper_count: dict[str, int] = defaultdict(int)
        for jumper in comp.jumpers:
            jumper_count[jumper.pin_a] += 1
            jumper_count[jumper.pin_b] += 1
        for pin in comp.pins:
            r, c = component_pin_absolute(comp, pin)
            sides = ("front", "back") if count_both_sides else (comp.side,)
            counts[(ci, pin.name)] = jumper_count[pin.name] + sum(by_hole.get((side, r, c), 0) for side in sides)
    return counts


def layout_warnings(
    layout: Layout,
    *,
    max_pin_connections: int = 2,
    count_both_sides: bool = True,
) -> List[LayoutWarning]:
    warnings = basic_layout_warnings(layout.rows, layout.cols, layout.components, layout.wires, layout.vias)

    # Component body overlaps on the same side.
    occupied: dict[tuple[str, GridPoint], int] = {}
    for ci, comp in enumerate(layout.components):
        for cell in _component_body_cells(comp):
            key = (comp.side, cell)
            if key in occupied:
                other = occupied[key]
                warnings.append(
                    LayoutWarning(
                        "component-overlap",
                        f"{layout.components[other].name} overlaps {comp.name} on the {comp.side} side.",
                        comp.side,
                        cell[0],
                        cell[1],
                        "component",
                        ci,
                    )
                )
            else:
                occupied[key] = ci

    # Keepout violations: component bodies, wire explicit points, vias.
    for ki, zone in enumerate(layout.keepouts):
        zone_sides = {"front", "back"} if zone.side == "both" else {zone.side}
        for ci, comp in enumerate(layout.components):
            if comp.side not in zone_sides:
                continue
            for r, c in _component_body_cells(comp):
                if point_in_rect(r, c, zone.row1, zone.col1, zone.row2, zone.col2):
                    warnings.append(LayoutWarning("keepout-component", f"{comp.name} is inside keepout zone {zone.name}.", comp.side, r, c, "component", ci))
                    break
        for wi, wire in enumerate(layout.wires):
            if wire.side not in zone_sides:
                continue
            for r, c in wire.points:
                if point_in_rect(r, c, zone.row1, zone.col1, zone.row2, zone.col2):
                    warnings.append(LayoutWarning("keepout-wire", f"A wire enters keepout zone {zone.name}.", wire.side, r, c, "wire", wi))
                    break
        for vi, via in enumerate(layout.vias):
            if zone.side in {"both", "front", "back"} and point_in_rect(via.row, via.col, zone.row1, zone.col1, zone.row2, zone.col2):
                warnings.append(LayoutWarning("keepout-via", f"A via is inside keepout zone {zone.name}.", "both", via.row, via.col, "via", vi))

    # Pin connection limits.
    if max_pin_connections > 0:
        counts = pin_connection_counts(layout, count_both_sides=count_both_sides)
        for (ci, pin_name), count in counts.items():
            if count > max_pin_connections and 0 <= ci < len(layout.components):
                comp = layout.components[ci]
                pin = next((p for p in comp.pins if p.name == pin_name), None)
                if pin is None:
                    continue
                r, c = component_pin_absolute(comp, pin)
                warnings.append(
                    LayoutWarning(
                        "pin-connection-limit",
                        f"{comp.name}.{pin_name} has {count} connections. Max allowed is {max_pin_connections}.",
                        comp.side,
                        r,
                        c,
                        "component",
                        ci,
                    )
                )
    return warnings
