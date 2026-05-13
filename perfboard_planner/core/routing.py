from __future__ import annotations

from typing import List, Tuple

GridPoint = Tuple[int, int]


def simple_dogleg_route(start: GridPoint, end: GridPoint) -> List[GridPoint]:
    """A small deterministic route helper used by the UI.

    It intentionally does not claim to be full autorouting. It gives a clean
    editable starting point: start -> horizontal/vertical dogleg -> end.
    """
    sr, sc = start
    er, ec = end
    if sr == er or sc == ec:
        return [start, end]
    return [start, (sr, ec), end]
