from __future__ import annotations

from collections.abc import Iterable


def _as_set(values: Iterable[str] | None) -> set[str]:
    return set(values or ())


def is_selectable_item(
    kind: str,
    item: object,
    *,
    current_side: str,
    hidden_groups: Iterable[str] | None = None,
    hidden_wire_colors: Iterable[str] | None = None,
    show_other_parts: bool = True,
    show_other_wires: bool = True,
    show_current_keepouts: bool = True,
    show_other_keepouts: bool = True,
    include_ghosts: bool = False,
) -> bool:
    """Return whether an item should be included in bulk selection.

    Bulk selection should match what the user can actually act on from the
    current board view: hidden groups and hidden wire colors are skipped, and
    opposite-side ghost overlays are only considered selectable when explicitly
    requested.
    """
    hidden_group_names = _as_set(hidden_groups)
    group = getattr(item, "group", "")
    if group and group in hidden_group_names:
        return False

    if kind == "wire" and getattr(item, "color", "") in _as_set(hidden_wire_colors):
        return False

    if kind == "via":
        return True

    side = getattr(item, "side", current_side)
    if kind == "component":
        return side == current_side or (include_ghosts and show_other_parts)
    if kind == "wire":
        return side == current_side or (include_ghosts and show_other_wires)
    if kind == "annotation":
        return side == current_side
    if kind == "keepout":
        applies_to_current = side == "both" or side == current_side
        if applies_to_current:
            return show_current_keepouts
        return include_ghosts and show_other_keepouts
    return True
