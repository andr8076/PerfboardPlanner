from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Iterable, Optional, Tuple

from PySide6.QtCore import Qt, QSize, QPointF
from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon, QKeySequence, QPixmap, QPainter, QPen, QShortcut, QBrush, QPainterPath
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolBar,
    QToolButton,
    QStyle,
    QInputDialog,
    QVBoxLayout,
    QWidget,
)

from ...core.bom import bom_csv, bom_rows
from ...core.checks import LayoutWarning, layout_warnings, pin_connection_counts
from ...core.models import Annotation, Component, ComponentJumper, ComponentPin, KeepoutZone, Layout, Via, Wire
from ...core.storage import layout_from_dict, layout_to_dict, load_layout_file, save_layout_file
from .board_view import BoardView, Selection
from .style import APP_STYLESHEET


class CompactTabWidget(QTabWidget):
    """A QTabWidget that is willing to shrink inside a dock.

    Qt's default tab widget minimum size can be dominated by the widest tab page
    or a long tab row. That made the left project dock refuse to collapse after
    the Groups panel gained longer controls. The content can scroll/clip safely,
    so the dock should be allowed to get narrow.
    """

    def minimumSizeHint(self) -> QSize:  # pragma: no cover - UI sizing helper
        hint = super().minimumSizeHint()
        return QSize(150, hint.height())

    def sizeHint(self) -> QSize:  # pragma: no cover - UI sizing helper
        hint = super().sizeHint()
        return QSize(260, hint.height())


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Perfboard Planner v34")
        self.resize(1500, 940)
        self.setMinimumSize(980, 640)
        self.current_path: Optional[Path] = None
        self.layout_model = Layout()
        self.undo_stack: list[dict] = []
        self.redo_stack: list[dict] = []
        self.max_pin_connections = 2
        self.count_pin_connections_both_sides = True
        self._building_inspector = False
        self._warnings: list[LayoutWarning] = []
        self.muted_warning_signatures: set[str] = set()
        self._build_ui()
        self._wire_events()
        self._refresh_all()

    def _build_ui(self) -> None:
        self.board = BoardView()
        self.board.set_layout(self.layout_model)

        self.canvas_shell = QWidget()
        shell_layout = QVBoxLayout(self.canvas_shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)

        self.canvas_topbar = QFrame()
        self.canvas_topbar.setObjectName("CanvasTopBar")
        topbar_layout = QHBoxLayout(self.canvas_topbar)
        topbar_layout.setContentsMargins(10, 6, 10, 6)
        topbar_layout.setSpacing(8)
        topbar_layout.addWidget(QLabel("Wire colors"))
        self.color_swatch_area = QWidget()
        self.color_swatch_layout = QHBoxLayout(self.color_swatch_area)
        self.color_swatch_layout.setContentsMargins(0, 0, 0, 0)
        self.color_swatch_layout.setSpacing(6)
        topbar_layout.addWidget(self.color_swatch_area, 1)
        self.show_all_colors_btn = QToolButton()
        self.show_all_colors_btn.setText("All")
        self.show_all_colors_btn.setToolTip("Show all wire colors")
        topbar_layout.addWidget(self.show_all_colors_btn)
        shell_layout.addWidget(self.canvas_topbar)

        shell_layout.addWidget(self.board, 1)

        self.canvas_footer = QFrame()
        self.canvas_footer.setObjectName("CanvasFooter")
        footer_layout = QHBoxLayout(self.canvas_footer)
        footer_layout.setContentsMargins(10, 6, 10, 6)
        footer_layout.setSpacing(6)

        footer_layout.addWidget(QLabel("Side"))
        self.front_side_button = QToolButton(); self.front_side_button.setText("Front"); self.front_side_button.setCheckable(True); self.front_side_button.setChecked(True); self.front_side_button.setToolTip("Show the front side")
        self.back_side_button = QToolButton(); self.back_side_button.setText("Back"); self.back_side_button.setCheckable(True); self.back_side_button.setToolTip("Show the physically mirrored back side")
        self.side_button_group = QActionGroup(self); self.side_button_group.setExclusive(True)
        self.front_side_action = QAction("Front", self); self.front_side_action.setCheckable(True); self.front_side_action.setChecked(True)
        self.back_side_action = QAction("Back", self); self.back_side_action.setCheckable(True)
        self.side_button_group.addAction(self.front_side_action); self.side_button_group.addAction(self.back_side_action)
        self.front_side_button.setDefaultAction(self.front_side_action); self.back_side_button.setDefaultAction(self.back_side_action)
        footer_layout.addWidget(self.front_side_button); footer_layout.addWidget(self.back_side_button)

        footer_layout.addStretch(1)
        self.footer_route_button = QToolButton()
        self.footer_route_button.setText("Suggest route")
        self.footer_route_button.setToolTip("Click, then choose start and destination holes on the board")
        self.footer_route_button.setVisible(False)
        footer_layout.addWidget(self.footer_route_button)
        footer_layout.addStretch(1)
        self.zoom_out_button = QToolButton(); self.zoom_out_button.setText("−"); self.zoom_out_button.setToolTip("Zoom out")
        self.zoom_label = QLabel("100%")
        self.zoom_label.setMinimumWidth(52)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_in_button = QToolButton(); self.zoom_in_button.setText("+"); self.zoom_in_button.setToolTip("Zoom in")
        self.zoom_reset_button = QToolButton(); self.zoom_reset_button.setText("100%"); self.zoom_reset_button.setToolTip("Reset zoom")
        self.zoom_fit_button = QToolButton(); self.zoom_fit_button.setText("Fit"); self.zoom_fit_button.setToolTip("Fit board to view")
        for w in [self.zoom_out_button, self.zoom_label, self.zoom_in_button, self.zoom_reset_button, self.zoom_fit_button]:
            footer_layout.addWidget(w)
        shell_layout.addWidget(self.canvas_footer)

        self.setCentralWidget(self.canvas_shell)
        self._build_toolbar()
        self._build_left_dock()
        self._build_right_dock()
        self.statusBar().showMessage("Ready")

    def _simple_icon(self, color: str, shape: str = "dot") -> QIcon:
        pixmap = QPixmap(20, 20)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        qcolor = QColor(color)
        painter.setPen(QPen(qcolor.darker(120), 2))
        painter.setBrush(qcolor)
        if shape == "line":
            painter.setPen(QPen(qcolor, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(4, 15, 16, 5)
        elif shape == "rect":
            painter.drawRoundedRect(4, 4, 12, 12, 3, 3)
        elif shape == "via":
            painter.drawEllipse(4, 4, 12, 12)
            painter.setBrush(QColor("#ffffff"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(8, 8, 4, 4)
        elif shape == "note":
            painter.drawRoundedRect(4, 5, 12, 10, 2, 2)
            painter.setPen(QPen(QColor("#ffffff"), 1))
            painter.drawLine(7, 8, 13, 8)
            painter.drawLine(7, 11, 12, 11)
        else:
            painter.drawEllipse(5, 5, 10, 10)
        painter.end()
        return QIcon(pixmap)

    def _warning_icon(self, color: str, *, muted: bool = False) -> QIcon:
        pixmap = QPixmap(22, 22)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        qcolor = QColor(color)
        painter.setPen(QPen(qcolor.darker(125), 1.8))
        painter.setBrush(qcolor)
        if muted:
            painter.drawEllipse(3, 3, 16, 16)
            painter.setPen(QPen(QColor(255, 255, 255), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(7, 7, 15, 15)
        else:
            path = QPainterPath()
            path.moveTo(11, 2.5)
            path.lineTo(20, 18.5)
            path.lineTo(2, 18.5)
            path.closeSubpath()
            painter.drawPath(path)
            painter.setPen(QPen(QColor(255, 255, 255), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(11, 7, 11, 12)
            painter.drawPoint(11, 16)
        painter.end()
        return QIcon(pixmap)

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main")
        toolbar.setIconSize(QSize(20, 20))
        toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toolbar.setMovable(False)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)

        self.new_action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon), "New", self); self.new_action.setShortcut(QKeySequence.StandardKey.New)
        self.open_action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton), "Open", self); self.open_action.setShortcut(QKeySequence.StandardKey.Open)
        self.save_action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton), "Save", self); self.save_action.setShortcut(QKeySequence.StandardKey.Save)
        self.save_as_action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_DriveFDIcon), "Save As", self)
        for action in [self.new_action, self.open_action, self.save_action, self.save_as_action]:
            toolbar.addAction(action)
        toolbar.addSeparator()

        self.undo_action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowBack), "Undo", self); self.undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        self.redo_action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowForward), "Redo", self); self.redo_action.setShortcut(QKeySequence.StandardKey.Redo)
        toolbar.addAction(self.undo_action); toolbar.addAction(self.redo_action)
        toolbar.addSeparator()

        self.mode_group = QActionGroup(self)
        self.mode_actions: dict[str, QAction] = {}
        mode_defs = [
            ("Select", "select", "#2457d6", "dot"),
            ("Part", "component", "#8b5cf6", "rect"),
            ("Wire", "wire", "#ef4444", "line"),
            ("Via", "via", "#a855f7", "via"),
            ("Note", "annotation", "#0ea5e9", "note"),
            ("Keepout", "keepout", "#f97316", "rect"),
        ]
        for text, tool, color, shape in mode_defs:
            act = QAction(self._simple_icon(color, shape), text, self)
            act.setCheckable(True); act.setData(tool)
            self.mode_group.addAction(act); toolbar.addAction(act); self.mode_actions[tool] = act
        self.mode_actions["select"].setChecked(True)
        toolbar.addSeparator()

        self.warnings_action = QAction(self._warning_icon("#94a3b8"), "Warnings", self)
        self.bom_action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView), "BOM", self)
        toolbar.addAction(self.warnings_action); toolbar.addAction(self.bom_action)

    def _build_left_dock(self) -> None:
        dock = QDockWidget("Project", self)
        dock.setObjectName("ProjectDock")
        dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        dock.setMinimumWidth(150)
        dock.setMinimumSize(150, 160)
        dock.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        tabs = CompactTabWidget()
        tabs.setMinimumWidth(0)
        tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        dock.setWidget(tabs)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
        self.left_tabs = tabs
        tabs.setUsesScrollButtons(True)
        tabs.setElideMode(Qt.TextElideMode.ElideRight)

        # Objects tree
        objects_tab = QWidget(); objects_layout = QVBoxLayout(objects_tab)
        self.object_tree = QListWidget(); self.object_tree.setAlternatingRowColors(True)
        objects_layout.addWidget(QLabel("Objects")); objects_layout.addWidget(self.object_tree)
        row = QHBoxLayout()
        self.delete_button = QPushButton("Delete")
        self.lock_button = QPushButton("Lock / unlock")
        row.addWidget(self.delete_button); row.addWidget(self.lock_button)
        objects_layout.addLayout(row)
        tabs.addTab(objects_tab, "Objects")

        # Groups / modules
        groups_tab = QWidget(); groups_layout = QVBoxLayout(groups_tab)
        groups_layout.addWidget(QLabel("Groups / modules"))
        self.group_list = QListWidget(); self.group_list.setAlternatingRowColors(True)
        groups_layout.addWidget(self.group_list, 1)
        group_row1 = QHBoxLayout()
        self.select_group_button = QPushButton("Select group")
        self.hide_group_button = QPushButton("Hide / show")
        group_row1.addWidget(self.select_group_button); group_row1.addWidget(self.hide_group_button)
        groups_layout.addLayout(group_row1)
        group_row2 = QHBoxLayout()
        self.lock_group_button = QPushButton("Lock / unlock")
        self.rename_group_button = QPushButton("Rename")
        group_row2.addWidget(self.lock_group_button); group_row2.addWidget(self.rename_group_button)
        groups_layout.addLayout(group_row2)
        self.assign_group_button = QPushButton("Assign selected to this group")
        self.clear_group_button = QPushButton("Clear group from selected")
        groups_layout.addWidget(self.assign_group_button)
        groups_layout.addWidget(self.clear_group_button)
        group_tip = QLabel("Tip: groups are modules. Select a group to move/delete/lock it together, or hide completed sections.")
        group_tip.setWordWrap(True)
        group_tip.setObjectName("MutedLabel")
        groups_layout.addWidget(group_tip)
        tabs.addTab(groups_tab, "Groups")

        # Warnings
        warnings_tab = QWidget(); warnings_layout = QVBoxLayout(warnings_tab)
        self.warning_summary_label = QLabel("Layout OK")
        self.warning_list = QListWidget(); self.warning_list.setAlternatingRowColors(True)
        warnings_layout.addWidget(self.warning_summary_label); warnings_layout.addWidget(self.warning_list)
        warning_buttons = QHBoxLayout()
        self.mute_warning_button = QPushButton("Mute selected")
        self.mute_all_warnings_button = QPushButton("Mute current")
        self.clear_muted_warnings_button = QPushButton("Clear muted")
        warning_buttons.addWidget(self.mute_warning_button)
        warning_buttons.addWidget(self.mute_all_warnings_button)
        warning_buttons.addWidget(self.clear_muted_warnings_button)
        warnings_layout.addLayout(warning_buttons)
        tabs.addTab(warnings_tab, "Warnings")

        # Library
        library_tab = QWidget(); lib_layout = QVBoxLayout(library_tab)
        lib_layout.addWidget(QLabel("Quick footprints"))
        self.library_list = QListWidget()
        for label in ["Resistor · 2-pin", "LED · 2-pin", "Diode · 2-pin", "DIP-8", "Pin header · 4", "Screw terminal · 2"]:
            self.library_list.addItem(label)
        lib_layout.addWidget(self.library_list)
        tabs.addTab(library_tab, "Library")

        self._make_project_dock_shrinkable(dock)

        self.bom_table = None

    def _make_project_dock_shrinkable(self, dock: QDockWidget) -> None:
        """Allow the left project dock to collapse to a useful narrow width."""
        for child in dock.findChildren(QWidget):
            child.setMinimumWidth(0)
            if isinstance(child, (QPushButton, QToolButton)):
                child.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            elif isinstance(child, QLabel):
                child.setWordWrap(True)
                child.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            elif isinstance(child, (QListWidget, QTabWidget)):
                child.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def _build_right_dock(self) -> None:
        dock = QDockWidget("Inspector", self)
        dock.setObjectName("InspectorDock")
        dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        dock.setMinimumWidth(340)
        self.inspector_scroll = QScrollArea(); self.inspector_scroll.setWidgetResizable(True)
        self.inspector_body = QWidget(); self.inspector_layout = QVBoxLayout(self.inspector_body); self.inspector_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.inspector_scroll.setWidget(self.inspector_body)
        dock.setWidget(self.inspector_scroll)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)

    def _wire_events(self) -> None:
        self.new_action.triggered.connect(self.new_file)
        self.open_action.triggered.connect(self.open_file)
        self.save_action.triggered.connect(self.save_file)
        self.save_as_action.triggered.connect(self.save_file_as)
        self.undo_action.triggered.connect(self.undo)
        self.redo_action.triggered.connect(self.redo)
        self.mode_group.triggered.connect(lambda action: self.set_tool(action.data()))
        self.front_side_action.triggered.connect(lambda: self.set_side("front"))
        self.back_side_action.triggered.connect(lambda: self.set_side("back"))
        self.warnings_action.triggered.connect(lambda: self.left_tabs.setCurrentIndex(2))
        self.bom_action.triggered.connect(self.show_bom_dialog)
        self.board.beforeLayoutChange.connect(self.push_undo)
        self.board.layoutChanged.connect(self._on_layout_changed)
        self.board.selectionChanged.connect(self.populate_inspector)
        self.board.statusMessage.connect(self.statusBar().showMessage)
        self.board.toolRequested.connect(self.set_tool)
        self.board.zoomChanged.connect(self.update_zoom_label)
        self.board.annotationRequested.connect(self.create_annotation)
        self.board.itemActivated.connect(lambda kind, idx: self.populate_inspector())
        self.delete_button.clicked.connect(self.board.delete_selected)
        self.lock_button.clicked.connect(self.toggle_lock_selected)
        self.object_tree.itemClicked.connect(self.select_from_object_tree)
        self.warning_list.itemClicked.connect(self.focus_warning_item)
        self.mute_warning_button.clicked.connect(self.mute_selected_warning)
        self.mute_all_warnings_button.clicked.connect(self.mute_all_current_warnings)
        self.clear_muted_warnings_button.clicked.connect(self.clear_muted_warnings)
        self.show_all_colors_btn.clicked.connect(self.show_all_wire_colors)
        self.footer_route_button.clicked.connect(self.start_route_suggestion)
        self.library_list.itemDoubleClicked.connect(self.apply_library_preset)
        self.group_list.itemDoubleClicked.connect(lambda item: self.select_group(item.data(Qt.ItemDataRole.UserRole)))
        self.select_group_button.clicked.connect(lambda: self.select_group(self.current_group_name()))
        self.hide_group_button.clicked.connect(lambda: self.toggle_group_hidden(self.current_group_name()))
        self.lock_group_button.clicked.connect(lambda: self.toggle_group_locked(self.current_group_name()))
        self.rename_group_button.clicked.connect(lambda: self.rename_group(self.current_group_name()))
        self.assign_group_button.clicked.connect(lambda: self.assign_selected_to_group(self.current_group_name()))
        self.clear_group_button.clicked.connect(self.clear_group_from_selected)
        self.zoom_out_button.clicked.connect(lambda: self.board.zoom_out())
        self.zoom_in_button.clicked.connect(lambda: self.board.zoom_in())
        self.zoom_reset_button.clicked.connect(lambda: self.board.reset_zoom())
        self.zoom_fit_button.clicked.connect(self.board.fit_to_view)
        self.escape_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self.escape_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self.escape_shortcut.activated.connect(lambda: self.set_tool("select"))

    def update_zoom_label(self, zoom: float) -> None:
        if hasattr(self, "zoom_label"):
            self.zoom_label.setText(f"{int(round(zoom * 100))}%")

    def set_tool(self, tool: str) -> None:
        self.board.set_tool(tool)
        if tool in self.mode_actions:
            self.mode_actions[tool].setChecked(True)
        if hasattr(self, "footer_route_button"):
            self.footer_route_button.setVisible(tool == "wire")
        self.statusBar().showMessage(f"Mode: {tool}")
        self.populate_inspector()

    def set_side(self, side: str) -> None:
        previous_side = getattr(self.board, "side", "front")
        self.board.set_side(side)
        self.front_side_action.setChecked(side == "front")
        self.back_side_action.setChecked(side == "back")
        if previous_side != side and hasattr(self.board, "start_flip_animation"):
            self.board.start_flip_animation(side)
        self.statusBar().showMessage(f"Viewing {side}; back side is physically mirrored")
        self.populate_inspector()

    def push_undo(self, label: str = "Edit") -> None:
        self.undo_stack.append(layout_to_dict(self.layout_model))
        if len(self.undo_stack) > 100:
            self.undo_stack.pop(0)
        self.redo_stack.clear()
        self._update_undo_actions()

    def undo(self) -> None:
        if not self.undo_stack:
            return
        self.redo_stack.append(layout_to_dict(self.layout_model))
        data = self.undo_stack.pop()
        self.layout_model = layout_from_dict(data)
        self.board.set_layout(self.layout_model)
        self._refresh_all()

    def redo(self) -> None:
        if not self.redo_stack:
            return
        self.undo_stack.append(layout_to_dict(self.layout_model))
        data = self.redo_stack.pop()
        self.layout_model = layout_from_dict(data)
        self.board.set_layout(self.layout_model)
        self._refresh_all()

    def _update_undo_actions(self) -> None:
        self.undo_action.setEnabled(bool(self.undo_stack)); self.redo_action.setEnabled(bool(self.redo_stack))

    def _on_layout_changed(self) -> None:
        self._refresh_all()

    def _refresh_all(self) -> None:
        self.board.pin_count_map = pin_connection_counts(self.layout_model, count_both_sides=self.count_pin_connections_both_sides)
        self._warnings = layout_warnings(self.layout_model, max_pin_connections=self.max_pin_connections, count_both_sides=self.count_pin_connections_both_sides)
        active_warnings = self.active_warnings()
        self.board.warning_points = {(w.row, w.col) for w in active_warnings if w.row is not None and w.col is not None}
        self.update_warning_action()
        self.board.update()
        self.populate_objects()
        self.populate_groups()
        self.populate_warnings()
        self.populate_wire_colors()
        self.populate_bom()
        self.populate_inspector()
        self._update_undo_actions()

    def new_file(self) -> None:
        self.push_undo("New file")
        self.layout_model = Layout()
        self.current_path = None
        self.board.set_layout(self.layout_model)
        self._refresh_all()

    def open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open layout", "", "JSON layout (*.json);;All files (*.*)")
        if not path:
            return
        try:
            self.layout_model = load_layout_file(path)
            self.current_path = Path(path)
            self.board.set_layout(self.layout_model)
            self.undo_stack.clear(); self.redo_stack.clear()
            self._refresh_all()
        except Exception as exc:
            QMessageBox.critical(self, "Open failed", str(exc))

    def save_file(self) -> None:
        if not self.current_path:
            self.save_file_as(); return
        try:
            save_layout_file(self.current_path, self.layout_model)
            self.statusBar().showMessage(f"Saved {self.current_path}")
        except Exception as exc:
            QMessageBox.critical(self, "Save failed", str(exc))

    def save_file_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save layout", "", "JSON layout (*.json);;All files (*.*)")
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        self.current_path = Path(path)
        self.save_file()

    def populate_objects(self) -> None:
        self.object_tree.blockSignals(True); self.object_tree.clear()
        sections = [
            ("component", self.layout_model.components, "Components"),
            ("wire", self.layout_model.wires, "Wires"),
            ("via", self.layout_model.vias, "Vias"),
            ("annotation", self.layout_model.annotations, "Annotations"),
            ("keepout", self.layout_model.keepouts, "Keepouts"),
        ]
        for kind, items, title in sections:
            header = QListWidgetItem(f"— {title} ({len(items)})")
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            self.object_tree.addItem(header)
            for i, item in enumerate(items):
                label = self.item_label(kind, i)
                lw = QListWidgetItem(label)
                lw.setData(Qt.ItemDataRole.UserRole, (kind, i))
                if (kind, i) in self.board.selected:
                    lw.setSelected(True)
                self.object_tree.addItem(lw)
        self.object_tree.blockSignals(False)

    def item_label(self, kind: str, idx: int) -> str:
        try:
            if kind == "component":
                c = self.layout_model.components[idx]
                lock = " 🔒" if c.locked else ""
                val = f" · {c.value}" if c.value else ""
                return f"{c.name}{val} · {c.side}{lock}"
            if kind == "wire":
                w = self.layout_model.wires[idx]
                return f"Wire {idx+1} · {w.color} · {w.side}{' 🔒' if w.locked else ''}"
            if kind == "via":
                v = self.layout_model.vias[idx]
                return f"Via r{v.row+1} c{v.col+1}{' 🔒' if v.locked else ''}"
            if kind == "annotation":
                a = self.layout_model.annotations[idx]
                return f"Note: {a.text[:24]} · {a.side}{' 🔒' if a.locked else ''}"
            if kind == "keepout":
                k = self.layout_model.keepouts[idx]
                return f"{k.name} · {k.side}{' 🔒' if k.locked else ''}"
        except Exception:
            pass
        return f"{kind} {idx+1}"

    def select_from_object_tree(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        self.board.selected = {data}
        self.board.selectionChanged.emit(); self.board.update()

    def _group_items(self, group: str) -> list[Selection]:
        if not group:
            return []
        result: list[Selection] = []
        for kind, coll in [("component", self.layout_model.components), ("wire", self.layout_model.wires), ("via", self.layout_model.vias), ("annotation", self.layout_model.annotations), ("keepout", self.layout_model.keepouts)]:
            for idx, item in enumerate(coll):
                if getattr(item, "group", "") == group:
                    result.append((kind, idx))
        return result

    def populate_groups(self) -> None:
        if not hasattr(self, "group_list"):
            return
        current = self.current_group_name()
        self.group_list.blockSignals(True)
        self.group_list.clear()
        groups = sorted({getattr(item, "group", "") for coll in [self.layout_model.components, self.layout_model.wires, self.layout_model.vias, self.layout_model.annotations, self.layout_model.keepouts] for item in coll if getattr(item, "group", "")})
        if not groups:
            item = QListWidgetItem("No groups yet")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.group_list.addItem(item)
        for group in groups:
            selections = self._group_items(group)
            hidden = group in self.board.hidden_groups
            locked_count = 0
            for kind, idx in selections:
                coll = self.board._collection(kind)
                if coll is not None and 0 <= idx < len(coll) and getattr(coll[idx], "locked", False):
                    locked_count += 1
            suffix = []
            if hidden:
                suffix.append("hidden")
            if locked_count:
                suffix.append(f"{locked_count} locked")
            label = f"{group} · {len(selections)} item{'s' if len(selections) != 1 else ''}" + (" · " + ", ".join(suffix) if suffix else "")
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, group)
            self.group_list.addItem(item)
            if group == current:
                item.setSelected(True)
                self.group_list.setCurrentItem(item)
        self.group_list.blockSignals(False)

    def current_group_name(self) -> str:
        if not hasattr(self, "group_list"):
            return ""
        item = self.group_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item and item.data(Qt.ItemDataRole.UserRole) else ""

    def select_group(self, group: str) -> None:
        if not group:
            return
        self.board.selected = set(self._group_items(group))
        self.board.selectionChanged.emit()
        self.board.update()
        self.statusBar().showMessage(f"Selected group: {group}")

    def toggle_group_hidden(self, group: str) -> None:
        if not group:
            return
        if group in self.board.hidden_groups:
            self.board.hidden_groups.remove(group)
        else:
            self.board.hidden_groups.add(group)
            self.board.selected = {sel for sel in self.board.selected if sel not in self._group_items(group)}
        self.populate_groups()
        self.board.selectionChanged.emit()
        self.board.update()

    def toggle_group_locked(self, group: str) -> None:
        if not group:
            return
        items = self._group_items(group)
        if not items:
            return
        should_lock = any(not getattr(self.board._collection(kind)[idx], "locked", False) for kind, idx in items if self.board._collection(kind) is not None and idx < len(self.board._collection(kind)))
        def do():
            for kind, idx in items:
                coll = self.board._collection(kind)
                if coll is not None and 0 <= idx < len(coll) and hasattr(coll[idx], "locked"):
                    setattr(coll[idx], "locked", should_lock)
        self._apply_change("Toggle group lock", do)

    def rename_group(self, group: str) -> None:
        if not group:
            return
        text, ok = QInputDialog.getText(self, "Rename group", "Group name:", text=group)
        if not ok or not text.strip():
            return
        new_name = text.strip()
        def do():
            for kind, idx in self._group_items(group):
                coll = self.board._collection(kind)
                if coll is not None and 0 <= idx < len(coll) and hasattr(coll[idx], "group"):
                    setattr(coll[idx], "group", new_name)
            if group in self.board.hidden_groups:
                self.board.hidden_groups.remove(group); self.board.hidden_groups.add(new_name)
        self._apply_change("Rename group", do)

    def assign_selected_to_group(self, group: str) -> None:
        if not self.board.selected:
            return
        if not group:
            group, ok = QInputDialog.getText(self, "Assign group", "Group name:")
            if not ok or not group.strip():
                return
            group = group.strip()
        def do():
            for kind, idx in self.board.selected:
                coll = self.board._collection(kind)
                if coll is not None and 0 <= idx < len(coll) and hasattr(coll[idx], "group"):
                    setattr(coll[idx], "group", group)
        self._apply_change("Assign group", do)
        self.left_tabs.setCurrentIndex(1)

    def clear_group_from_selected(self) -> None:
        if not self.board.selected:
            return
        def do():
            for kind, idx in self.board.selected:
                coll = self.board._collection(kind)
                if coll is not None and 0 <= idx < len(coll) and hasattr(coll[idx], "group"):
                    setattr(coll[idx], "group", "")
        self._apply_change("Clear group", do)

    def _warning_signature(self, warning: LayoutWarning) -> str:
        return "|".join([
            str(warning.code),
            str(warning.side),
            str(warning.row),
            str(warning.col),
            str(warning.item_kind),
            str(warning.item_index),
            str(warning.message),
        ])

    def active_warnings(self) -> list[LayoutWarning]:
        return [w for w in self._warnings if self._warning_signature(w) not in self.muted_warning_signatures]

    def muted_warnings(self) -> list[LayoutWarning]:
        return [w for w in self._warnings if self._warning_signature(w) in self.muted_warning_signatures]

    def update_warning_action(self) -> None:
        active_count = len(self.active_warnings())
        muted_count = len(self.muted_warnings())
        if active_count:
            self.warnings_action.setText(f"Warnings ({active_count})")
            self.warnings_action.setIcon(self._warning_icon("#f59e0b"))
            self.warnings_action.setToolTip(f"{active_count} active layout warning{'s' if active_count != 1 else ''}. {muted_count} muted.")
        elif muted_count:
            self.warnings_action.setText("Warnings muted")
            self.warnings_action.setIcon(self._warning_icon("#94a3b8", muted=True))
            self.warnings_action.setToolTip(f"No active warnings. {muted_count} warning{'s' if muted_count != 1 else ''} muted.")
        else:
            self.warnings_action.setText("Warnings")
            self.warnings_action.setIcon(self._warning_icon("#22c55e"))
            self.warnings_action.setToolTip("No layout warnings.")

    def populate_warnings(self) -> None:
        self.warning_list.clear()
        active = self.active_warnings()
        muted = self.muted_warnings()
        active_count = len(active)
        if active_count:
            text = f"{active_count} active layout warning{'s' if active_count != 1 else ''}"
            if muted:
                text += f" · {len(muted)} muted"
        elif muted:
            text = f"Layout OK · {len(muted)} muted warning{'s' if len(muted) != 1 else ''}"
        else:
            text = "Layout OK"
        self.warning_summary_label.setText(text)
        for warning in active:
            item = QListWidgetItem(f"{warning.code}: {warning.message}")
            item.setData(Qt.ItemDataRole.UserRole, warning)
            self.warning_list.addItem(item)
        if muted:
            header = QListWidgetItem(f"— Muted ({len(muted)})")
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            header.setForeground(QBrush(QColor("#64748b")))
            self.warning_list.addItem(header)
            for warning in muted:
                item = QListWidgetItem(f"muted · {warning.code}: {warning.message}")
                item.setData(Qt.ItemDataRole.UserRole, warning)
                item.setForeground(QBrush(QColor("#94a3b8")))
                self.warning_list.addItem(item)

    def mute_selected_warning(self) -> None:
        item = self.warning_list.currentItem()
        if not item:
            return
        warning = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(warning, LayoutWarning):
            return
        self.muted_warning_signatures.add(self._warning_signature(warning))
        self._refresh_all()
        self.statusBar().showMessage("Warning muted.")

    def mute_all_current_warnings(self) -> None:
        for warning in self.active_warnings():
            self.muted_warning_signatures.add(self._warning_signature(warning))
        self._refresh_all()
        self.statusBar().showMessage("Current warnings muted.")

    def clear_muted_warnings(self) -> None:
        self.muted_warning_signatures.clear()
        self._refresh_all()
        self.statusBar().showMessage("Muted warnings cleared.")

    def focus_warning_item(self, item: QListWidgetItem) -> None:
        warning = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(warning, LayoutWarning):
            return
        if warning.side in {"front", "back"}:
            self.set_side(warning.side)
        if warning.item_kind and warning.item_index is not None:
            self.board.selected = {(warning.item_kind, warning.item_index)}
        if warning.row is not None and warning.col is not None:
            target = self.board.grid_to_view(warning.row, warning.col)
            self.board.pan += QPointF(self.board.rect().center()) - target
        self.board.selectionChanged.emit(); self.board.update()

    def populate_wire_colors(self) -> None:
        while self.color_swatch_layout.count():
            item = self.color_swatch_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        counts: dict[str, dict[str, int]] = {}
        for wire in self.layout_model.wires:
            entry = counts.setdefault(wire.color, {"front": 0, "back": 0})
            entry[wire.side] = entry.get(wire.side, 0) + 1
        if not counts:
            empty = QLabel("No wires yet")
            empty.setObjectName("MutedLabel")
            self.color_swatch_layout.addWidget(empty)
            return
        for color, side_counts in sorted(counts.items()):
            total = side_counts.get("front", 0) + side_counts.get("back", 0)
            hidden = color in self.board.hidden_wire_colors
            button = QToolButton()
            button.setText("×" if hidden else (str(total) if total > 1 else ""))
            button.setToolTip(f"{color} · front {side_counts.get('front', 0)}, back {side_counts.get('back', 0)}. Click to hide/show.")
            button.setCheckable(True); button.setChecked(not hidden)
            fg = "#ffffff" if QColor(color).lightness() < 120 else "#111827"
            bg = "#ffffff" if hidden else color
            border = color if hidden else "rgba(15, 23, 42, 0.18)"
            button.setStyleSheet(f"QToolButton {{background: {bg}; color: {fg}; border-radius: 8px; min-width: 28px; max-width: 34px; min-height: 24px; font-weight: 800; border: 2px solid {border};}}")
            button.clicked.connect(lambda checked=False, c=color: self.toggle_wire_color(c))
            self.color_swatch_layout.addWidget(button)
        self.color_swatch_layout.addStretch(1)

    def toggle_wire_color(self, color: str) -> None:
        if color in self.board.hidden_wire_colors:
            self.board.hidden_wire_colors.remove(color)
        else:
            self.board.hidden_wire_colors.add(color)
        self.populate_wire_colors(); self.board.update()

    def show_all_wire_colors(self) -> None:
        self.board.hidden_wire_colors.clear(); self.populate_wire_colors(); self.board.update()

    def populate_bom(self) -> None:
        if not getattr(self, "bom_table", None):
            return
        rows = bom_rows(self.layout_model)
        self.bom_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, value in enumerate([row.quantity, row.category, row.component_type, row.value, row.names]):
                self.bom_table.setItem(r, c, QTableWidgetItem(str(value)))

    def show_bom_dialog(self) -> None:
        dlg = BomDialog(self.layout_model, self)
        dlg.exec()

    def export_bom_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export BOM CSV", "bom.csv", "CSV (*.csv);;All files (*.*)")
        if not path:
            return
        Path(path).write_text(bom_csv(self.layout_model, separator=";"), encoding="utf-8")
        self.statusBar().showMessage(f"Exported {path}")

    def clear_inspector(self) -> None:
        while self.inspector_layout.count():
            item = self.inspector_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def populate_inspector(self) -> None:
        if self._building_inspector:
            return
        self._building_inspector = True
        try:
            self.clear_inspector()
            selected = sorted(self.board.selected)
            if len(selected) == 1:
                kind, idx = selected[0]
                if kind == "component" and idx < len(self.layout_model.components):
                    self._inspect_component(idx)
                elif kind == "wire" and idx < len(self.layout_model.wires):
                    self._inspect_wire(idx)
                elif kind == "via" and idx < len(self.layout_model.vias):
                    self._inspect_via(idx)
                elif kind == "annotation" and idx < len(self.layout_model.annotations):
                    self._inspect_annotation(idx)
                elif kind == "keepout" and idx < len(self.layout_model.keepouts):
                    self._inspect_keepout(idx)
            elif len(selected) > 1:
                self._inspect_multi(selected)
            else:
                self._inspect_project_and_tools()
            self.inspector_layout.addStretch(1)
        finally:
            self._building_inspector = False

    def _card(self, title: str) -> tuple[QGroupBox, QFormLayout]:
        box = QGroupBox(title)
        form = QFormLayout(box)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.inspector_layout.addWidget(box)
        return box, form

    def _line(self, value: str, changed) -> QLineEdit:
        w = QLineEdit(value)
        w.editingFinished.connect(lambda: changed(w.text()))
        return w

    def _spin(self, value: int, lo: int, hi: int, changed) -> QSpinBox:
        w = QSpinBox(); w.setRange(lo, hi); w.setValue(value)
        w.valueChanged.connect(changed)
        return w

    def _combo(self, value: str, options: list[str], changed) -> QComboBox:
        w = QComboBox(); w.addItems(options)
        if value in options: w.setCurrentText(value)
        else: w.setEditText(value)
        w.currentTextChanged.connect(changed)
        return w

    def _color_button(self, value: str, changed) -> QPushButton:
        btn = QPushButton(value)
        btn.setStyleSheet(f"background:{value}; color:{'#fff' if QColor(value).lightness()<120 else '#111'}; border-radius:8px; padding:8px;")
        def choose():
            color = QColorDialog.getColor(QColor(value), self, "Choose color")
            if color.isValid():
                changed(color.name())
        btn.clicked.connect(choose)
        return btn

    def used_wire_colors(self) -> list[str]:
        colors = []
        for wire in self.layout_model.wires:
            color = wire.color or "#d00000"
            if color not in colors:
                colors.append(color)
        return sorted(colors, key=str.lower)

    def _wire_color_selector(self, value: str, changed) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        current = QPushButton(value)
        current.setMinimumWidth(86)

        def apply_style(color_text: str) -> None:
            qcolor = QColor(color_text or "#d00000")
            fg = "#fff" if qcolor.lightness() < 120 else "#111827"
            current.setText(color_text)
            current.setStyleSheet(f"background:{color_text}; color:{fg}; border-radius:8px; padding:8px; font-weight:700;")

        def apply_color(color_text: str) -> None:
            if not color_text:
                return
            apply_style(color_text)
            changed(color_text)
            self.populate_wire_colors()
            self.board.update()

        apply_style(value)
        current.clicked.connect(lambda: choose_custom())
        layout.addWidget(current)

        for color_text in self.used_wire_colors():
            swatch = QToolButton()
            swatch.setFixedSize(28, 26)
            swatch.setToolTip(f"Use existing wire color {color_text}")
            qcolor = QColor(color_text)
            mark = "✓" if color_text.lower() == value.lower() else ""
            swatch.setText(mark)
            fg = "#fff" if qcolor.lightness() < 120 else "#111827"
            swatch.setStyleSheet(f"QToolButton {{background:{color_text}; color:{fg}; border:1px solid #64748b; border-radius:7px; font-weight:900;}} QToolButton:hover {{border:2px solid #2457d6;}}")
            swatch.clicked.connect(lambda checked=False, c=color_text: apply_color(c))
            layout.addWidget(swatch)

        more = QToolButton()
        more.setText("…")
        more.setToolTip("Choose a custom wire color")
        layout.addWidget(more)

        def choose_custom() -> None:
            color = QColorDialog.getColor(QColor(value), self, "Choose wire color")
            if color.isValid():
                apply_color(color.name())

        more.clicked.connect(choose_custom)
        layout.addStretch(1)
        return row

    def _apply_change(self, label: str, fn) -> None:
        self.push_undo(label)
        fn()
        self._refresh_all()

    def _inspect_project_and_tools(self) -> None:
        _, form = self._card("Project")
        p = self.layout_model.project
        form.addRow("Title", self._line(p.title, lambda v: self._apply_change("Edit title", lambda: setattr(p, "title", v))))
        form.addRow("Author", self._line(p.author, lambda v: self._apply_change("Edit author", lambda: setattr(p, "author", v))))
        form.addRow("Revision", self._line(p.revision, lambda v: self._apply_change("Edit revision", lambda: setattr(p, "revision", v))))
        notes = QTextEdit(p.notes); notes.setMinimumHeight(70); notes.textChanged.connect(lambda: setattr(p, "notes", notes.toPlainText()))
        form.addRow("Notes", notes)

        _, view = self._card("View")
        cb_names = QCheckBox("Show component names"); cb_names.setChecked(self.board.show_component_names); cb_names.toggled.connect(lambda v: setattr(self.board, "show_component_names", v) or self.board.update())
        cb_pins = QCheckBox("Show pin names"); cb_pins.setChecked(self.board.show_pin_names); cb_pins.toggled.connect(lambda v: setattr(self.board, "show_pin_names", v) or self.board.update())
        cb_counts = QCheckBox("Show pin counts"); cb_counts.setChecked(self.board.show_pin_counts); cb_counts.toggled.connect(lambda v: setattr(self.board, "show_pin_counts", v) or self.board.update())
        cb_other_parts = QCheckBox("Ghost opposite-side parts"); cb_other_parts.setChecked(self.board.show_other_parts); cb_other_parts.toggled.connect(lambda v: setattr(self.board, "show_other_parts", v) or self.board.update())
        cb_other_pins = QCheckBox("Ghost opposite-side pins"); cb_other_pins.setChecked(self.board.show_other_pins); cb_other_pins.toggled.connect(lambda v: setattr(self.board, "show_other_pins", v) or self.board.update())
        cb_other_wires = QCheckBox("Ghost opposite-side wires"); cb_other_wires.setChecked(self.board.show_other_wires); cb_other_wires.toggled.connect(lambda v: setattr(self.board, "show_other_wires", v) or self.board.update())
        for cb in [cb_names, cb_pins, cb_counts, cb_other_parts, cb_other_pins, cb_other_wires]: view.addRow(cb)
        max_spin = self._spin(self.max_pin_connections, 0, 20, lambda v: self._set_pin_limit(v))
        both = QCheckBox("Count both sides for pin limits"); both.setChecked(self.count_pin_connections_both_sides); both.toggled.connect(lambda v: self._set_pin_count_both_sides(v))
        view.addRow("Max pin connections", max_spin); view.addRow(both)

        if self.board.tool == "component":
            self._inspect_new_component_template()
        elif self.board.tool == "wire":
            self._inspect_new_wire_tool()

    def _inspect_new_component_template(self) -> None:
        _, newpart = self._card("Part mode · new component")
        t = self.board.new_component_template
        newpart.addRow("Name", self._line(t.name, lambda v: setattr(t, "name", v)))
        newpart.addRow("Value", self._line(t.value, lambda v: setattr(t, "value", v)))
        newpart.addRow("Type", self._line(t.component_type, lambda v: setattr(t, "component_type", v)))
        newpart.addRow("Category", self._combo(t.category, ["Passive", "Semiconductor", "IC", "Connector", "Module", "Custom"], lambda v: setattr(t, "category", v)))
        newpart.addRow("Width", self._spin(t.width, 1, 100, lambda v: setattr(t, "width", v)))
        newpart.addRow("Height", self._spin(t.height, 1, 100, lambda v: setattr(t, "height", v)))
        newpart.addRow("Body angle", self._spin(t.rotation, 0, 359, lambda v: setattr(t, "rotation", v)))
        newpart.addRow("Color", self._color_button(t.color, lambda v: setattr(t, "color", v)))
        pins_btn = QPushButton("Edit template pins")
        pins_btn.clicked.connect(lambda: self.edit_component_pins(t, is_template=True))
        newpart.addRow(pins_btn)

    def _inspect_new_wire_tool(self) -> None:
        _, wire = self._card("Wire mode")
        wire.addRow("Color", self._wire_color_selector(self.board.current_wire_color, lambda v: setattr(self.board, "current_wire_color", v)))
        hint = QLabel("Use the bottom-bar Suggest route button, then click two board holes or pins.")
        hint.setObjectName("MutedLabel")
        wire.addRow(hint)

    def _set_pin_limit(self, value: int) -> None:
        self.max_pin_connections = value; self._refresh_all()

    def _set_pin_count_both_sides(self, value: bool) -> None:
        self.count_pin_connections_both_sides = bool(value); self._refresh_all()

    def _inspect_component(self, idx: int) -> None:
        comp = self.layout_model.components[idx]
        _, form = self._card("Component")
        form.addRow("Name", self._line(comp.name, lambda v: self._apply_change("Edit component", lambda: setattr(comp, "name", v))))
        form.addRow("Value", self._line(comp.value, lambda v: self._apply_change("Edit component", lambda: setattr(comp, "value", v))))
        form.addRow("Type", self._line(comp.component_type, lambda v: self._apply_change("Edit component", lambda: setattr(comp, "component_type", v))))
        form.addRow("Category", self._combo(comp.category, ["Passive", "Semiconductor", "IC", "Connector", "Module", "Custom"], lambda v: self._apply_change("Edit component", lambda: setattr(comp, "category", v))))
        form.addRow("Side", self._combo(comp.side, ["front", "back"], lambda v: self._apply_change("Move side", lambda: setattr(comp, "side", v))))
        form.addRow("Row", self._spin(comp.row + 1, 1, self.layout_model.rows, lambda v: self._apply_change("Move component", lambda: setattr(comp, "row", v-1))))
        form.addRow("Column", self._spin(comp.col + 1, 1, self.layout_model.cols, lambda v: self._apply_change("Move component", lambda: setattr(comp, "col", v-1))))
        form.addRow("Width", self._spin(comp.width, 1, 100, lambda v: self._apply_change("Resize component", lambda: setattr(comp, "width", v))))
        form.addRow("Height", self._spin(comp.height, 1, 100, lambda v: self._apply_change("Resize component", lambda: setattr(comp, "height", v))))
        form.addRow("Body angle", self._spin(comp.rotation, 0, 359, lambda v: self._apply_change("Rotate component", lambda: setattr(comp, "rotation", v))))
        form.addRow("Color", self._color_button(comp.color, lambda v: self._apply_change("Color component", lambda: setattr(comp, "color", v))))
        show_name = QCheckBox("Show this component name"); show_name.setChecked(comp.show_name); show_name.toggled.connect(lambda v: self._apply_change("Toggle name", lambda: setattr(comp, "show_name", bool(v))))
        show_pins = QCheckBox("Show this component's pin names"); show_pins.setChecked(comp.show_pin_names); show_pins.toggled.connect(lambda v: self._apply_change("Toggle pins", lambda: setattr(comp, "show_pin_names", bool(v))))
        locked = QCheckBox("Locked"); locked.setChecked(comp.locked); locked.toggled.connect(lambda v: self._apply_change("Lock component", lambda: setattr(comp, "locked", bool(v))))
        form.addRow(show_name); form.addRow(show_pins); form.addRow(locked)
        form.addRow("Group", self._line(comp.group, lambda v: self._apply_change("Set group", lambda: setattr(comp, "group", v))))
        form.addRow("Orientation", self._line(comp.orientation_note, lambda v: self._apply_change("Orientation note", lambda: setattr(comp, "orientation_note", v))))
        pin_btn = QPushButton(f"Edit pins ({len(comp.pins)}) / internal jumpers")
        pin_btn.clicked.connect(lambda: self.edit_component_pins(comp, is_template=False))
        form.addRow(pin_btn)

    def _inspect_wire(self, idx: int) -> None:
        wire = self.layout_model.wires[idx]
        _, form = self._card("Wire")
        form.addRow("Name", self._line(wire.name, lambda v: self._apply_change("Edit wire", lambda: setattr(wire, "name", v))))
        form.addRow("Side", self._combo(wire.side, ["front", "back"], lambda v: self._apply_change("Move wire side", lambda: setattr(wire, "side", v))))
        form.addRow("Color", self._wire_color_selector(wire.color, lambda v: self._apply_change("Wire color", lambda: setattr(wire, "color", v))))
        locked = QCheckBox("Locked"); locked.setChecked(wire.locked); locked.toggled.connect(lambda v: self._apply_change("Lock wire", lambda: setattr(wire, "locked", bool(v))))
        form.addRow(locked)
        form.addRow("Group", self._line(wire.group, lambda v: self._apply_change("Set group", lambda: setattr(wire, "group", v))))
        points = QTextEdit("\n".join(f"{r+1};{c+1}" for r,c in wire.points)); points.setMinimumHeight(90)
        apply_btn = QPushButton("Apply points")
        def apply_points():
            new = []
            for line in points.toPlainText().splitlines():
                if not line.strip(): continue
                parts = line.replace(",", ";").split(";")
                if len(parts) >= 2:
                    new.append((max(0, int(parts[0])-1), max(0, int(parts[1])-1)))
            if len(new) >= 2:
                self._apply_change("Edit wire points", lambda: setattr(wire, "points", new))
        apply_btn.clicked.connect(apply_points)
        form.addRow("Points r;c", points); form.addRow(apply_btn)

    def _inspect_via(self, idx: int) -> None:
        via = self.layout_model.vias[idx]
        _, form = self._card("Via")
        form.addRow("Name", self._line(via.name, lambda v: self._apply_change("Edit via", lambda: setattr(via, "name", v))))
        form.addRow("Row", self._spin(via.row + 1, 1, self.layout_model.rows, lambda v: self._apply_change("Move via", lambda: setattr(via, "row", v-1))))
        form.addRow("Column", self._spin(via.col + 1, 1, self.layout_model.cols, lambda v: self._apply_change("Move via", lambda: setattr(via, "col", v-1))))
        form.addRow("Color", self._color_button(via.color, lambda v: self._apply_change("Via color", lambda: setattr(via, "color", v))))

    def _inspect_annotation(self, idx: int) -> None:
        note = self.layout_model.annotations[idx]
        _, form = self._card("Annotation")
        form.addRow("Text", self._line(note.text, lambda v: self._apply_change("Edit note", lambda: setattr(note, "text", v))))
        form.addRow("Side", self._combo(note.side, ["front", "back"], lambda v: self._apply_change("Note side", lambda: setattr(note, "side", v))))
        form.addRow("Row", self._spin(note.row + 1, 1, self.layout_model.rows, lambda v: self._apply_change("Move note", lambda: setattr(note, "row", v-1))))
        form.addRow("Column", self._spin(note.col + 1, 1, self.layout_model.cols, lambda v: self._apply_change("Move note", lambda: setattr(note, "col", v-1))))
        form.addRow("Color", self._color_button(note.color, lambda v: self._apply_change("Note color", lambda: setattr(note, "color", v))))

    def _inspect_keepout(self, idx: int) -> None:
        zone = self.layout_model.keepouts[idx]
        _, form = self._card("Keepout zone")
        form.addRow("Name", self._line(zone.name, lambda v: self._apply_change("Edit keepout", lambda: setattr(zone, "name", v))))
        form.addRow("Side", self._combo(zone.side, ["both", "front", "back"], lambda v: self._apply_change("Keepout side", lambda: setattr(zone, "side", v))))
        for attr, label in [("row1", "Row 1"), ("col1", "Column 1"), ("row2", "Row 2"), ("col2", "Column 2")]:
            hi = self.layout_model.rows if "row" in attr else self.layout_model.cols
            form.addRow(label, self._spin(getattr(zone, attr)+1, 1, hi, lambda v, a=attr: self._apply_change("Resize keepout", lambda: setattr(zone, a, v-1))))
        form.addRow("Color", self._color_button(zone.color, lambda v: self._apply_change("Keepout color", lambda: setattr(zone, "color", v))))

    def _inspect_multi(self, selected: list[Selection]) -> None:
        _, form = self._card(f"Multi-select ({len(selected)} items)")
        side = self._combo("", ["", "front", "back"], lambda v: self.bulk_set_side(v))
        form.addRow("Set side", side)
        group_line = self._line("", lambda v: self.bulk_set_group(v))
        form.addRow("Set group", group_line)
        lock_btn = QPushButton("Toggle lock selected")
        lock_btn.clicked.connect(self.toggle_lock_selected)
        form.addRow(lock_btn)
        color_btn = QPushButton("Set selected color…")
        color_btn.clicked.connect(self.bulk_color_selected)
        form.addRow(color_btn)

    def bulk_set_side(self, side: str) -> None:
        if side not in {"front", "back"}: return
        def do():
            for kind, idx in self.board.selected:
                coll = self.board._collection(kind)
                if coll is not None and idx < len(coll) and hasattr(coll[idx], "side"):
                    setattr(coll[idx], "side", side)
        self._apply_change("Bulk side", do)

    def bulk_set_group(self, group: str) -> None:
        def do():
            for kind, idx in self.board.selected:
                coll = self.board._collection(kind)
                if coll is not None and idx < len(coll) and hasattr(coll[idx], "group"):
                    setattr(coll[idx], "group", group)
        self._apply_change("Bulk group", do)

    def bulk_color_selected(self) -> None:
        color = QColorDialog.getColor(QColor("#2457d6"), self, "Choose selected color")
        if not color.isValid(): return
        def do():
            for kind, idx in self.board.selected:
                coll = self.board._collection(kind)
                if coll is not None and idx < len(coll) and hasattr(coll[idx], "color"):
                    setattr(coll[idx], "color", color.name())
        self._apply_change("Bulk color", do)

    def toggle_lock_selected(self) -> None:
        def do():
            for kind, idx in self.board.selected:
                coll = self.board._collection(kind)
                if coll is not None and idx < len(coll) and hasattr(coll[idx], "locked"):
                    setattr(coll[idx], "locked", not bool(getattr(coll[idx], "locked")))
        self._apply_change("Toggle lock", do)

    def create_annotation(self, row: int, col: int) -> None:
        dlg = TextDialog("New annotation", "Text:", self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.text.strip():
            self.push_undo("Add annotation")
            self.layout_model.annotations.append(Annotation(dlg.text.strip(), row, col, side=self.board.side))
            self.board.selected = {("annotation", len(self.layout_model.annotations)-1)}
            self._refresh_all()

    def edit_component_pins(self, component: Component, *, is_template: bool) -> None:
        dlg = PinEditorDialog(component, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            if not is_template:
                self.push_undo("Edit pins")
            component.pins = dlg.pins
            component.jumpers = dlg.jumpers
            self._refresh_all()

    def apply_library_preset(self, item: QListWidgetItem) -> None:
        text = item.text()
        t = self.board.new_component_template
        if "DIP-8" in text:
            t.name = "U"; t.component_type = "DIP-8"; t.category = "IC"; t.width = 4; t.height = 4
            t.pins = [ComponentPin(f"P{i+1}", i, -1) for i in range(4)] + [ComponentPin(f"P{i+5}", 3-i, 4) for i in range(4)]
        elif "Pin header" in text:
            t.name = "J"; t.component_type = "pin header"; t.category = "Connector"; t.width = 1; t.height = 4; t.pins = [ComponentPin(f"P{i+1}", i, 0) for i in range(4)]
        elif "Screw" in text:
            t.name = "J"; t.component_type = "screw terminal"; t.category = "Connector"; t.width = 2; t.height = 1; t.pins = [ComponentPin("P1", 0, 0), ComponentPin("P2", 0, 1)]
        else:
            t.name = "R" if "Resistor" in text else "D" if "Diode" in text else "LED" if "LED" in text else "Part"
            t.component_type = text.split(" · ")[0].lower(); t.category = "Passive" if "Resistor" in text else "Semiconductor"
            t.width = 3; t.height = 1; t.pins = [ComponentPin("P1", 0, 0), ComponentPin("P2", 0, 2)]
        self.set_tool("component"); self.populate_inspector()
        self.statusBar().showMessage(f"Loaded template: {text}")

    def start_route_suggestion(self) -> None:
        self.set_tool("wire")
        self.board.start_route_suggestion()
        self.statusBar().showMessage("Suggest route: click the start hole, then the destination hole.")

    def suggest_route_dialog(self) -> None:
        # Backwards-compatible alias for older UI hookups.
        self.start_route_suggestion()


class BomDialog(QDialog):
    def __init__(self, layout_model: Layout, parent=None):
        super().__init__(parent)
        self.layout_model = layout_model
        self.setWindowTitle("Bill of materials")
        self.resize(760, 460)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Grouped by category, type, and value."))
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Qty", "Category", "Type", "Value", "Names"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        buttons = QHBoxLayout()
        export_btn = QPushButton("Export CSV")
        close_btn = QPushButton("Close")
        buttons.addStretch(1); buttons.addWidget(export_btn); buttons.addWidget(close_btn)
        layout.addLayout(buttons)
        export_btn.clicked.connect(self.export_csv)
        close_btn.clicked.connect(self.accept)
        self.populate()

    def populate(self) -> None:
        rows = bom_rows(self.layout_model)
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, value in enumerate([row.quantity, row.category, row.component_type, row.value, row.names]):
                self.table.setItem(r, c, QTableWidgetItem(str(value)))

    def export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export BOM CSV", "bom.csv", "CSV (*.csv);;All files (*.*)")
        if not path:
            return
        Path(path).write_text(bom_csv(self.layout_model, separator=";"), encoding="utf-8")


class TextDialog(QDialog):
    def __init__(self, title: str, label: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(label))
        self.edit = QLineEdit()
        layout.addWidget(self.edit)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def text(self) -> str:
        return self.edit.text()


class PinEditorDialog(QDialog):
    def __init__(self, component: Component, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Pin layout: {component.name or 'component'}")
        self.resize(760, 620)
        self.pins = [ComponentPin(p.name, p.row, p.col) for p in component.pins]
        self.jumpers = [ComponentJumper(j.pin_a, j.pin_b, j.color) for j in component.jumpers]
        self.width = component.width
        self.height = component.height
        self.selected_pin_name: str = self.pins[0].name if self.pins else ""

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Click cells to add/select component attachment pins. Yellow cells are the visible component body; outer cells are external lead positions."))

        body = QHBoxLayout()
        layout.addLayout(body, 1)

        self.grid_frame = QFrame()
        self.grid_frame.setObjectName("PinGridFrame")
        self.grid_layout = QGridLayout(self.grid_frame)
        self.grid_layout.setContentsMargins(10, 10, 10, 10)
        self.grid_layout.setSpacing(6)
        body.addWidget(self.grid_frame, 1)

        side = QVBoxLayout()
        body.addLayout(side)
        side.addWidget(QLabel("Pins"))
        self.pin_list = QListWidget()
        self.pin_list.setMinimumWidth(220)
        self.pin_list.setMaximumHeight(150)
        side.addWidget(self.pin_list)

        pin_button_row = QHBoxLayout()
        self.rename_btn = QPushButton("Rename")
        self.remove_btn = QPushButton("Remove")
        pin_button_row.addWidget(self.rename_btn); pin_button_row.addWidget(self.remove_btn)
        side.addLayout(pin_button_row)
        self.clear_btn = QPushButton("Clear pins")
        side.addWidget(self.clear_btn)

        side.addWidget(QLabel("Presets"))
        preset_grid = QGridLayout()
        self.preset_two_btn = QPushButton("2-pin horizontal")
        self.preset_four_btn = QPushButton("4 corners")
        self.preset_dip_btn = QPushButton("DIP sides")
        self.preset_external_btn = QPushButton("External leads")
        preset_grid.addWidget(self.preset_two_btn, 0, 0)
        preset_grid.addWidget(self.preset_four_btn, 0, 1)
        preset_grid.addWidget(self.preset_dip_btn, 1, 0)
        preset_grid.addWidget(self.preset_external_btn, 1, 1)
        side.addLayout(preset_grid)

        side.addSpacing(8)
        side.addWidget(QLabel("Internal jumpers"))
        self.jumper_list = QListWidget()
        self.jumper_list.setMinimumWidth(220)
        self.jumper_list.setMinimumHeight(110)
        side.addWidget(self.jumper_list)
        jumper_row = QHBoxLayout()
        self.jumper_a = QComboBox(); self.jumper_b = QComboBox()
        jumper_row.addWidget(self.jumper_a); jumper_row.addWidget(self.jumper_b)
        side.addLayout(jumper_row)
        jumper_buttons = QHBoxLayout()
        self.add_jumper_btn = QPushButton("Add")
        self.remove_jumper_btn = QPushButton("Remove")
        jumper_buttons.addWidget(self.add_jumper_btn); jumper_buttons.addWidget(self.remove_jumper_btn)
        side.addLayout(jumper_buttons)
        side.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.pin_list.itemClicked.connect(self._pin_list_clicked)
        self.rename_btn.clicked.connect(self.rename_selected_pin)
        self.remove_btn.clicked.connect(self.remove_selected_pin)
        self.clear_btn.clicked.connect(self.clear_pins)
        self.preset_two_btn.clicked.connect(self.preset_two_pin)
        self.preset_four_btn.clicked.connect(self.preset_four_corners)
        self.preset_dip_btn.clicked.connect(self.preset_dip)
        self.preset_external_btn.clicked.connect(self.preset_external_leads)
        self.add_jumper_btn.clicked.connect(self.add_jumper)
        self.remove_jumper_btn.clicked.connect(self.remove_selected_jumper)
        self.refresh()

    def _range(self) -> tuple[range, range]:
        pad = 2
        return range(-pad, self.height + pad), range(-pad, self.width + pad)

    def pin_at(self, row: int, col: int) -> Optional[ComponentPin]:
        return next((p for p in self.pins if p.row == row and p.col == col), None)

    def refresh(self) -> None:
        self.refresh_grid()
        self.refresh_pin_list()
        self.refresh_jumpers()

    def refresh_grid(self) -> None:
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        rows, cols = self._range()
        self.grid_layout.addWidget(QLabel(""), 0, 0)
        for gc, col in enumerate(cols, start=1):
            lbl = QLabel(str(col))
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setObjectName("PinGridCoord")
            self.grid_layout.addWidget(lbl, 0, gc)
        for gr, row in enumerate(rows, start=1):
            lbl = QLabel(str(row))
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setObjectName("PinGridCoord")
            self.grid_layout.addWidget(lbl, gr, 0)
            for gc, col in enumerate(cols, start=1):
                pin = self.pin_at(row, col)
                btn = QToolButton()
                btn.setMinimumSize(38, 34)
                btn.setToolTip(f"Relative row {row}, col {col}")
                btn.setText(pin.name if pin else "•")
                inside = 0 <= row < self.height and 0 <= col < self.width
                if pin:
                    selected = pin.name == self.selected_pin_name
                    bg = "#111827" if selected else "#f8fafc"
                    fg = "#ffffff" if selected else "#111827"
                    border = "#2457d6" if selected else "#111827"
                    btn.setStyleSheet(f"QToolButton {{background: {bg}; color: {fg}; border: 2px solid {border}; border-radius: 8px; font-weight: 800;}}")
                elif inside:
                    btn.setStyleSheet("QToolButton {background: #ffe59a; color: #475569; border: 1px solid #f5c542; border-radius: 8px;} QToolButton:hover {border-color: #2457d6;}")
                else:
                    btn.setStyleSheet("QToolButton {background: #eef2f7; color: #64748b; border: 1px solid #cbd5e1; border-radius: 8px;} QToolButton:hover {border-color: #2457d6;}")
                btn.clicked.connect(lambda checked=False, r=row, c=col: self.toggle_pin_cell(r, c))
                self.grid_layout.addWidget(btn, gr, gc)

    def refresh_pin_list(self) -> None:
        self.pin_list.blockSignals(True)
        self.pin_list.clear()
        for pin in self.pins:
            item = QListWidgetItem(f"{pin.name}: row {pin.row}, col {pin.col}")
            item.setData(Qt.ItemDataRole.UserRole, pin.name)
            self.pin_list.addItem(item)
            if pin.name == self.selected_pin_name:
                item.setSelected(True)
                self.pin_list.setCurrentItem(item)
        self.pin_list.blockSignals(False)
        names = [p.name for p in self.pins]
        for combo in [self.jumper_a, self.jumper_b]:
            current = combo.currentText()
            combo.blockSignals(True)
            combo.clear(); combo.addItems(names)
            if current in names:
                combo.setCurrentText(current)
            combo.blockSignals(False)

    def refresh_jumpers(self) -> None:
        self.jumper_list.clear()
        for jumper in self.jumpers:
            item = QListWidgetItem(f"{jumper.pin_a} ↔ {jumper.pin_b} · {jumper.color}")
            self.jumper_list.addItem(item)

    def _pin_list_clicked(self, item: QListWidgetItem) -> None:
        name = item.data(Qt.ItemDataRole.UserRole)
        if name:
            self.selected_pin_name = name
            self.refresh_grid()

    def _next_pin_name(self) -> str:
        used = {p.name for p in self.pins}
        n = 1
        while f"P{n}" in used:
            n += 1
        return f"P{n}"

    def toggle_pin_cell(self, row: int, col: int) -> None:
        pin = self.pin_at(row, col)
        if pin:
            self.selected_pin_name = pin.name
        else:
            pin = ComponentPin(self._next_pin_name(), row, col)
            self.pins.append(pin)
            self.selected_pin_name = pin.name
        self.refresh()

    def rename_selected_pin(self) -> None:
        pin = next((p for p in self.pins if p.name == self.selected_pin_name), None)
        if not pin:
            return
        text, ok = QInputDialog.getText(self, "Rename pin", "Pin name:", text=pin.name)
        if ok and text.strip():
            old = pin.name
            pin.name = text.strip()
            for jumper in self.jumpers:
                if jumper.pin_a == old:
                    jumper.pin_a = pin.name
                if jumper.pin_b == old:
                    jumper.pin_b = pin.name
            self.selected_pin_name = pin.name
            self.refresh()

    def remove_selected_pin(self) -> None:
        name = self.selected_pin_name
        self.pins = [p for p in self.pins if p.name != name]
        self.jumpers = [j for j in self.jumpers if j.pin_a != name and j.pin_b != name]
        self.selected_pin_name = self.pins[0].name if self.pins else ""
        self.refresh()

    def clear_pins(self) -> None:
        self.pins = []
        self.jumpers = []
        self.selected_pin_name = ""
        self.refresh()

    def preset_two_pin(self) -> None:
        self.pins = [ComponentPin("P1", 0, 0), ComponentPin("P2", 0, max(1, self.width - 1))]
        self.jumpers = []
        self.selected_pin_name = "P1"
        self.refresh()

    def preset_four_corners(self) -> None:
        self.pins = [ComponentPin("P1", 0, 0), ComponentPin("P2", 0, self.width - 1), ComponentPin("P3", self.height - 1, 0), ComponentPin("P4", self.height - 1, self.width - 1)]
        self.jumpers = []
        self.selected_pin_name = "P1"
        self.refresh()

    def preset_dip(self) -> None:
        n = max(2, self.height)
        self.pins = [ComponentPin(f"P{i+1}", i, -1) for i in range(n)] + [ComponentPin(f"P{i+n+1}", n - 1 - i, self.width) for i in range(n)]
        self.jumpers = []
        self.selected_pin_name = "P1"
        self.refresh()

    def preset_external_leads(self) -> None:
        self.pins = [
            ComponentPin("P1", 0, -1),
            ComponentPin("P2", 0, self.width),
            ComponentPin("P3", self.height - 1, -1),
            ComponentPin("P4", self.height - 1, self.width),
        ]
        self.jumpers = []
        self.selected_pin_name = "P1"
        self.refresh()

    def add_jumper(self) -> None:
        a = self.jumper_a.currentText().strip()
        b = self.jumper_b.currentText().strip()
        if not a or not b or a == b:
            return
        if any({j.pin_a, j.pin_b} == {a, b} for j in self.jumpers):
            return
        self.jumpers.append(ComponentJumper(a, b, "#00aaff"))
        self.refresh_jumpers()

    def remove_selected_jumper(self) -> None:
        row = self.jumper_list.currentRow()
        if 0 <= row < len(self.jumpers):
            del self.jumpers[row]
            self.refresh_jumpers()

    def accept(self) -> None:
        names = [p.name for p in self.pins]
        if len(names) != len(set(names)):
            QMessageBox.warning(self, "Duplicate pins", "Pin names must be unique.")
            return
        super().accept()


def run() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Perfboard Planner")
    app.setStyleSheet(APP_STYLESHEET)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    run()
