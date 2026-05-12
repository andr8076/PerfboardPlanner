from __future__ import annotations

import heapq
import math
from typing import Dict, Iterable, Optional, Sequence, Tuple

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal, QTimer
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPainterPath, QPen, QBrush, QWheelEvent
from PySide6.QtWidgets import QWidget

from ...core.geometry import board_contains, component_pin_absolute, display_col_for_side, logical_col_from_display, distance_to_segment
from ...core.models import Annotation, Component, ComponentPin, KeepoutZone, Layout, Via, Wire
from ...core.routing import simple_dogleg_route

Selection = Tuple[str, int]
GridPoint = Tuple[int, int]


class BoardView(QWidget):
    selectionChanged = Signal()
    layoutChanged = Signal()
    beforeLayoutChange = Signal(str)
    statusMessage = Signal(str)
    annotationRequested = Signal(int, int)
    itemActivated = Signal(str, int)
    toolRequested = Signal(str)
    zoomChanged = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.layout_model = Layout()
        self.tool = "select"
        self.side = "front"
        self.zoom = 1.0
        self.pan = QPointF(28, 28)
        self.margin = 42.0
        self.selected: set[Selection] = set()
        self.hidden_wire_colors: set[str] = set()
        self.hidden_groups: set[str] = set()
        self.show_other_parts = True
        self.show_other_pins = True
        self.show_other_wires = True
        self.show_current_keepouts = True
        self.show_other_keepouts = True
        self.show_front_row_labels = True
        self.show_front_col_labels = True
        self.show_back_row_labels = True
        self.show_back_col_labels = True
        self.grid_label_style = "numbers"
        self.show_mouse_cross = False
        self.inspector_focus_grid: Optional[GridPoint] = None
        self.highlighted_groups: set[str] = set()
        self.show_component_names = True
        self.show_pin_names = True
        self.show_pin_counts = True
        self.warning_points: set[GridPoint] = set()
        self.pin_count_map: Dict[Tuple[int, str], int] = {}
        self.temp_wire: list[GridPoint] = []
        self.keepout_start: Optional[GridPoint] = None
        self.new_component_template = Component("Part", 0, 0, 3, 2, "#ffcc66")
        self.current_wire_color = "#d00000"
        self.current_keepout_color = "#ef4444"
        self.route_suggestion_active = False
        self.route_suggestion_points: list[GridPoint] = []
        self.dragging_view = False
        self.drag_last_pos = QPointF()
        self.drag_start_grid: Optional[GridPoint] = None
        self.drag_originals: dict[Selection, object] = {}
        self._drag_snapshot_taken = False
        self.hover_grid: Optional[GridPoint] = None
        self.flip_cue_frames = 0
        self.flip_cue_text = ""

    def set_layout(self, layout: Layout) -> None:
        self.layout_model = layout
        self.hidden_groups.clear()
        self.highlighted_groups.clear()
        self.selected.clear()
        self.temp_wire.clear()
        self.keepout_start = None
        self.route_suggestion_active = False
        self.route_suggestion_points.clear()
        self.update()
        self.selectionChanged.emit()

    def set_tool(self, tool: str) -> None:
        self.tool = tool
        if tool != "wire":
            self.temp_wire.clear()
            self.route_suggestion_active = False
            self.route_suggestion_points.clear()
        if tool != "keepout":
            self.keepout_start = None
        self.update()

    def start_route_suggestion(self) -> None:
        self.set_tool("wire")
        self.route_suggestion_active = True
        self.route_suggestion_points.clear()
        self.temp_wire.clear()
        self.statusMessage.emit("Suggest route: click the start hole, then the destination hole.")
        self.toolRequested.emit("wire")
        self.update()

    def set_side(self, side: str) -> None:
        self.side = side if side in {"front", "back"} else "front"
        self.update()

    def start_flip_animation(self, side: str) -> None:
        self.flip_cue_text = "Front side" if side == "front" else "Back side · mirrored"
        self.flip_cue_frames = 12
        self._tick_flip_animation()

    def _tick_flip_animation(self) -> None:
        if self.flip_cue_frames <= 0:
            self.update()
            return
        self.flip_cue_frames -= 1
        self.update()
        QTimer.singleShot(28, self._tick_flip_animation)

    def fit_to_view(self) -> None:
        if self.layout_model.cols <= 0 or self.layout_model.rows <= 0:
            return
        board_w = self.margin * 2 + (self.layout_model.cols - 1) * self.layout_model.spacing + 80
        board_h = self.margin * 2 + (self.layout_model.rows - 1) * self.layout_model.spacing + 80
        if board_w <= 0 or board_h <= 0:
            return
        self.zoom = max(0.2, min(3.0, min(self.width() / board_w, self.height() / board_h)))
        self.pan = QPointF(20, 20)
        self.zoomChanged.emit(self.zoom)
        self.update()

    def set_zoom_value(self, value: float) -> None:
        self.zoom = max(0.25, min(4.0, float(value)))
        self.zoomChanged.emit(self.zoom)
        self.update()

    def zoom_in(self) -> None:
        self.set_zoom_value(self.zoom * 1.15)

    def zoom_out(self) -> None:
        self.set_zoom_value(self.zoom / 1.15)

    def reset_zoom(self) -> None:
        self.zoom = 1.0
        self.zoomChanged.emit(self.zoom)
        self.update()

    def grid_to_scene(self, row: int, col: int) -> QPointF:
        dcol = display_col_for_side(col, self.layout_model.cols, self.side)
        return QPointF(self.margin + dcol * self.layout_model.spacing, self.margin + row * self.layout_model.spacing)

    def grid_to_view(self, row: int, col: int) -> QPointF:
        p = self.grid_to_scene(row, col)
        return QPointF(self.pan.x() + p.x() * self.zoom, self.pan.y() + p.y() * self.zoom)

    def view_to_grid(self, point: QPointF) -> Optional[GridPoint]:
        sx = (point.x() - self.pan.x()) / self.zoom
        sy = (point.y() - self.pan.y()) / self.zoom
        dcol = round((sx - self.margin) / self.layout_model.spacing)
        row = round((sy - self.margin) / self.layout_model.spacing)
        col = logical_col_from_display(dcol, self.layout_model.cols, self.side)
        if not board_contains(row, col, self.layout_model.rows, self.layout_model.cols):
            return None
        hp = self.grid_to_scene(row, col)
        if abs(sx - hp.x()) <= self.layout_model.spacing * 0.48 and abs(sy - hp.y()) <= self.layout_model.spacing * 0.48:
            return row, col
        return None

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#eef1f7"))
        self._draw_shadow_panel(painter)
        self._draw_board(painter)
        self._draw_mouse_cross(painter)
        self._draw_keepouts(painter, ghost=True)
        self._draw_wires(painter, ghost=True)
        self._draw_components(painter, ghost=True)
        self._draw_vias(painter)
        self._draw_keepouts(painter, ghost=False)
        self._draw_wires(painter, ghost=False)
        self._draw_components(painter, ghost=False)
        self._draw_annotations(painter)
        self._draw_group_outlines(painter)
        self._draw_temp_objects(painter)
        self._draw_wire_mode_hint(painter)
        self._draw_warnings(painter)
        self._draw_selection_box(painter)
        self._draw_flip_cue(painter)
        painter.end()

    def _color(self, value: str, alpha: float = 1.0) -> QColor:
        color = QColor(value or "#000000")
        color.setAlphaF(max(0.0, min(1.0, alpha)))
        return color

    def _index_letters(self, index: int) -> str:
        value = max(0, int(index)) + 1
        chars: list[str] = []
        while value:
            value, rem = divmod(value - 1, 26)
            chars.append(chr(65 + rem))
        return "".join(reversed(chars)) or "A"

    def _grid_label(self, index: int) -> str:
        if self.grid_label_style == "letters":
            return self._index_letters(index)
        if self.grid_label_style == "both":
            return f"{self._index_letters(index)}{index + 1}"
        return str(index + 1)

    def _side_label_visibility(self) -> tuple[bool, bool]:
        if self.side == "back":
            return self.show_back_row_labels, self.show_back_col_labels
        return self.show_front_row_labels, self.show_front_col_labels

    def _draw_grid_labels(self, painter: QPainter, board_rect: QRectF) -> None:
        show_rows, show_cols = self._side_label_visibility()
        if not (show_rows or show_cols):
            return
        painter.save()
        painter.setFont(QFont("Segoe UI", max(7, int(8.5 * self.zoom)), QFont.Weight.Bold))
        painter.setPen(QColor("#dbeafe"))
        badge_bg = QColor(15, 23, 42, 145)
        pad = max(18, 18 * self.zoom)
        if show_cols:
            for c in range(self.layout_model.cols):
                p = self.grid_to_view(0, c)
                txt = self._grid_label(c)
                w = max(18, 9 * len(txt) + 8)
                rect = QRectF(p.x() - w/2, board_rect.top() - pad - 4, w, 16)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(badge_bg)
                painter.drawRoundedRect(rect, 5, 5)
                painter.setPen(QColor("#dbeafe"))
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, txt)
        if show_rows:
            for r in range(self.layout_model.rows):
                p = self.grid_to_view(r, 0)
                txt = self._grid_label(r)
                w = max(18, 9 * len(txt) + 8)
                rect = QRectF(board_rect.left() - pad - w + 10, p.y() - 8, w, 16)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(badge_bg)
                painter.drawRoundedRect(rect, 5, 5)
                painter.setPen(QColor("#dbeafe"))
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, txt)
        painter.restore()

    def _draw_mouse_cross(self, painter: QPainter) -> None:
        if not self.show_mouse_cross:
            return
        grid = self.inspector_focus_grid or self.hover_grid
        if not grid or not board_contains(grid[0], grid[1], self.layout_model.rows, self.layout_model.cols):
            return
        r, c = grid
        p_left = self.grid_to_view(r, 0)
        p_right = self.grid_to_view(r, self.layout_model.cols - 1)
        p_top = self.grid_to_view(0, c)
        p_bottom = self.grid_to_view(self.layout_model.rows - 1, c)
        painter.save()
        pen = QPen(QColor(250, 204, 21, 145), max(2.0, 3.2 * self.zoom), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(p_left, p_right)
        painter.drawLine(p_top, p_bottom)
        painter.setBrush(QColor(250, 204, 21, 85))
        painter.setPen(QPen(QColor(250, 204, 21, 210), max(1.5, 2 * self.zoom)))
        painter.drawEllipse(self.grid_to_view(r, c), max(8, 10 * self.zoom), max(8, 10 * self.zoom))
        painter.restore()

    def _draw_flip_cue(self, painter: QPainter) -> None:
        if self.flip_cue_frames <= 0:
            return
        alpha = int(30 + 150 * (self.flip_cue_frames / 12))
        w = min(360, max(220, self.width() // 4))
        h = 64
        x = (self.width() - w) / 2
        y = 24
        rect = QRectF(x, y, w, h)
        painter.setPen(Qt.PenStyle.NoPen)
        bg = QColor(15, 23, 42, alpha)
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, 20, 20)
        painter.setPen(QPen(QColor(255, 255, 255, max(120, alpha)), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        center_y = y + h / 2
        painter.drawArc(QRectF(x + 22, y + 14, 44, 36), 35 * 16, 285 * 16)
        painter.drawLine(QPointF(x + 61, center_y - 14), QPointF(x + 72, center_y - 6))
        painter.drawLine(QPointF(x + 61, center_y - 14), QPointF(x + 61, center_y - 1))
        painter.setFont(QFont("Segoe UI", max(11, int(14 * self.zoom)), QFont.Weight.Bold))
        painter.drawText(rect.adjusted(90, 0, -16, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.flip_cue_text)

    def _draw_shadow_panel(self, painter: QPainter) -> None:
        x1, y1 = self.grid_to_view(0, 0).x() - 28 * self.zoom, self.grid_to_view(0, 0).y() - 28 * self.zoom
        x2, y2 = self.grid_to_view(self.layout_model.rows - 1, self.layout_model.cols - 1).x() + 28 * self.zoom, self.grid_to_view(self.layout_model.rows - 1, self.layout_model.cols - 1).y() + 28 * self.zoom
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 24))
        painter.drawRoundedRect(QRectF(x1 + 8, y1 + 10, x2 - x1, y2 - y1), 18, 18)

    def _draw_board(self, painter: QPainter) -> None:
        p1 = self.grid_to_view(0, 0)
        p2 = self.grid_to_view(self.layout_model.rows - 1, self.layout_model.cols - 1)
        rect = QRectF(min(p1.x(), p2.x()) - 28 * self.zoom, min(p1.y(), p2.y()) - 28 * self.zoom, abs(p2.x() - p1.x()) + 56 * self.zoom, abs(p2.y() - p1.y()) + 56 * self.zoom)
        painter.setBrush(QColor("#177a3b"))
        side_border = QColor("#5b8def") if self.side == "front" else QColor("#f59e0b")
        mode_colors = {
            "component": QColor("#8b5cf6"),
            "wire": QColor("#ef4444"),
            "via": QColor("#a855f7"),
            "annotation": QColor("#0ea5e9"),
            "keepout": QColor("#f97316"),
        }
        if self.tool != "select" and self.tool in mode_colors:
            painter.setPen(QPen(mode_colors[self.tool], max(4.0, 7 * self.zoom)))
            painter.drawRoundedRect(rect.adjusted(-4 * self.zoom, -4 * self.zoom, 4 * self.zoom, 4 * self.zoom), 16 * self.zoom, 16 * self.zoom)
            painter.setPen(QPen(side_border, max(1.5, 2.2 * self.zoom)))
        else:
            painter.setPen(QPen(side_border, max(2.0, 4 * self.zoom)))
        painter.drawRoundedRect(rect, 14 * self.zoom, 14 * self.zoom)

        # subtle strip hints
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 35))
        for col in range(0, self.layout_model.cols, 2):
            top = self.grid_to_view(0, col)
            bottom = self.grid_to_view(self.layout_model.rows - 1, col)
            painter.drawRoundedRect(QRectF(top.x() - 5 * self.zoom, top.y() - 26 * self.zoom, 10 * self.zoom, 9 * self.zoom), 3, 3)
            painter.drawRoundedRect(QRectF(bottom.x() - 5 * self.zoom, bottom.y() + 17 * self.zoom, 10 * self.zoom, 9 * self.zoom), 3, 3)

        for r in range(self.layout_model.rows):
            for c in range(self.layout_model.cols):
                p = self.grid_to_view(r, c)
                radius = max(2.0, 4.2 * self.zoom)
                painter.setBrush(QColor("#e5e7eb"))
                painter.setPen(QPen(QColor("#64748b"), max(1.0, 1 * self.zoom)))
                painter.drawEllipse(p, radius, radius)

        self._draw_grid_labels(painter, rect)

    def _is_item_hidden_by_group(self, item) -> bool:
        group = getattr(item, "group", "")
        return bool(group and group in self.hidden_groups)

    def _snap_grid_for_wire(self, pos: QPointF) -> Optional[GridPoint]:
        """Prefer visible component pins before raw hole snapping.

        The normal board-hole snap is intentionally precise. In wire mode the
        user is often aiming at a drawn pin marker or its tiny label, so this
        uses a slightly larger, context-aware target around component pins and
        then falls back to normal hole snapping.
        """
        raw_grid = self.view_to_grid(pos)
        if raw_grid is not None:
            raw_point = self.grid_to_view(*raw_grid)
            raw_dist = math.hypot(pos.x() - raw_point.x(), pos.y() - raw_point.y())
            if raw_dist <= max(8.0, self.layout_model.spacing * self.zoom * 0.34):
                return raw_grid
        best: Optional[GridPoint] = None
        # A pin marker is visually larger than the underlying hole. Allow enough
        # radius to hit the marker/label area, but still choose the nearest pin.
        base_radius = max(16.0, self.layout_model.spacing * self.zoom * 0.9)
        best_dist = float("inf")
        for comp in self.layout_model.components:
            if comp.side != self.side or self._is_item_hidden_by_group(comp):
                continue
            # If the click is around the component body, be more willing to snap
            # to one of its pins. This avoids suggested routes landing on the
            # nearest ordinary board hole instead of the intended component pin.
            near_component = self._component_rect(comp).adjusted(-28 * self.zoom, -28 * self.zoom, 28 * self.zoom, 28 * self.zoom).contains(pos)
            local_limit = max(base_radius, self.layout_model.spacing * self.zoom * (1.35 if near_component else 0.9))
            for pin in comp.pins:
                r, c = component_pin_absolute(comp, pin)
                if not board_contains(r, c, self.layout_model.rows, self.layout_model.cols):
                    continue
                point = self.grid_to_view(r, c)
                dist = math.hypot(pos.x() - point.x(), pos.y() - point.y())
                if dist <= local_limit and dist < best_dist:
                    best_dist = dist
                    best = (r, c)
        return best or raw_grid

    def _segment_grid_points(self, a: GridPoint, b: GridPoint) -> list[GridPoint]:
        ar, ac = a; br, bc = b
        if ar == br:
            step = 1 if bc >= ac else -1
            return [(ar, c) for c in range(ac, bc + step, step)]
        if ac == bc:
            step = 1 if br >= ar else -1
            return [(r, ac) for r in range(ar, br + step, step)]
        return [a, b]

    def _route_blocked_cells(self, start: GridPoint, end: GridPoint) -> set[GridPoint]:
        blocked: set[GridPoint] = set()
        endpoints = {start, end}
        for comp in self.layout_model.components:
            if comp.side != self.side or self._is_item_hidden_by_group(comp):
                continue
            for r in range(comp.row, comp.row + comp.height):
                for c in range(comp.col, comp.col + comp.width):
                    pt = (r, c)
                    if pt not in endpoints and board_contains(r, c, self.layout_model.rows, self.layout_model.cols):
                        blocked.add(pt)
        for zone in self.layout_model.keepouts:
            if self._is_item_hidden_by_group(zone) or zone.side not in {"both", self.side}:
                continue
            r1, r2 = sorted((zone.row1, zone.row2)); c1, c2 = sorted((zone.col1, zone.col2))
            for r in range(r1, r2 + 1):
                for c in range(c1, c2 + 1):
                    pt = (r, c)
                    if pt not in endpoints and board_contains(r, c, self.layout_model.rows, self.layout_model.cols):
                        blocked.add(pt)
        return blocked

    def _route_wire_cells(self) -> set[GridPoint]:
        occupied: set[GridPoint] = set()
        for wire in self.layout_model.wires:
            if wire.side != self.side or wire.color in self.hidden_wire_colors or self._is_item_hidden_by_group(wire):
                continue
            for a, b in zip(wire.points, wire.points[1:]):
                for pt in self._segment_grid_points(a, b):
                    if board_contains(pt[0], pt[1], self.layout_model.rows, self.layout_model.cols):
                        occupied.add(pt)
        return occupied

    def _compress_route(self, points: list[GridPoint]) -> list[GridPoint]:
        if len(points) <= 2:
            return points
        result = [points[0]]
        prev_dir: Optional[tuple[int, int]] = None
        for a, b in zip(points, points[1:]):
            direction = (0 if b[0] == a[0] else (1 if b[0] > a[0] else -1), 0 if b[1] == a[1] else (1 if b[1] > a[1] else -1))
            if prev_dir is not None and direction != prev_dir:
                result.append(a)
            prev_dir = direction
        result.append(points[-1])
        return result

    def _suggest_route(self, start: GridPoint, end: GridPoint) -> list[GridPoint]:
        """Find a safe orthogonal route that never crosses component bodies.

        Existing wires are treated as expensive, not blocked, so the router will
        avoid crossing/overlapping them unless that is the only practical route.
        Component bodies and keepout cells are hard blocks, except for the two
        explicit endpoints.
        """
        if start == end:
            return [start]
        rows = self.layout_model.rows
        cols = self.layout_model.cols
        if not (board_contains(start[0], start[1], rows, cols) and board_contains(end[0], end[1], rows, cols)):
            return []
        blocked = self._route_blocked_cells(start, end)
        wire_cells = self._route_wire_cells()
        frontier: list[tuple[int, int, GridPoint, Optional[tuple[int, int]]]] = []
        heapq.heappush(frontier, (0, 0, start, None))
        came_from: dict[GridPoint, Optional[GridPoint]] = {start: None}
        best_cost: dict[tuple[GridPoint, Optional[tuple[int, int]]], int] = {(start, None): 0}
        counter = 0
        directions = [(1, 0), (-1, 0), (0, 1), (0, -1)]
        found: Optional[tuple[GridPoint, Optional[tuple[int, int]]]] = None
        best_end_cost = 10**12
        while frontier:
            cost, _, current, prev_dir = heapq.heappop(frontier)
            if current == end:
                found = (current, prev_dir)
                best_end_cost = cost
                break
            if cost > best_end_cost:
                continue
            for direction in directions:
                nr, nc = current[0] + direction[0], current[1] + direction[1]
                nxt = (nr, nc)
                if not board_contains(nr, nc, rows, cols) or nxt in blocked:
                    continue
                step_cost = 10
                if nxt in wire_cells and nxt not in {start, end}:
                    step_cost += 22
                if prev_dir is not None and direction != prev_dir:
                    step_cost += 4
                # A tiny Manhattan bias makes equally safe routes look direct.
                priority = cost + step_cost + abs(end[0] - nr) + abs(end[1] - nc)
                state = (nxt, direction)
                new_cost = cost + step_cost
                if new_cost < best_cost.get(state, 10**12):
                    best_cost[state] = new_cost
                    if nxt not in came_from or new_cost < best_end_cost:
                        came_from[nxt] = current
                    counter += 1
                    heapq.heappush(frontier, (priority, counter, nxt, direction))
        if found is None or end not in came_from:
            return []
        path: list[GridPoint] = []
        cur: Optional[GridPoint] = end
        while cur is not None:
            path.append(cur)
            cur = came_from.get(cur)
        path.reverse()
        return self._compress_route(path)

    def _draw_components(self, painter: QPainter, *, ghost: bool) -> None:
        for i, comp in enumerate(self.layout_model.components):
            if self._is_item_hidden_by_group(comp):
                continue
            is_current = comp.side == self.side
            if ghost and (is_current or not self.show_other_parts):
                continue
            if not ghost and not is_current:
                continue
            self._draw_component(painter, i, comp, alpha=0.28 if ghost else 1.0, ghost=ghost)

    def _component_rect(self, comp: Component) -> QRectF:
        p1 = self.grid_to_view(comp.row, comp.col)
        p2 = self.grid_to_view(comp.row + comp.height - 1, comp.col + comp.width - 1)
        pad = self.layout_model.spacing * self.zoom * 0.42
        return QRectF(min(p1.x(), p2.x()) - pad, min(p1.y(), p2.y()) - pad, abs(p2.x() - p1.x()) + pad * 2, abs(p2.y() - p1.y()) + pad * 2)

    def _draw_component(self, painter: QPainter, index: int, comp: Component, *, alpha: float, ghost: bool) -> None:
        rect = self._component_rect(comp)
        selected = ("component", index) in self.selected
        painter.save()
        painter.translate(rect.center())
        painter.rotate(comp.rotation)
        local = QRectF(-rect.width()/2, -rect.height()/2, rect.width(), rect.height())
        painter.setBrush(self._color(comp.color, alpha))
        painter.setPen(QPen(QColor("#ffffff") if selected else self._color("#111827", 0.75 if not ghost else 0.25), 3 if selected else 1.2))
        painter.drawRoundedRect(local, 8, 8)
        painter.restore()

        # Jumpers and pins are not rotated because they are physical snap holes.
        for jumper in comp.jumpers:
            pa = next((p for p in comp.pins if p.name == jumper.pin_a), None)
            pb = next((p for p in comp.pins if p.name == jumper.pin_b), None)
            if pa and pb:
                ar, ac = component_pin_absolute(comp, pa)
                br, bc = component_pin_absolute(comp, pb)
                painter.setPen(QPen(self._color(jumper.color, alpha), max(2.0, 3.0 * self.zoom), Qt.PenStyle.DashLine if ghost else Qt.PenStyle.SolidLine))
                painter.drawLine(self.grid_to_view(ar, ac), self.grid_to_view(br, bc))
        for pin in comp.pins:
            r, c = component_pin_absolute(comp, pin)
            if not board_contains(r, c, self.layout_model.rows, self.layout_model.cols):
                continue
            p = self.grid_to_view(r, c)
            painter.setBrush(self._color("#111827", alpha))
            painter.setPen(QPen(QColor("#ffffff"), max(1.0, 1.2 * self.zoom)))
            painter.drawEllipse(p, max(3, 5 * self.zoom), max(3, 5 * self.zoom))
            if self.show_pin_counts:
                count = self.pin_count_map.get((index, pin.name))
                if count is not None and count > 0 and not ghost:
                    painter.setBrush(QColor("#ffffff"))
                    painter.setPen(QPen(QColor("#334155"), 1))
                    painter.drawRoundedRect(QRectF(p.x()+5*self.zoom, p.y()-13*self.zoom, 16*self.zoom, 14*self.zoom), 5, 5)
                    painter.drawText(QRectF(p.x()+5*self.zoom, p.y()-13*self.zoom, 16*self.zoom, 14*self.zoom), Qt.AlignmentFlag.AlignCenter, str(count))
            if (self.show_pin_names and comp.show_pin_names and not ghost) or (ghost and self.show_other_pins):
                painter.setPen(self._color("#111827", 0.65 if not ghost else 0.32))
                painter.setFont(QFont("Segoe UI", max(7, int(8 * self.zoom))))
                painter.drawText(QPointF(p.x() + 7 * self.zoom, p.y() - 7 * self.zoom), pin.name)
        if self.show_component_names and comp.show_name and not ghost:
            painter.setPen(QColor("#0f172a"))
            painter.setFont(QFont("Segoe UI", max(8, int(9 * self.zoom)), QFont.Weight.Bold))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, comp.name)
            if comp.value:
                painter.setFont(QFont("Segoe UI", max(7, int(8 * self.zoom))))
                painter.drawText(rect.adjusted(0, 16*self.zoom, 0, 0), Qt.AlignmentFlag.AlignCenter, comp.value)

    def _draw_wires(self, painter: QPainter, *, ghost: bool) -> None:
        for i, wire in enumerate(self.layout_model.wires):
            if self._is_item_hidden_by_group(wire):
                continue
            is_current = wire.side == self.side
            if wire.color in self.hidden_wire_colors:
                continue
            if ghost and (is_current or not self.show_other_wires):
                continue
            if not ghost and not is_current:
                continue
            self._draw_wire(painter, i, wire, alpha=0.25 if ghost else 1.0, ghost=ghost)

    def _draw_wire(self, painter: QPainter, index: int, wire: Wire, *, alpha: float, ghost: bool) -> None:
        if len(wire.points) < 1:
            return
        pts = [self.grid_to_view(r, c) for r, c in wire.points if board_contains(r, c, self.layout_model.rows, self.layout_model.cols)]
        if not pts:
            return
        selected = ("wire", index) in self.selected
        if len(pts) >= 2:
            pen_bg = QPen(QColor("#ffffff") if selected else QColor(0, 0, 0, 55), max(5.0, 8.5 * self.zoom), Qt.PenStyle.DashLine if ghost else Qt.PenStyle.SolidLine)
            pen_bg.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen_bg.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen_bg)
            for a, b in zip(pts, pts[1:]):
                painter.drawLine(a, b)
            pen = QPen(self._color(wire.color, alpha), max(3.0, 5.0 * self.zoom), Qt.PenStyle.DashLine if ghost else Qt.PenStyle.SolidLine)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            for a, b in zip(pts, pts[1:]):
                painter.drawLine(a, b)
        for p in pts:
            painter.setBrush(self._color(wire.color, alpha))
            painter.setPen(QPen(QColor("#ffffff"), max(1.0, 1.2 * self.zoom)))
            painter.drawEllipse(p, max(3, 5 * self.zoom), max(3, 5 * self.zoom))
        if wire.name and not ghost and len(pts) >= 2:
            mid = pts[len(pts)//2]
            painter.setPen(QColor("#111827"))
            painter.setFont(QFont("Segoe UI", max(7, int(8 * self.zoom))))
            painter.drawText(QPointF(mid.x()+8*self.zoom, mid.y()-8*self.zoom), wire.name)

    def _draw_vias(self, painter: QPainter) -> None:
        for i, via in enumerate(self.layout_model.vias):
            if self._is_item_hidden_by_group(via):
                continue
            if not board_contains(via.row, via.col, self.layout_model.rows, self.layout_model.cols):
                continue
            p = self.grid_to_view(via.row, via.col)
            selected = ("via", i) in self.selected
            painter.setBrush(self._color(via.color, 0.9))
            painter.setPen(QPen(QColor("#ffffff") if selected else QColor("#4c1d95"), max(2.0, 2.4 * self.zoom)))
            painter.drawEllipse(p, max(5, 7 * self.zoom), max(5, 7 * self.zoom))
            painter.setBrush(QColor("#ffffff"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(p, max(2, 3 * self.zoom), max(2, 3 * self.zoom))

    def _draw_annotations(self, painter: QPainter) -> None:
        for i, note in enumerate(self.layout_model.annotations):
            if self._is_item_hidden_by_group(note):
                continue
            if note.side != self.side:
                continue
            p = self.grid_to_view(note.row, note.col)
            selected = ("annotation", i) in self.selected
            painter.setBrush(QColor("#ffffff"))
            painter.setPen(QPen(QColor("#2457d6") if selected else QColor("#cbd5e1"), 2 if selected else 1))
            rect = QRectF(p.x(), p.y() - 18 * self.zoom, max(60, 120 * self.zoom), max(22, 34 * self.zoom))
            painter.drawRoundedRect(rect, 8, 8)
            painter.setPen(self._color(note.color, 1))
            painter.drawText(rect.adjusted(8, 2, -8, -2), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, note.text)

    def _draw_keepouts(self, painter: QPainter, *, ghost: bool) -> None:
        """Draw mechanical keepout zones.

        Current-side keepouts are normal overlays. Opposite-side keepouts are
        ghost overlays, so users can line up mechanical constraints without
        cluttering the canvas when they do not need them.
        """
        for i, zone in enumerate(self.layout_model.keepouts):
            if self._is_item_hidden_by_group(zone):
                continue
            applies_to_current = zone.side == "both" or zone.side == self.side
            if ghost:
                if not self.show_other_keepouts or applies_to_current:
                    continue
            else:
                if not self.show_current_keepouts or not applies_to_current:
                    continue
            p1 = self.grid_to_view(zone.row1, zone.col1)
            p2 = self.grid_to_view(zone.row2, zone.col2)
            rect = QRectF(min(p1.x(), p2.x()), min(p1.y(), p2.y()), abs(p2.x()-p1.x()), abs(p2.y()-p1.y())).adjusted(-9*self.zoom, -9*self.zoom, 9*self.zoom, 9*self.zoom)
            selected = ("keepout", i) in self.selected
            alpha = 0.08 if ghost else 0.16
            line_alpha = 0.42 if ghost else 0.9
            pen_style = Qt.PenStyle.DotLine if ghost else Qt.PenStyle.DashLine
            painter.setBrush(self._color(zone.color, alpha))
            painter.setPen(QPen(self._color(zone.color, line_alpha), 3 if selected else 1.4, pen_style))
            painter.drawRoundedRect(rect, 10, 10)
            painter.setPen(self._color(zone.color, 0.55 if ghost else 0.95))
            label = f"{zone.name} ({zone.side})" if ghost else zone.name
            painter.drawText(rect.adjusted(8, 4, -8, -4), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft, label)

    def _group_outline_color(self, group: str) -> QColor:
        palette = ["#2563eb", "#7c3aed", "#db2777", "#ea580c", "#059669", "#0891b2"]
        return QColor(palette[abs(hash(group)) % len(palette)])

    def _rect_for_selection_like(self, kind: str, idx: int) -> Optional[QRectF]:
        if kind == "component" and 0 <= idx < len(self.layout_model.components):
            comp = self.layout_model.components[idx]
            if comp.side == self.side:
                return self._component_rect(comp)
        if kind == "wire" and 0 <= idx < len(self.layout_model.wires):
            wire = self.layout_model.wires[idx]
            if wire.side == self.side and wire.points:
                pts = [self.grid_to_view(r, c) for r, c in wire.points if board_contains(r, c, self.layout_model.rows, self.layout_model.cols)]
                if pts:
                    xs = [p.x() for p in pts]; ys = [p.y() for p in pts]
                    pad = max(10, 8 * self.zoom)
                    return QRectF(min(xs)-pad, min(ys)-pad, max(xs)-min(xs)+2*pad, max(ys)-min(ys)+2*pad)
        if kind == "via" and 0 <= idx < len(self.layout_model.vias):
            via = self.layout_model.vias[idx]
            p = self.grid_to_view(via.row, via.col)
            pad = max(10, 8 * self.zoom)
            return QRectF(p.x()-pad, p.y()-pad, pad*2, pad*2)
        if kind == "annotation" and 0 <= idx < len(self.layout_model.annotations):
            note = self.layout_model.annotations[idx]
            if note.side == self.side:
                p = self.grid_to_view(note.row, note.col)
                return QRectF(p.x(), p.y()-18*self.zoom, max(60, 120*self.zoom), max(22,34*self.zoom))
        if kind == "keepout" and 0 <= idx < len(self.layout_model.keepouts):
            zone = self.layout_model.keepouts[idx]
            if zone.side in {"both", self.side}:
                p1 = self.grid_to_view(zone.row1, zone.col1); p2 = self.grid_to_view(zone.row2, zone.col2)
                return QRectF(min(p1.x(), p2.x()), min(p1.y(), p2.y()), abs(p2.x()-p1.x()), abs(p2.y()-p1.y()))
        return None

    def _draw_group_outlines(self, painter: QPainter) -> None:
        if not self.highlighted_groups:
            return
        groups: Dict[str, list[QRectF]] = {}
        collections = [
            ("component", self.layout_model.components),
            ("wire", self.layout_model.wires),
            ("via", self.layout_model.vias),
            ("annotation", self.layout_model.annotations),
            ("keepout", self.layout_model.keepouts),
        ]
        for kind, coll in collections:
            for idx, item in enumerate(coll):
                group = getattr(item, "group", "")
                if not group or group in self.hidden_groups or group not in self.highlighted_groups:
                    continue
                rect = self._rect_for_selection_like(kind, idx)
                if rect is not None:
                    groups.setdefault(group, []).append(rect)
        for group, rects in groups.items():
            if not rects:
                continue
            rect = QRectF(rects[0])
            for other in rects[1:]:
                rect = rect.united(other)
            rect = rect.adjusted(-14, -14, 14, 14)
            color = self._group_outline_color(group)
            painter.setBrush(QColor(color.red(), color.green(), color.blue(), 20))
            painter.setPen(QPen(color, 1.6, Qt.PenStyle.DashLine))
            painter.drawRoundedRect(rect, 14, 14)
            painter.setFont(QFont("Segoe UI", max(8, int(9 * self.zoom)), QFont.Weight.Bold))
            painter.setPen(color)
            painter.drawText(rect.adjusted(8, 2, -8, -2), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft, group)

    def _draw_temp_objects(self, painter: QPainter) -> None:
        if self.temp_wire:
            pts = [self.grid_to_view(r, c) for r, c in self.temp_wire]
            painter.setPen(QPen(self._color(self.current_wire_color, 0.85), max(3, 4*self.zoom), Qt.PenStyle.DashLine))
            for a, b in zip(pts, pts[1:]):
                painter.drawLine(a, b)
            for p in pts:
                painter.setBrush(self._color(self.current_wire_color, 0.9))
                painter.setPen(QPen(QColor("#ffffff"), 1.2))
                painter.drawEllipse(p, max(4, 5*self.zoom), max(4, 5*self.zoom))
        if self.keepout_start:
            p = self.grid_to_view(*self.keepout_start)
            painter.setBrush(self._color(self.current_keepout_color, 0.25))
            painter.setPen(QPen(self._color(self.current_keepout_color, 0.9), 2, Qt.PenStyle.DashLine))
            painter.drawEllipse(p, 8*self.zoom, 8*self.zoom)

    def _draw_wire_mode_hint(self, painter: QPainter) -> None:
        # The old canvas instruction bubble felt like a floating button. Keep
        # the canvas clean; when route suggestion is active, only draw the
        # actual preview path.
        if self.tool != "wire" or not self.route_suggestion_active or not self.route_suggestion_points:
            return
        start = self.route_suggestion_points[0]
        end = self.hover_grid or start
        route = self._suggest_route(start, end)
        if not route:
            painter.save()
            painter.setPen(QPen(QColor("#ef4444"), max(2.0, 2.4 * self.zoom)))
            painter.setFont(QFont("Segoe UI", max(8, int(9 * self.zoom)), QFont.Weight.Bold))
            p = self.grid_to_view(*start)
            painter.drawText(QPointF(p.x() + 12 * self.zoom, p.y() - 12 * self.zoom), "No safe route")
            painter.restore()
            return
        pts = [self.grid_to_view(r, c) for r, c in route]
        painter.save()
        painter.setPen(QPen(self._color(self.current_wire_color, 0.58), max(2.5, 4 * self.zoom), Qt.PenStyle.DashLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        for a, b in zip(pts, pts[1:]):
            painter.drawLine(a, b)
        for p in pts:
            painter.setBrush(self._color(self.current_wire_color, 0.78))
            painter.setPen(QPen(QColor("#ffffff"), 1.2))
            painter.drawEllipse(p, max(3.5, 4.5*self.zoom), max(3.5, 4.5*self.zoom))
        painter.restore()

    def _draw_warnings(self, painter: QPainter) -> None:
        painter.setBrush(QColor(239, 68, 68, 55))
        painter.setPen(QPen(QColor("#ef4444"), max(2.0, 2.2*self.zoom)))
        for r, c in self.warning_points:
            if board_contains(r, c, self.layout_model.rows, self.layout_model.cols):
                p = self.grid_to_view(r, c)
                painter.drawEllipse(p, max(9, 11*self.zoom), max(9, 11*self.zoom))

    def _draw_selection_box(self, painter: QPainter) -> None:
        for kind, idx in self.selected:
            if kind == "component" and 0 <= idx < len(self.layout_model.components):
                rect = self._component_rect(self.layout_model.components[idx]).adjusted(-4, -4, 4, 4)
            elif kind == "annotation" and 0 <= idx < len(self.layout_model.annotations):
                note = self.layout_model.annotations[idx]
                p = self.grid_to_view(note.row, note.col)
                rect = QRectF(p.x(), p.y()-18*self.zoom, max(60, 120*self.zoom), max(22, 34*self.zoom)).adjusted(-4,-4,4,4)
            elif kind == "keepout" and 0 <= idx < len(self.layout_model.keepouts):
                zone = self.layout_model.keepouts[idx]
                p1 = self.grid_to_view(zone.row1, zone.col1); p2 = self.grid_to_view(zone.row2, zone.col2)
                rect = QRectF(min(p1.x(), p2.x()), min(p1.y(), p2.y()), abs(p2.x()-p1.x()), abs(p2.y()-p1.y())).adjusted(-12*self.zoom, -12*self.zoom, 12*self.zoom, 12*self.zoom)
            else:
                continue
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#2457d6"), 2, Qt.PenStyle.DashLine))
            painter.drawRoundedRect(rect, 8, 8)

    def wheelEvent(self, event: QWheelEvent):  # noqa: N802
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            old_zoom = self.zoom
            factor = 1.12 if event.angleDelta().y() > 0 else 1/1.12
            self.zoom = max(0.25, min(4.0, self.zoom * factor))
            pos = QPointF(event.position())
            scene_before = QPointF((pos.x()-self.pan.x())/old_zoom, (pos.y()-self.pan.y())/old_zoom)
            self.pan = QPointF(pos.x()-scene_before.x()*self.zoom, pos.y()-scene_before.y()*self.zoom)
            self.zoomChanged.emit(self.zoom)
            self.statusMessage.emit(f"Zoom {int(self.zoom*100)}%")
        else:
            delta = event.angleDelta()
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.pan += QPointF(delta.y() / 2, 0)
            else:
                self.pan += QPointF(0, delta.y() / 2)
        self.update()

    def mousePressEvent(self, event: QMouseEvent):  # noqa: N802
        self.setFocus()
        pos = QPointF(event.position())
        if event.button() in {Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton} and self.tool == "select":
            self.dragging_view = True
            self.drag_last_pos = pos
            return
        grid = self._snap_grid_for_wire(pos) if self.tool == "wire" else self.view_to_grid(pos)
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self.tool != "select" and grid is None:
            self.set_tool("select")
            self.toolRequested.emit("select")
            self.statusMessage.emit("Returned to Select mode.")
            return
        if self.tool == "select":
            hit = self.hit_test(pos)
            additive = bool(event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.MetaModifier))
            if hit:
                if additive:
                    if hit in self.selected:
                        self.selected.remove(hit)
                    else:
                        self.selected.add(hit)
                else:
                    if hit not in self.selected:
                        self.selected = {hit}
                self.drag_start_grid = grid
                self._cache_drag_originals()
                self._drag_snapshot_taken = False
            else:
                if not additive:
                    self.selected.clear()
                self.drag_start_grid = None
                self.drag_originals.clear()
            self.selectionChanged.emit(); self.update(); return
        if grid is None:
            return
        if self.tool == "component":
            self.beforeLayoutChange.emit("Add component")
            row, col = grid
            t = self.new_component_template
            comp = Component(t.name, row, col, t.width, t.height, t.color, side=self.side, rotation=t.rotation, show_name=t.show_name, show_pin_names=t.show_pin_names,
                             pins=[ComponentPin(p.name, p.row, p.col) for p in t.pins], jumpers=list(t.jumpers), component_type=t.component_type, value=t.value, category=t.category, orientation_note=t.orientation_note)
            self.layout_model.components.append(comp)
            self.selected = {("component", len(self.layout_model.components)-1)}
            self.layoutChanged.emit(); self.selectionChanged.emit(); self.update(); return
        if self.tool == "wire":
            if self.route_suggestion_active:
                self._handle_route_suggestion_click(grid)
            else:
                self._handle_wire_click(grid, event.modifiers())
            return
        if self.tool == "via":
            self.beforeLayoutChange.emit("Toggle via")
            existing = next((i for i,v in enumerate(self.layout_model.vias) if (v.row, v.col) == grid), None)
            if existing is None:
                self.layout_model.vias.append(Via(grid[0], grid[1]))
                self.selected = {("via", len(self.layout_model.vias)-1)}
            else:
                del self.layout_model.vias[existing]
                self.selected.clear()
            self.layoutChanged.emit(); self.selectionChanged.emit(); self.update(); return
        if self.tool == "annotation":
            self.annotationRequested.emit(grid[0], grid[1]); return
        if self.tool == "keepout":
            if self.keepout_start is None:
                self.keepout_start = grid
                self.statusMessage.emit("Keepout start set. Click the opposite corner.")
            else:
                self.beforeLayoutChange.emit("Add keepout")
                r1, c1 = self.keepout_start
                r2, c2 = grid
                self.layout_model.keepouts.append(KeepoutZone("Keepout", r1, c1, r2, c2, side="both", color=self.current_keepout_color))
                self.keepout_start = None
                self.selected = {("keepout", len(self.layout_model.keepouts)-1)}
                self.layoutChanged.emit(); self.selectionChanged.emit()
            self.update(); return

    def mouseMoveEvent(self, event: QMouseEvent):  # noqa: N802
        pos = QPointF(event.position())
        if self.dragging_view:
            delta = pos - self.drag_last_pos
            self.pan += delta
            self.drag_last_pos = pos
            self.update(); return
        grid = self._snap_grid_for_wire(pos) if self.tool == "wire" else self.view_to_grid(pos)
        self.hover_grid = grid
        if grid:
            r, c = grid
            mapped_c = display_col_for_side(c, self.layout_model.cols, "back" if self.side == "front" else "front")
            self.statusMessage.emit(f"{self.side.title()} hole r{r+1} c{c+1} · opposite physical c{mapped_c+1}")
        if self.tool == "select" and self.drag_start_grid and self.drag_originals and grid:
            if not self._drag_snapshot_taken:
                self.beforeLayoutChange.emit("Move selected items")
                self._drag_snapshot_taken = True
            dr = grid[0] - self.drag_start_grid[0]
            dc = grid[1] - self.drag_start_grid[1]
            self._apply_drag_delta(dr, dc)
            self.layoutChanged.emit(); self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):  # noqa: N802
        self.dragging_view = False
        self.drag_start_grid = None
        self.drag_originals.clear()
        self._drag_snapshot_taken = False

    def mouseDoubleClickEvent(self, event: QMouseEvent):  # noqa: N802
        hit = self.hit_test(QPointF(event.position()))
        if hit:
            self.selected = {hit}
            self.selectionChanged.emit(); self.update()
            self.itemActivated.emit(hit[0], hit[1])
        elif self.tool == "wire" and len(self.temp_wire) >= 2:
            self.finish_temp_wire()

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() in {Qt.Key.Key_Delete, Qt.Key.Key_Backspace}:
            self.delete_selected()
        elif event.key() == Qt.Key.Key_Escape:
            self.temp_wire.clear(); self.keepout_start = None
            self.route_suggestion_active = False; self.route_suggestion_points.clear()
            self.set_tool("select")
            self.toolRequested.emit("select")
            self.statusMessage.emit("Returned to Select mode.")
            self.update()
        elif event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter} and self.tool == "wire":
            self.finish_temp_wire()
        elif event.key() == Qt.Key.Key_R:
            self.rotate_selected()
        else:
            super().keyPressEvent(event)

    def hit_test(self, pos: QPointF) -> Optional[Selection]:
        # topmost order: annotations/vias/components/wires/keepouts
        for i in range(len(self.layout_model.annotations)-1, -1, -1):
            note = self.layout_model.annotations[i]
            if self._is_item_hidden_by_group(note):
                continue
            if note.side != self.side:
                continue
            p = self.grid_to_view(note.row, note.col)
            rect = QRectF(p.x(), p.y()-18*self.zoom, max(60, 120*self.zoom), max(22,34*self.zoom))
            if rect.contains(pos):
                return ("annotation", i)
        for i in range(len(self.layout_model.vias)-1, -1, -1):
            via = self.layout_model.vias[i]
            if self._is_item_hidden_by_group(via):
                continue
            p = self.grid_to_view(via.row, via.col)
            if math.hypot(pos.x()-p.x(), pos.y()-p.y()) <= max(8, 9*self.zoom):
                return ("via", i)
        for i in range(len(self.layout_model.components)-1, -1, -1):
            comp = self.layout_model.components[i]
            if self._is_item_hidden_by_group(comp):
                continue
            if comp.side != self.side:
                continue
            if self._component_rect(comp).adjusted(-4,-4,4,4).contains(pos):
                return ("component", i)
        for i in range(len(self.layout_model.wires)-1, -1, -1):
            wire = self.layout_model.wires[i]
            if self._is_item_hidden_by_group(wire):
                continue
            if wire.side != self.side or wire.color in self.hidden_wire_colors:
                continue
            pts = [self.grid_to_view(r, c) for r, c in wire.points]
            for a, b in zip(pts, pts[1:]):
                if distance_to_segment(pos.x(), pos.y(), a.x(), a.y(), b.x(), b.y()) <= max(8, 7*self.zoom):
                    return ("wire", i)
        for i in range(len(self.layout_model.keepouts)-1, -1, -1):
            zone = self.layout_model.keepouts[i]
            if self._is_item_hidden_by_group(zone):
                continue
            if zone.side not in {"both", self.side} or not self.show_current_keepouts:
                continue
            p1 = self.grid_to_view(zone.row1, zone.col1); p2 = self.grid_to_view(zone.row2, zone.col2)
            rect = QRectF(min(p1.x(), p2.x()), min(p1.y(), p2.y()), abs(p2.x()-p1.x()), abs(p2.y()-p1.y())).adjusted(-12*self.zoom,-12*self.zoom,12*self.zoom,12*self.zoom)
            if rect.contains(pos):
                return ("keepout", i)
        return None

    def _handle_route_suggestion_click(self, grid: GridPoint) -> None:
        if not self.route_suggestion_points:
            self.route_suggestion_points = [grid]
            self.statusMessage.emit("Route start set. Click the destination hole.")
            self.update()
            return
        start = self.route_suggestion_points[0]
        if start == grid:
            self.statusMessage.emit("Choose a different destination hole for the suggested route.")
            return
        route = self._suggest_route(start, grid)
        if not route or len(route) < 2:
            self.statusMessage.emit("No safe route found without crossing component bodies.")
            self.update()
            return
        self.beforeLayoutChange.emit("Suggest route")
        self.layout_model.wires.append(Wire("", route, self.current_wire_color, side=self.side))
        self.selected = {("wire", len(self.layout_model.wires)-1)}
        self.route_suggestion_points.clear()
        self.route_suggestion_active = False
        self.layoutChanged.emit(); self.selectionChanged.emit(); self.update()
        self.statusMessage.emit("Suggested wire added.")

    def _handle_wire_click(self, grid: GridPoint, modifiers) -> None:
        if not self.temp_wire:
            self.temp_wire = [grid]
            self.statusMessage.emit("Wire started. Shift-click adds bends; normal click finishes.")
        elif modifiers & Qt.KeyboardModifier.ShiftModifier:
            if self.temp_wire[-1] != grid:
                self.temp_wire.append(grid)
        else:
            if self.temp_wire[-1] != grid:
                self.temp_wire.append(grid)
            self.finish_temp_wire()
        self.update()

    def finish_temp_wire(self) -> None:
        if len(self.temp_wire) < 2:
            self.temp_wire.clear(); self.update(); return
        self.beforeLayoutChange.emit("Add wire")
        self.layout_model.wires.append(Wire("", list(self.temp_wire), self.current_wire_color, side=self.side))
        self.selected = {("wire", len(self.layout_model.wires)-1)}
        self.temp_wire.clear()
        self.layoutChanged.emit(); self.selectionChanged.emit(); self.update()

    def delete_selected(self) -> None:
        if not self.selected:
            return
        deletable = {sel for sel in self.selected if not self._is_locked(sel)}
        if not deletable:
            self.statusMessage.emit("Selection is locked."); return
        self.beforeLayoutChange.emit("Delete selected")
        for kind in ["annotation", "via", "wire", "component", "keepout"]:
            indexes = sorted([idx for k, idx in deletable if k == kind], reverse=True)
            collection = self._collection(kind)
            for idx in indexes:
                if collection is not None and 0 <= idx < len(collection):
                    del collection[idx]
        self.selected.clear()
        self.layoutChanged.emit(); self.selectionChanged.emit(); self.update()

    def rotate_selected(self) -> None:
        comps = [idx for kind, idx in self.selected if kind == "component" and 0 <= idx < len(self.layout_model.components)]
        if not comps:
            return
        self.beforeLayoutChange.emit("Rotate components")
        for idx in comps:
            comp = self.layout_model.components[idx]
            if comp.locked:
                continue
            comp.rotation = (int(comp.rotation) + 90) % 360
        self.layoutChanged.emit(); self.update()

    def _collection(self, kind: str):
        return {
            "component": self.layout_model.components,
            "wire": self.layout_model.wires,
            "via": self.layout_model.vias,
            "annotation": self.layout_model.annotations,
            "keepout": self.layout_model.keepouts,
        }.get(kind)

    def _is_locked(self, sel: Selection) -> bool:
        collection = self._collection(sel[0])
        if collection is None or not (0 <= sel[1] < len(collection)):
            return False
        return bool(getattr(collection[sel[1]], "locked", False))

    def _cache_drag_originals(self) -> None:
        self.drag_originals.clear()
        for sel in self.selected:
            if self._is_locked(sel):
                continue
            collection = self._collection(sel[0])
            if collection is None or not (0 <= sel[1] < len(collection)):
                continue
            item = collection[sel[1]]
            if sel[0] == "component":
                self.drag_originals[sel] = (item.row, item.col)
            elif sel[0] == "wire":
                self.drag_originals[sel] = list(item.points)
            elif sel[0] == "via":
                self.drag_originals[sel] = (item.row, item.col)
            elif sel[0] == "annotation":
                self.drag_originals[sel] = (item.row, item.col)
            elif sel[0] == "keepout":
                continue

    def _apply_drag_delta(self, dr: int, dc: int) -> None:
        for sel, original in self.drag_originals.items():
            collection = self._collection(sel[0])
            if collection is None or not (0 <= sel[1] < len(collection)):
                continue
            item = collection[sel[1]]
            if sel[0] == "component":
                row, col = original
                item.row = max(0, min(self.layout_model.rows-1, row+dr)); item.col = max(0, min(self.layout_model.cols-1, col+dc))
            elif sel[0] == "wire":
                item.points = [(max(0, min(self.layout_model.rows-1, r+dr)), max(0, min(self.layout_model.cols-1, c+dc))) for r, c in original]
            elif sel[0] in {"via", "annotation"}:
                row, col = original
                item.row = max(0, min(self.layout_model.rows-1, row+dr)); item.col = max(0, min(self.layout_model.cols-1, col+dc))
