from __future__ import annotations

from collections import defaultdict, deque
from typing import Dict, Iterable, List, Mapping, Sequence, Set, Tuple

from .geometry import component_pin_absolute
from .models import Component, Via, Wire

Node = Tuple[str, str, int, int]  # side, kind, row, col. kind is normally "hole".


def explicit_wire_nodes(wire: Wire) -> List[Node]:
    """Return only explicit endpoints/bends, not every hole a wire visually passes.

    This matches the visual rule: a wire crossing a hole is not a connection unless
    the user intentionally put a point there.
    """
    return [(wire.side, "hole", int(r), int(c)) for r, c in wire.points]


def build_graph(components: Sequence[Component], wires: Sequence[Wire], vias: Sequence[Via]) -> Dict[Node, Set[Node]]:
    graph: Dict[Node, Set[Node]] = defaultdict(set)

    def link(a: Node, b: Node) -> None:
        graph[a].add(b)
        graph[b].add(a)

    for wire in wires:
        nodes = explicit_wire_nodes(wire)
        for a, b in zip(nodes, nodes[1:]):
            link(a, b)
        for node in nodes:
            graph.setdefault(node, set())

    for via in vias:
        link(("front", "hole", via.row, via.col), ("back", "hole", via.row, via.col))

    for comp in components:
        pin_by_name = {pin.name: pin for pin in comp.pins}
        for jumper in comp.jumpers:
            pa = pin_by_name.get(jumper.pin_a)
            pb = pin_by_name.get(jumper.pin_b)
            if pa is None or pb is None:
                continue
            ar, ac = component_pin_absolute(comp, pa)
            br, bc = component_pin_absolute(comp, pb)
            link((comp.side, "hole", ar, ac), (comp.side, "hole", br, bc))

    return graph


def connected_nodes(graph: Mapping[Node, Set[Node]], start: Node) -> Set[Node]:
    seen: Set[Node] = set()
    queue: deque[Node] = deque([start])
    while queue:
        node = queue.popleft()
        if node in seen:
            continue
        seen.add(node)
        queue.extend(n for n in graph.get(node, set()) if n not in seen)
    return seen
