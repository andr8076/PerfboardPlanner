from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple


Side = str  # "front" or "back"
GridPoint = Tuple[int, int]


@dataclass
class ComponentPin:
    name: str
    row: int
    col: int


@dataclass
class ComponentJumper:
    pin_a: str
    pin_b: str
    color: str = "#00aaff"


@dataclass
class Component:
    name: str
    row: int
    col: int
    width: int
    height: int
    color: str
    side: Side = "front"
    rotation: int = 0  # visual body angle in degrees
    show_name: bool = True
    show_pin_names: bool = True
    pins: List[ComponentPin] = field(default_factory=list)
    jumpers: List[ComponentJumper] = field(default_factory=list)

    # Reserved fields for the next UI/data pass. They are harmless defaults for
    # older layouts and let the model grow without more JSON migrations.
    component_type: str = "generic"
    value: str = ""
    category: str = "Custom"
    orientation_note: str = ""
    locked: bool = False
    group: str = ""


@dataclass
class Wire:
    name: str
    points: List[GridPoint]
    color: str
    side: Side = "front"

    # Kept only for loading old files. The modern UI uses automatic visual wire
    # separation and color visibility instead of manual layers/lanes.
    layer: str = "main"
    lane: int = 0

    locked: bool = False
    group: str = ""


@dataclass
class Via:
    row: int
    col: int
    name: str = ""
    color: str = "#9c27b0"
    locked: bool = False
    group: str = ""


@dataclass
class Annotation:
    """Future replacement for the old fake-component text labels."""

    text: str
    row: int
    col: int
    side: Side = "front"
    color: str = "#111111"
    group: str = ""
    locked: bool = False


@dataclass
class KeepoutZone:
    """Reserved model for mechanical no-go areas."""

    name: str
    row1: int
    col1: int
    row2: int
    col2: int
    side: str = "both"  # front, back, or both
    color: str = "#ef4444"
    locked: bool = False
    group: str = ""


@dataclass
class BoardProject:
    title: str = "Untitled perfboard"
    author: str = ""
    revision: str = ""
    notes: str = ""
    todo: str = ""
    changelog: str = ""


@dataclass
class Layout:
    rows: int = 30
    cols: int = 45
    spacing: int = 22
    components: List[Component] = field(default_factory=list)
    wires: List[Wire] = field(default_factory=list)
    vias: List[Via] = field(default_factory=list)
    annotations: List[Annotation] = field(default_factory=list)
    keepouts: List[KeepoutZone] = field(default_factory=list)
    project: BoardProject = field(default_factory=BoardProject)
    schema_version: int = 11

    def board_dict(self) -> Dict[str, int]:
        return {"rows": int(self.rows), "cols": int(self.cols), "spacing": int(self.spacing)}
