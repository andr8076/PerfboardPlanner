from __future__ import annotations

import math
from typing import Tuple

GridPoint = Tuple[int, int]
XY = Tuple[float, float]


def normalized_angle(value: object) -> int:
    try:
        return int(float(value)) % 360
    except Exception:
        return 0


def rotate_point_90_clockwise(row: int, col: int, width: int, height: int) -> GridPoint:
    """Rotate a point inside a width×height footprint around the top-left box."""
    return col, height - 1 - row


def display_col_for_side(col: int, cols: int, side: str) -> int:
    """The back side is physically mirrored by default."""
    if side == "back":
        return cols - 1 - col
    return col


def logical_col_from_display(display_col: int, cols: int, side: str) -> int:
    if side == "back":
        return cols - 1 - display_col
    return display_col


def component_pin_absolute(component, pin) -> GridPoint:
    return component.row + pin.row, component.col + pin.col


def board_contains(row: int, col: int, rows: int, cols: int) -> bool:
    return 0 <= row < rows and 0 <= col < cols


def distance_to_segment(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx = bx - ax
    dy = by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    cx = ax + t * dx
    cy = ay + t * dy
    return math.hypot(px - cx, py - cy)


def point_in_rect(row: int, col: int, row1: int, col1: int, row2: int, col2: int) -> bool:
    lo_r, hi_r = sorted((row1, row2))
    lo_c, hi_c = sorted((col1, col2))
    return lo_r <= row <= hi_r and lo_c <= col <= hi_c
