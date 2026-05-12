from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple

from .models import Component, Layout


@dataclass(frozen=True)
class BomRow:
    quantity: int
    category: str
    component_type: str
    value: str
    names: str


def bom_rows(layout: Layout) -> List[BomRow]:
    groups: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for comp in layout.components:
        category = comp.category or "Custom"
        component_type = comp.component_type or "generic"
        value = comp.value or ""
        groups[(category, component_type, value)].append(comp.name or "Part")
    rows = [BomRow(len(names), category, component_type, value, ", ".join(sorted(names))) for (category, component_type, value), names in groups.items()]
    rows.sort(key=lambda row: (row.category.lower(), row.component_type.lower(), row.value.lower(), row.names.lower()))
    return rows


def bom_csv(layout: Layout, separator: str = ";") -> str:
    lines = [separator.join(["Quantity", "Category", "Type", "Value", "Names"])]
    for row in bom_rows(layout):
        parts = [str(row.quantity), row.category, row.component_type, row.value, row.names]
        lines.append(separator.join(p.replace("\n", " ").replace(separator, ",") for p in parts))
    return "\n".join(lines) + "\n"
