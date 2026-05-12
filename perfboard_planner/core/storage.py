from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Sequence

from .geometry import normalized_angle
from .models import (
    Annotation,
    BoardProject,
    Component,
    ComponentJumper,
    ComponentPin,
    KeepoutZone,
    Layout,
    Via,
    Wire,
)

CURRENT_SCHEMA_VERSION = 11


def _as_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off"}
    return bool(value)


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except Exception:
        return default


def normalize_pins(pins: Sequence[ComponentPin], width: int, height: int) -> List[ComponentPin]:
    """Return pins with stable names. External pins are allowed."""
    out: List[ComponentPin] = []
    used: set[str] = set()
    for i, pin in enumerate(pins, start=1):
        name = str(pin.name or f"P{i}").strip() or f"P{i}"
        base = name
        suffix = 2
        while name in used:
            name = f"{base}_{suffix}"
            suffix += 1
        used.add(name)
        out.append(ComponentPin(name, _as_int(pin.row), _as_int(pin.col)))
    return out


def normalize_jumpers(jumpers: Sequence[ComponentJumper], pins: Sequence[ComponentPin]) -> List[ComponentJumper]:
    pin_names = {p.name for p in pins}
    out: List[ComponentJumper] = []
    seen: set[tuple[str, str]] = set()
    for j in jumpers:
        a = str(j.pin_a or "")
        b = str(j.pin_b or "")
        if not a or not b or a == b or a not in pin_names or b not in pin_names:
            continue
        key = tuple(sorted((a, b)))
        if key in seen:
            continue
        seen.add(key)
        out.append(ComponentJumper(a, b, str(j.color or "#00aaff")))
    return out


def component_from_dict(data: Mapping[str, Any]) -> Component:
    width = max(1, _as_int(data.get("width"), 1))
    height = max(1, _as_int(data.get("height"), 1))
    pins = normalize_pins(
        [ComponentPin(str(p.get("name", "")), _as_int(p.get("row")), _as_int(p.get("col"))) for p in data.get("pins", [])],
        width,
        height,
    )
    jumpers = normalize_jumpers(
        [ComponentJumper(str(j.get("pin_a", "")), str(j.get("pin_b", "")), str(j.get("color", "#00aaff"))) for j in data.get("jumpers", [])],
        pins,
    )
    return Component(
        str(data.get("name", "Part")),
        _as_int(data.get("row")),
        _as_int(data.get("col")),
        width,
        height,
        str(data.get("color", "#ffcc66")),
        side=str(data.get("side", "front")) if str(data.get("side", "front")) in {"front", "back"} else "front",
        rotation=normalized_angle(data.get("rotation", 0)),
        show_name=_as_bool(data.get("show_name"), True),
        show_pin_names=_as_bool(data.get("show_pin_names"), True),
        pins=pins,
        jumpers=jumpers,
        component_type=str(data.get("component_type", data.get("type", "generic")) or "generic"),
        value=str(data.get("value", "") or ""),
        category=str(data.get("category", "Custom") or "Custom"),
        orientation_note=str(data.get("orientation_note", "") or ""),
        locked=_as_bool(data.get("locked"), False),
        group=str(data.get("group", "") or ""),
    )


def wire_from_dict(data: Mapping[str, Any]) -> Wire:
    points = []
    for p in data.get("points", []):
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            points.append((_as_int(p[0]), _as_int(p[1])))
    return Wire(
        str(data.get("name", "") or ""),
        points,
        str(data.get("color", "#d00000") or "#d00000"),
        side=str(data.get("side", "front")) if str(data.get("side", "front")) in {"front", "back"} else "front",
        layer="main",  # obsolete field normalized away
        lane=0,        # obsolete field normalized away
        locked=_as_bool(data.get("locked"), False),
        group=str(data.get("group", "") or ""),
    )


def via_from_dict(data: Mapping[str, Any]) -> Via:
    return Via(
        _as_int(data.get("row")),
        _as_int(data.get("col")),
        str(data.get("name", "") or ""),
        str(data.get("color", "#9c27b0") or "#9c27b0"),
        locked=_as_bool(data.get("locked"), False),
        group=str(data.get("group", "") or ""),
    )


def annotation_from_dict(data: Mapping[str, Any]) -> Annotation:
    return Annotation(
        str(data.get("text", "") or ""),
        _as_int(data.get("row")),
        _as_int(data.get("col")),
        side=str(data.get("side", "front")) if str(data.get("side", "front")) in {"front", "back"} else "front",
        color=str(data.get("color", "#111111") or "#111111"),
        group=str(data.get("group", "") or ""),
        locked=_as_bool(data.get("locked"), False),
    )


def keepout_from_dict(data: Mapping[str, Any]) -> KeepoutZone:
    side = str(data.get("side", "both") or "both")
    if side not in {"front", "back", "both"}:
        side = "both"
    return KeepoutZone(
        str(data.get("name", "Keepout") or "Keepout"),
        _as_int(data.get("row1")),
        _as_int(data.get("col1")),
        _as_int(data.get("row2")),
        _as_int(data.get("col2")),
        side=side,
        color=str(data.get("color", "#ef4444") or "#ef4444"),
        locked=_as_bool(data.get("locked"), False),
        group=str(data.get("group", "") or ""),
    )


def project_from_dict(data: Mapping[str, Any] | None) -> BoardProject:
    data = data or {}
    return BoardProject(
        title=str(data.get("title", "Untitled perfboard") or "Untitled perfboard"),
        author=str(data.get("author", "") or ""),
        revision=str(data.get("revision", "") or ""),
        notes=str(data.get("notes", "") or ""),
        todo=str(data.get("todo", "") or ""),
        changelog=str(data.get("changelog", "") or ""),
    )


def layout_from_dict(data: Mapping[str, Any]) -> Layout:
    board = data.get("board", {}) if isinstance(data.get("board", {}), Mapping) else {}
    return Layout(
        rows=max(1, _as_int(board.get("rows"), _as_int(data.get("rows"), 30))),
        cols=max(1, _as_int(board.get("cols"), _as_int(data.get("cols"), 45))),
        spacing=max(1, _as_int(board.get("spacing"), _as_int(data.get("spacing"), 22))),
        components=[component_from_dict(c) for c in data.get("components", []) if isinstance(c, Mapping)],
        wires=[wire_from_dict(w) for w in data.get("wires", []) if isinstance(w, Mapping)],
        vias=[via_from_dict(v) for v in data.get("vias", []) if isinstance(v, Mapping)],
        annotations=[annotation_from_dict(a) for a in data.get("annotations", []) if isinstance(a, Mapping)],
        keepouts=[keepout_from_dict(k) for k in data.get("keepouts", []) if isinstance(k, Mapping)],
        project=project_from_dict(data.get("project") if isinstance(data.get("project"), Mapping) else None),
        schema_version=_as_int(data.get("schema_version", data.get("version")), CURRENT_SCHEMA_VERSION),
    )


def _drop_obsolete_wire_fields(wire_dict: Dict[str, Any]) -> Dict[str, Any]:
    # New files do not save the old manual layer/lane UI state.
    wire_dict.pop("layer", None)
    wire_dict.pop("lane", None)
    return wire_dict


def layout_to_dict(layout: Layout, *, compact: bool = False) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "app_version": "v36",
        "board": layout.board_dict(),
        "project": asdict(layout.project),
        "components": [asdict(c) for c in layout.components],
        "wires": [_drop_obsolete_wire_fields(asdict(w)) for w in layout.wires],
        "vias": [asdict(v) for v in layout.vias],
    }
    if layout.annotations:
        data["annotations"] = [asdict(a) for a in layout.annotations]
    if layout.keepouts:
        data["keepouts"] = [asdict(k) for k in layout.keepouts]
    return data


def layout_from_app(rows: int, cols: int, spacing: int, components, wires, vias, project: BoardProject | None = None) -> Layout:
    return Layout(int(rows), int(cols), int(spacing), list(components), list(wires), list(vias), project=project or BoardProject())


def save_layout_file(path: str | Path, layout: Layout) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(layout_to_dict(layout), f, indent=2)


def load_layout_file(path: str | Path) -> Layout:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return layout_from_dict(data)
