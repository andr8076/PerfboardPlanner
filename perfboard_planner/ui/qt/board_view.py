from __future__ import annotations

import heapq
import math
from typing import Dict, Iterable, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt, Signal, QTimer
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPen, QWheelEvent
from PySide6.QtWidgets import QWidget

from ...core.geometry import board_contains, component_pin_absolute, display_col_for_side, logical_col_from_display, distance_to_segment
from ...core.models import Component, ComponentPin, KeepoutZone, Layout, Via, Wire

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
    deleteRequested = Signal()
    quickActionRequested = Signal(str)

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
        # Label style can differ by side because the physical back view is mirrored.
        self.front_grid_label_style = "numbers"
        self.back_grid_label_style = "numbers"
        self.grid_label_style = "numbers"  # compatibility fallback for old UI code
        self.show_mouse_cross = True
        self.warning_focus_mode = False
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
        self._quick_button_rects: dict[str, QRectF] = {}

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

    def view_to_nearest_grid(self, point: QPointF) -> Optional[GridPoint]:
        """Nearest board coordinate, used when dragging selected objects.

        Selection can start on the body of a keepout, annotation, or component,
        not necessarily exactly on a hole. Dragging should still feel direct, so
        this method snaps to the nearest valid hole without the strict hit radius
        used by normal placement.
        """
        sx = (point.x() - self.pan.x()) / self.zoom
        sy = (point.y() - self.pan.y()) / self.zoom
        dcol = round((sx - self.margin) / self.layout_model.spacing)
        row = round((sy - self.margin) / self.layout_model.spacing)
        row = max(0, min(self.layout_model.rows - 1, row))
        dcol = max(0, min(self.layout_model.cols - 1, dcol))
        col = logical_col_from_display(dcol, self.layout_model.cols, self.side)
        col = max(0, min(self.layout_model.cols - 1, col))
        return row, col

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
        self._draw_tool_preview(painter)
        self._draw_temp_objects(painter)
        self._draw_wire_mode_hint(painter)
        self._draw_warning_focus_overlay(painter)
        self._draw_warnings(painter)
        self._draw_selection_box(painter)
        self._draw_selection_quick_controls(painter)
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

    def _grid_label(self, index: int, style: Optional[str] = None) -> str:
        style = style or (self.back_grid_label_style if self.side == "back" else self.front_grid_label_style) or self.grid_label_style
        if style == "letters":
            return self._index_letters(index)
        if style == "both":
            return f"{self._index_letters(index)}{index + 1}"
        return str(index + 1)

    def _side_label_visibility(self) -> tuple[bool, bool, str]:
        if self.side == "back":
            return self.show_back_row_labels, self.show_back_col_labels, self.back_grid_label_style
        return self.show_front_row_labels, self.show_front_col_labels, self.front_grid_label_style

    def _draw_grid_labels(self, painter: QPainter, board_rect: QRectF) -> None:
        show_rows, show_cols, style = self._side_label_visibility()
        if not (show_rows or show_cols):
            return

        # Use slim rails instead of a separate rounded badge per hole. The old
        # badge-per-number look became cluttered on narrow boards and when zoomed
        # out, especially along the top edge.
        painter.save()
        font_size = max(6, min(11, int(8.0 * self.zoom)))
        painter.setFont(QFont("Segoe UI", font_size, QFont.Weight.Bold))
        rail_bg = QColor(15, 23, 42, 118)
        text_color = QColor("#dbeafe")
        tick_pen = QPen(QColor(219, 234, 254, 88), max(1.0, 1.0 * self.zoom))
        text_metrics = painter.fontMetrics()

        if show_cols:
            rail_h = max(15.0, 17.0 * self.zoom)
            rail = QRectF(board_rect.left(), board_rect.top() - rail_h - 5 * self.zoom, board_rect.width(), rail_h)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(rail_bg)
            painter.drawRoundedRect(rail, 6, 6)
            for c in range(self.layout_model.cols):
                p = self.grid_to_view(0, c)
                txt = self._grid_label(c, style)
                label_w = text_metrics.horizontalAdvance(txt)
                painter.setPen(tick_pen)
                painter.drawLine(QPointF(p.x(), rail.bottom() - 3), QPointF(p.x(), rail.bottom() + 4 * self.zoom))
                painter.setPen(text_color)
                if label_w > self.layout_model.spacing * self.zoom * 0.82:
                    painter.save()
                    painter.translate(p.x(), rail.center().y() + 1)
                    painter.rotate(-55)
                    painter.drawText(QRectF(-label_w / 2, -rail_h / 2, label_w + 4, rail_h), Qt.AlignmentFlag.AlignCenter, txt)
                    painter.restore()
                else:
                    painter.drawText(QRectF(p.x() - label_w / 2 - 2, rail.top(), label_w + 4, rail_h), Qt.AlignmentFlag.AlignCenter, txt)

        if show_rows:
            rail_w = max(18.0, 20.0 * self.zoom)
            rail = QRectF(board_rect.left() - rail_w - 5 * self.zoom, board_rect.top(), rail_w, board_rect.height())
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(rail_bg)
            painter.drawRoundedRect(rail, 6, 6)
            for r in range(self.layout_model.rows):
                p = self.grid_to_view(r, 0)
                txt = self._grid_label(r, style)
                painter.setPen(tick_pen)
                painter.drawLine(QPointF(rail.right() - 3, p.y()), QPointF(rail.right() + 4 * self.zoom, p.y()))
                painter.setPen(text_color)
                painter.drawText(QRectF(rail.left(), p.y() - 8, rail_w, 16), Qt.AlignmentFlag.AlignCenter, txt)
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

    def _route_blocked_cells_for_side(self, side: str, start: GridPoint, end: GridPoint) -> set[GridPoint]:
        """Cells that an autorouted wire must never pass through on one side.

        Component bodies and keepout zones are hard blocks. The endpoints are
        exempt so a route can start/end on a component pin that sits on the edge
        of the component footprint.
        """
        blocked: set[GridPoint] = set()
        endpoints = {start, end}
        for comp in self.layout_model.components:
            if comp.side != side or self._is_item_hidden_by_group(comp):
                continue
            for r in range(comp.row, comp.row + comp.height):
                for c in range(comp.col, comp.col + comp.width):
                    pt = (r, c)
                    if pt not in endpoints and board_contains(r, c, self.layout_model.rows, self.layout_model.cols):
                        blocked.add(pt)
        for comp in self.layout_model.components:
            if comp.side != side or self._is_item_hidden_by_group(comp):
                continue
            for pin in comp.pins:
                pt = component_pin_absolute(comp, pin)
                if pt not in endpoints and board_contains(pt[0], pt[1], self.layout_model.rows, self.layout_model.cols):
                    blocked.add(pt)

        for zone in self.layout_model.keepouts:
            if self._is_item_hidden_by_group(zone) or zone.side not in {"both", side}:
                continue
            r1, r2 = sorted((zone.row1, zone.row2)); c1, c2 = sorted((zone.col1, zone.col2))
            for r in range(r1, r2 + 1):
                for c in range(c1, c2 + 1):
                    pt = (r, c)
                    if pt not in endpoints and board_contains(r, c, self.layout_model.rows, self.layout_model.cols):
                        blocked.add(pt)
        return blocked

    def _route_blocked_cells(self, start: GridPoint, end: GridPoint) -> set[GridPoint]:
        return self._route_blocked_cells_for_side(self.side, start, end)

    def _wire_cells_from_wires(self, wires: Iterable[Wire], side: str) -> set[GridPoint]:
        occupied: set[GridPoint] = set()
        for wire in wires:
            if wire.side != side or wire.color in self.hidden_wire_colors or self._is_item_hidden_by_group(wire):
                continue
            for a, b in zip(wire.points, wire.points[1:]):
                for pt in self._segment_grid_points(a, b):
                    if board_contains(pt[0], pt[1], self.layout_model.rows, self.layout_model.cols):
                        occupied.add(pt)
        return occupied

    def _route_wire_cells_for_side(self, side: str, *, ignore_wire_indexes: Optional[set[int]] = None, extra_occupied: Optional[set[GridPoint]] = None) -> set[GridPoint]:
        occupied: set[GridPoint] = set(extra_occupied or set())
        ignore_wire_indexes = ignore_wire_indexes or set()
        for i, wire in enumerate(self.layout_model.wires):
            if i in ignore_wire_indexes:
                continue
            if wire.side != side or wire.color in self.hidden_wire_colors or self._is_item_hidden_by_group(wire):
                continue
            for a, b in zip(wire.points, wire.points[1:]):
                for pt in self._segment_grid_points(a, b):
                    if board_contains(pt[0], pt[1], self.layout_model.rows, self.layout_model.cols):
                        occupied.add(pt)
        return occupied

    def _route_wire_cells(self) -> set[GridPoint]:
        return self._route_wire_cells_for_side(self.side)

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

    def _suggest_route_on_side(
        self,
        start: GridPoint,
        end: GridPoint,
        side: str,
        *,
        ignore_wire_indexes: Optional[set[int]] = None,
        extra_occupied: Optional[set[GridPoint]] = None,
    ) -> list[GridPoint]:
        """Find a safe orthogonal route without runaway memory use.

        Component bodies and keepout cells are hard blocks, except for the two
        explicit endpoints. Existing wire cells are not blocked, but they are
        expensive, so the route avoids them when a clean alternative exists.
        """
        if start == end:
            return [start]
        rows = self.layout_model.rows
        cols = self.layout_model.cols
        if not (board_contains(start[0], start[1], rows, cols) and board_contains(end[0], end[1], rows, cols)):
            return []

        blocked = self._route_blocked_cells_for_side(side, start, end)
        wire_cells = self._route_wire_cells_for_side(side, ignore_wire_indexes=ignore_wire_indexes, extra_occupied=extra_occupied)
        directions = [(1, 0), (-1, 0), (0, 1), (0, -1)]

        # State is (grid point, incoming direction). Keeping the direction in
        # the state lets us penalize bends without corrupting the parent chain.
        State = tuple[GridPoint, Optional[tuple[int, int]]]
        start_state: State = (start, None)

        def heuristic(pt: GridPoint) -> int:
            return (abs(end[0] - pt[0]) + abs(end[1] - pt[1])) * 10

        frontier: list[tuple[int, int, int, GridPoint, Optional[tuple[int, int]]]] = []
        counter = 0
        heapq.heappush(frontier, (heuristic(start), counter, 0, start, None))

        best_g: dict[State, int] = {start_state: 0}
        came_from: dict[State, Optional[State]] = {start_state: None}
        found_state: Optional[State] = None
        expanded = 0
        max_expansions = max(1, rows * cols * 4 + 8)

        while frontier and expanded < max_expansions:
            _, _, current_g, current, prev_dir = heapq.heappop(frontier)
            state: State = (current, prev_dir)
            if current_g != best_g.get(state):
                continue
            expanded += 1

            if current == end:
                found_state = state
                break

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

                next_g = current_g + step_cost
                next_state: State = (nxt, direction)
                if next_g >= best_g.get(next_state, 10**12):
                    continue

                best_g[next_state] = next_g
                came_from[next_state] = state
                counter += 1
                heapq.heappush(frontier, (next_g + heuristic(nxt), counter, next_g, nxt, direction))

        if found_state is None:
            return []

        path: list[GridPoint] = []
        state: Optional[State] = found_state
        guard = 0
        while state is not None and guard <= max_expansions:
            point, _ = state
            path.append(point)
            state = came_from.get(state)
            guard += 1
        if not path or path[-1] != start:
            return []
        path.reverse()
        return self._compress_route(path)

    def _suggest_route(self, start: GridPoint, end: GridPoint) -> list[GridPoint]:
        return self._suggest_route_on_side(start, end, self.side)

    def _route_cost(self, route: list[GridPoint]) -> int:
        if not route or len(route) < 2:
            return 10**9
        length = 0
        bends = 0
        prev_dir: Optional[tuple[int, int]] = None
        for a, b in zip(route, route[1:]):
            length += abs(b[0] - a[0]) + abs(b[1] - a[1])
            direction = (0 if b[0] == a[0] else (1 if b[0] > a[0] else -1), 0 if b[1] == a[1] else (1 if b[1] > a[1] else -1))
            if prev_dir is not None and direction != prev_dir:
                bends += 1
            prev_dir = direction
        return length * 10 + bends * 4

    def _route_cells(self, route: list[GridPoint]) -> set[GridPoint]:
        cells: set[GridPoint] = set()
        for a, b in zip(route, route[1:]):
            cells.update(self._segment_grid_points(a, b))
        return cells

    def _via_exists(self, point: GridPoint) -> bool:
        return any((via.row, via.col) == point for via in self.layout_model.vias)

    def _copy_wire_with_route(self, source: Wire, route: list[GridPoint], side: str) -> Wire:
        return Wire(
            source.name,
            list(route),
            source.color,
            side=side,
            locked=source.locked,
            group=source.group,
        )

    def _candidate_via_points(self, start: GridPoint, end: GridPoint, side: str, other_side: str, limit: int = 18) -> list[GridPoint]:
        blocked_side = self._route_blocked_cells_for_side(side, start, end)
        blocked_other = self._route_blocked_cells_for_side(other_side, start, end)
        rows, cols = self.layout_model.rows, self.layout_model.cols
        sr, sc = start; er, ec = end
        candidates: list[tuple[int, GridPoint]] = []
        for r in range(rows):
            for c in range(cols):
                pt = (r, c)
                if pt in {start, end} or pt in blocked_side or pt in blocked_other:
                    continue
                # Prefer places near the start/end corridor, but keep enough
                # candidates for the router to go around obstacles.
                dist_to_ends = min(abs(r - sr) + abs(c - sc), abs(r - er) + abs(c - ec))
                corridor = abs((r - sr) * (ec - sc) - (c - sc) * (er - sr)) if start != end else 0
                candidates.append((dist_to_ends * 8 + corridor, pt))
        candidates.sort(key=lambda item: item[0])
        return [pt for _, pt in candidates[:limit]]

    def _best_cross_side_route(
        self,
        wire: Wire,
        start: GridPoint,
        end: GridPoint,
        *,
        target_indexes: set[int],
        occupied_by_side: Dict[str, set[GridPoint]],
    ) -> Optional[tuple[int, GridPoint, GridPoint, list[GridPoint], list[GridPoint], list[GridPoint]]]:
        side = wire.side
        other_side = "back" if side == "front" else "front"
        candidates = self._candidate_via_points(start, end, side, other_side)
        if len(candidates) < 2:
            return None
        near_start = sorted(candidates, key=lambda pt: abs(pt[0]-start[0]) + abs(pt[1]-start[1]))[:10]
        near_end = sorted(candidates, key=lambda pt: abs(pt[0]-end[0]) + abs(pt[1]-end[1]))[:10]
        best: Optional[tuple[int, GridPoint, GridPoint, list[GridPoint], list[GridPoint], list[GridPoint]]] = None
        for via_a in near_start:
            route_a = self._suggest_route_on_side(start, via_a, side, ignore_wire_indexes=target_indexes, extra_occupied=occupied_by_side.get(side, set()))
            if len(route_a) < 2:
                continue
            occ_side_a = set(occupied_by_side.get(side, set())) | self._route_cells(route_a)
            for via_b in near_end:
                if via_a == via_b:
                    continue
                route_b = self._suggest_route_on_side(via_a, via_b, other_side, ignore_wire_indexes=target_indexes, extra_occupied=occupied_by_side.get(other_side, set()))
                if len(route_b) < 2:
                    continue
                route_c = self._suggest_route_on_side(via_b, end, side, ignore_wire_indexes=target_indexes, extra_occupied=occ_side_a)
                if len(route_c) < 2:
                    continue
                score = self._route_cost(route_a) + self._route_cost(route_b) + self._route_cost(route_c) + 55
                if best is None or score < best[0]:
                    best = (score, via_a, via_b, route_a, route_b, route_c)
        return best

    def optimize_wire_routes(self, *, scope: str = "current_side", allow_cross_side: bool = False) -> tuple[int, int, int]:
        """Reroute existing wires while preserving their endpoints.

        Returns (updated_wire_count, failed_wire_count, added_via_count).  Scope
        is either ``current_side`` or ``whole_board``.  If cross-side routing is
        allowed, a wire may be split into same-color segments on both sides with
        vias inserted at the transitions.
        """
        if scope not in {"current_side", "whole_board"}:
            scope = "current_side"
        target_indexes = {
            i for i, wire in enumerate(self.layout_model.wires)
            if len(wire.points) >= 2
            and not wire.locked
            and not self._is_item_hidden_by_group(wire)
            and (scope == "whole_board" or wire.side == self.side)
        }
        if not target_indexes:
            return (0, 0, 0)

        self.beforeLayoutChange.emit("Suggest all wires")
        original_wires = list(self.layout_model.wires)
        new_wires: list[Wire] = []
        kept_wires = [wire for i, wire in enumerate(original_wires) if i not in target_indexes]
        occupied_by_side: Dict[str, set[GridPoint]] = {
            "front": self._wire_cells_from_wires(kept_wires, "front"),
            "back": self._wire_cells_from_wires(kept_wires, "back"),
        }
        routed = 0
        failed = 0
        added_vias = 0

        for i, source in enumerate(original_wires):
            if i not in target_indexes:
                new_wires.append(source)
                continue
            start, end = source.points[0], source.points[-1]
            direct = self._suggest_route_on_side(
                start,
                end,
                source.side,
                ignore_wire_indexes=target_indexes,
                extra_occupied=occupied_by_side.get(source.side, set()),
            )
            direct_score = self._route_cost(direct) if len(direct) >= 2 else 10**9
            via_choice = None
            if allow_cross_side:
                via_choice = self._best_cross_side_route(source, start, end, target_indexes=target_indexes, occupied_by_side=occupied_by_side)

            if via_choice is not None and via_choice[0] < direct_score:
                _, via_a, via_b, route_a, route_b, route_c = via_choice
                for point in (via_a, via_b):
                    if not self._via_exists(point):
                        self.layout_model.vias.append(Via(point[0], point[1], color=source.color, group=source.group))
                        added_vias += 1
                other_side = "back" if source.side == "front" else "front"
                segments = [
                    self._copy_wire_with_route(source, route_a, source.side),
                    self._copy_wire_with_route(source, route_b, other_side),
                    self._copy_wire_with_route(source, route_c, source.side),
                ]
                for seg in segments:
                    new_wires.append(seg)
                    occupied_by_side.setdefault(seg.side, set()).update(self._route_cells(seg.points))
                routed += 1
            elif len(direct) >= 2:
                wire = self._copy_wire_with_route(source, direct, source.side)
                new_wires.append(wire)
                occupied_by_side.setdefault(wire.side, set()).update(self._route_cells(wire.points))
                routed += 1
            else:
                new_wires.append(source)
                occupied_by_side.setdefault(source.side, set()).update(self._route_cells(source.points))
                failed += 1

        self.layout_model.wires = new_wires
        self.selected.clear()
        self.layoutChanged.emit()
        self.selectionChanged.emit()
        self.update()
        return routed, failed, added_vias

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

    def _draw_tool_preview(self, painter: QPainter) -> None:
        """Live placement preview for the active creation tool."""
        grid = self.hover_grid
        if not grid:
            return
        painter.save()
        if self.tool == "component":
            t = self.new_component_template
            comp = Component(
                t.name,
                grid[0],
                grid[1],
                t.width,
                t.height,
                t.color,
                side=self.side,
                rotation=t.rotation,
                show_name=t.show_name,
                show_pin_names=t.show_pin_names,
                pins=[ComponentPin(p.name, p.row, p.col) for p in t.pins],
                jumpers=[j for j in t.jumpers],
                component_type=t.component_type,
                value=t.value,
                category=t.category,
                orientation_note=t.orientation_note,
            )
            self._draw_component(painter, -1, comp, alpha=0.48, ghost=False)
            rect = self._component_rect(comp).adjusted(-4, -4, 4, 4)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            invalid = False
            for r in range(comp.row, comp.row + comp.height):
                for c in range(comp.col, comp.col + comp.width):
                    if not board_contains(r, c, self.layout_model.rows, self.layout_model.cols):
                        invalid = True
            painter.setPen(QPen(QColor("#ef4444") if invalid else QColor("#2457d6"), 2, Qt.PenStyle.DashLine))
            painter.drawRoundedRect(rect, 10, 10)
            painter.setPen(QColor("#0f172a"))
            painter.setFont(QFont("Segoe UI", max(8, int(9 * self.zoom)), QFont.Weight.Bold))
            painter.drawText(rect.adjusted(8, -22, -8, -4), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, comp.name)
        elif self.tool == "via":
            p = self.grid_to_view(*grid)
            painter.setBrush(self._color("#9c27b0", 0.42))
            painter.setPen(QPen(QColor("#ffffff"), max(1.5, 2 * self.zoom)))
            painter.drawEllipse(p, max(6, 8 * self.zoom), max(6, 8 * self.zoom))
            painter.setBrush(QColor("#ffffff"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(p, max(2, 3 * self.zoom), max(2, 3 * self.zoom))
        elif self.tool == "annotation":
            p = self.grid_to_view(*grid)
            rect = QRectF(p.x(), p.y() - 18 * self.zoom, max(80, 120 * self.zoom), max(24, 34 * self.zoom))
            painter.setBrush(QColor(255, 255, 255, 185))
            painter.setPen(QPen(QColor("#0ea5e9"), 1.5, Qt.PenStyle.DashLine))
            painter.drawRoundedRect(rect, 8, 8)
            painter.setPen(QColor("#0f172a"))
            painter.drawText(rect.adjusted(8, 2, -8, -2), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "New note…")
        elif self.tool == "keepout":
            if self.keepout_start:
                p1 = self.grid_to_view(*self.keepout_start)
                p2 = self.grid_to_view(*grid)
                rect = QRectF(min(p1.x(), p2.x()), min(p1.y(), p2.y()), abs(p2.x() - p1.x()), abs(p2.y() - p1.y())).adjusted(-9*self.zoom, -9*self.zoom, 9*self.zoom, 9*self.zoom)
                painter.setBrush(self._color(self.current_keepout_color, 0.14))
                painter.setPen(QPen(self._color(self.current_keepout_color, 0.92), 2, Qt.PenStyle.DashLine))
                painter.drawRoundedRect(rect, 10, 10)
        painter.restore()

    def _draw_warning_focus_overlay(self, painter: QPainter) -> None:
        """Dim the design so warning markers become the visual focus."""
        if not self.warning_focus_mode or not self.warning_points:
            return
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(15, 23, 42, 82))
        painter.drawRect(self.rect())
        for r, c in self.warning_points:
            if not board_contains(r, c, self.layout_model.rows, self.layout_model.cols):
                continue
            p = self.grid_to_view(r, c)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            painter.setBrush(Qt.GlobalColor.transparent)
            painter.drawEllipse(p, max(24, 30*self.zoom), max(24, 30*self.zoom))
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.restore()

    def _selection_union_rect(self) -> Optional[QRectF]:
        rects: list[QRectF] = []
        for kind, idx in self.selected:
            rect = self._rect_for_selection_like(kind, idx)
            if rect is not None:
                rects.append(rect)
        if not rects:
            return None
        rect = QRectF(rects[0])
        for other in rects[1:]:
            rect = rect.united(other)
        return rect

    def _draw_selection_quick_controls(self, painter: QPainter) -> None:
        """Small canvas controls for common selected-component actions."""
        self._quick_button_rects.clear()
        if self.tool != "select" or len(self.selected) != 1:
            return
        kind, idx = next(iter(self.selected))
        if kind != "component" or not (0 <= idx < len(self.layout_model.components)):
            return
        rect = self._component_rect(self.layout_model.components[idx])
        labels = [("rotate", "⟳"), ("side", "⇄"), ("lock", "🔒" if not self.layout_model.components[idx].locked else "🔓"), ("pins", "Pins")]
        size = max(28.0, 30.0 * min(self.zoom, 1.25))
        gap = 6.0
        total = len(labels) * size + (len(labels) - 1) * gap
        x = rect.center().x() - total / 2
        y = rect.top() - size - 14
        if y < 8:
            y = rect.bottom() + 14
        painter.save()
        painter.setFont(QFont("Segoe UI", max(8, int(9 * self.zoom)), QFont.Weight.Bold))
        for action, label in labels:
            brect = QRectF(x, y, size if action != "pins" else size * 1.55, size)
            self._quick_button_rects[action] = brect
            painter.setBrush(QColor(255, 255, 255, 235))
            painter.setPen(QPen(QColor("#2457d6"), 1.4))
            painter.drawRoundedRect(brect, 9, 9)
            painter.setPen(QColor("#0f172a"))
            painter.drawText(brect, Qt.AlignmentFlag.AlignCenter, label)
            x += brect.width() + gap
        painter.restore()

    def _quick_action_at(self, pos: QPointF) -> Optional[str]:
        for action, rect in self._quick_button_rects.items():
            if rect.contains(pos):
                return action
        return None

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

    def _split_name_prefix_number(self, name: str) -> tuple[str, Optional[int]]:
        name = (name or "Part").strip() or "Part"
        pos = len(name)
        while pos > 0 and name[pos - 1].isdigit():
            pos -= 1
        prefix = name[:pos] or name
        number = int(name[pos:]) if pos < len(name) else None
        return prefix, number

    def _next_component_name(self, template_name: str, component_type: str = "") -> str:
        prefix, explicit_number = self._split_name_prefix_number(template_name)
        # Treat simple library prefixes as auto-numbering names. If the user has
        # entered a descriptive name with a number, continue that sequence too.
        used = []
        for comp in self.layout_model.components:
            p, n = self._split_name_prefix_number(comp.name)
            if p == prefix and n is not None:
                used.append(n)
        if used or explicit_number is None:
            return f"{prefix}{(max(used) + 1) if used else 1}"
        candidate = f"{prefix}{explicit_number}"
        if not any(comp.name == candidate for comp in self.layout_model.components):
            return candidate
        return f"{prefix}{max(used or [explicit_number]) + 1}"

    def nudge_selected(self, dr: int, dc: int) -> None:
        if not self.selected:
            return
        self._cache_drag_originals()
        if not self.drag_originals:
            return
        self.beforeLayoutChange.emit("Nudge selected")
        self._apply_drag_delta(dr, dc)
        self.drag_originals.clear()
        self.layoutChanged.emit()
        self.update()

    def mousePressEvent(self, event: QMouseEvent):  # noqa: N802
        self.setFocus()
        pos = QPointF(event.position())
        if event.button() == Qt.MouseButton.MiddleButton or (event.button() == Qt.MouseButton.RightButton and self.tool == "select"):
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
            quick = self._quick_action_at(pos)
            if quick:
                self.quickActionRequested.emit(quick)
                return
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
                self.drag_start_grid = grid or self.view_to_nearest_grid(pos)
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
            comp_name = self._next_component_name(t.name, t.component_type)
            comp = Component(comp_name, row, col, t.width, t.height, t.color, side=self.side, rotation=t.rotation, show_name=t.show_name, show_pin_names=t.show_pin_names,
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
        if self.tool == "select" and self.drag_start_grid and self.drag_originals:
            drag_grid = grid or self.view_to_nearest_grid(pos)
            if not drag_grid:
                return
            if not self._drag_snapshot_taken:
                self.beforeLayoutChange.emit("Move selected items")
                self._drag_snapshot_taken = True
            dr = drag_grid[0] - self.drag_start_grid[0]
            dc = drag_grid[1] - self.drag_start_grid[1]
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
            self.deleteRequested.emit()
        elif event.key() in {Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down}:
            step = 5 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
            delta = {
                Qt.Key.Key_Left: (0, -step),
                Qt.Key.Key_Right: (0, step),
                Qt.Key.Key_Up: (-step, 0),
                Qt.Key.Key_Down: (step, 0),
            }[event.key()]
            self.nudge_selected(*delta)
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
                self.drag_originals[sel] = (item.row1, item.col1, item.row2, item.col2)

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
            elif sel[0] == "keepout":
                r1, c1, r2, c2 = original
                min_r, max_r = min(r1, r2), max(r1, r2)
                min_c, max_c = min(c1, c2), max(c1, c2)
                height = max_r - min_r
                width = max_c - min_c
                new_min_r = max(0, min(self.layout_model.rows - 1 - height, min_r + dr))
                new_min_c = max(0, min(self.layout_model.cols - 1 - width, min_c + dc))
                rr1, rr2 = new_min_r, new_min_r + height
                cc1, cc2 = new_min_c, new_min_c + width
                item.row1, item.row2 = (rr1, rr2) if r1 <= r2 else (rr2, rr1)
                item.col1, item.col2 = (cc1, cc2) if c1 <= c2 else (cc2, cc1)
