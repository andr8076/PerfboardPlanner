from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Iterable, Optional, Tuple

from PySide6.QtCore import Qt, QSize, QPointF
from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon, QKeySequence
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
    QVBoxLayout,
    QWidget,
)

from ...core.bom import bom_csv, bom_rows
from ...core.checks import LayoutWarning, layout_warnings, pin_connection_counts
from ...core.models import Annotation, Component, ComponentJumper, ComponentPin, KeepoutZone, Layout, Via, Wire
from ...core.routing import simple_dogleg_route
from ...core.storage import layout_from_dict, layout_to_dict, load_layout_file, save_layout_file
from .board_view import BoardView, Selection
from .style import APP_STYLESHEET


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Perfboard Planner v30")
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
        self._build_ui()
        self._wire_events()
        self._refresh_all()

    def _build_ui(self) -> None:
        self.board = BoardView()
        self.board.set_layout(self.layout_model)
        self.setCentralWidget(self.board)
        self._build_toolbar()
        self._build_left_dock()
        self._build_right_dock()
        self.statusBar().showMessage("Ready")

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main")
        toolbar.setIconSize(QSize(18, 18))
        toolbar.setMovable(False)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)

        self.new_action = QAction("New", self); self.new_action.setShortcut(QKeySequence.StandardKey.New)
        self.open_action = QAction("Open", self); self.open_action.setShortcut(QKeySequence.StandardKey.Open)
        self.save_action = QAction("Save", self); self.save_action.setShortcut(QKeySequence.StandardKey.Save)
        self.save_as_action = QAction("Save As", self)
        for action in [self.new_action, self.open_action, self.save_action, self.save_as_action]:
            toolbar.addAction(action)
        toolbar.addSeparator()

        self.undo_action = QAction("Undo", self); self.undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        self.redo_action = QAction("Redo", self); self.redo_action.setShortcut(QKeySequence.StandardKey.Redo)
        toolbar.addAction(self.undo_action); toolbar.addAction(self.redo_action)
        toolbar.addSeparator()

        self.mode_group = QActionGroup(self)
        self.mode_actions: dict[str, QAction] = {}
        for text, tool in [("Select", "select"), ("Part", "component"), ("Wire", "wire"), ("Via", "via"), ("Note", "annotation"), ("Keepout", "keepout")]:
            act = QAction(text, self); act.setCheckable(True); act.setData(tool)
            self.mode_group.addAction(act); toolbar.addAction(act); self.mode_actions[tool] = act
        self.mode_actions["select"].setChecked(True)
        toolbar.addSeparator()

        self.front_action = QAction("Front", self); self.front_action.setCheckable(True); self.front_action.setChecked(True)
        self.back_action = QAction("Back", self); self.back_action.setCheckable(True)
        self.side_group = QActionGroup(self); self.side_group.addAction(self.front_action); self.side_group.addAction(self.back_action)
        toolbar.addAction(self.front_action); toolbar.addAction(self.back_action)
        toolbar.addSeparator()

        self.zoom_fit_action = QAction("Fit", self)
        self.warnings_action = QAction("Warnings", self)
        self.bom_action = QAction("BOM", self)
        toolbar.addAction(self.zoom_fit_action); toolbar.addAction(self.warnings_action); toolbar.addAction(self.bom_action)

    def _build_left_dock(self) -> None:
        dock = QDockWidget("Project", self)
        dock.setObjectName("ProjectDock")
        dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        dock.setMinimumWidth(300)
        tabs = QTabWidget()
        dock.setWidget(tabs)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
        self.left_tabs = tabs

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

        # Warnings
        warnings_tab = QWidget(); warnings_layout = QVBoxLayout(warnings_tab)
        self.warning_summary_label = QLabel("Layout OK")
        self.warning_list = QListWidget(); self.warning_list.setAlternatingRowColors(True)
        warnings_layout.addWidget(self.warning_summary_label); warnings_layout.addWidget(self.warning_list)
        tabs.addTab(warnings_tab, "Warnings")

        # Wire colors compact rail
        colors_tab = QWidget(); colors_layout = QVBoxLayout(colors_tab)
        colors_layout.addWidget(QLabel("Click a color to hide/show wires."))
        self.color_swatch_area = QWidget(); self.color_swatch_layout = QGridLayout(self.color_swatch_area); self.color_swatch_layout.setContentsMargins(0,0,0,0); self.color_swatch_layout.setSpacing(6)
        colors_layout.addWidget(self.color_swatch_area)
        self.show_all_colors_btn = QPushButton("Show all wire colors")
        colors_layout.addWidget(self.show_all_colors_btn)
        colors_layout.addStretch(1)
        tabs.addTab(colors_tab, "Wire colors")

        # Library
        library_tab = QWidget(); lib_layout = QVBoxLayout(library_tab)
        lib_layout.addWidget(QLabel("Quick footprints"))
        self.library_list = QListWidget()
        for label in ["Resistor · 2-pin", "LED · 2-pin", "Diode · 2-pin", "DIP-8", "Pin header · 4", "Screw terminal · 2"]:
            self.library_list.addItem(label)
        lib_layout.addWidget(self.library_list)
        tabs.addTab(library_tab, "Library")

        # BOM preview
        bom_tab = QWidget(); bom_layout = QVBoxLayout(bom_tab)
        self.bom_table = QTableWidget(0, 5)
        self.bom_table.setHorizontalHeaderLabels(["Qty", "Category", "Type", "Value", "Names"])
        self.bom_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.export_bom_button = QPushButton("Export BOM CSV")
        bom_layout.addWidget(self.bom_table); bom_layout.addWidget(self.export_bom_button)
        tabs.addTab(bom_tab, "BOM")

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
        self.front_action.triggered.connect(lambda: self.set_side("front"))
        self.back_action.triggered.connect(lambda: self.set_side("back"))
        self.zoom_fit_action.triggered.connect(self.board.fit_to_view)
        self.warnings_action.triggered.connect(lambda: self.left_tabs.setCurrentIndex(1))
        self.bom_action.triggered.connect(lambda: self.left_tabs.setCurrentIndex(4))
        self.board.beforeLayoutChange.connect(self.push_undo)
        self.board.layoutChanged.connect(self._on_layout_changed)
        self.board.selectionChanged.connect(self.populate_inspector)
        self.board.statusMessage.connect(self.statusBar().showMessage)
        self.board.annotationRequested.connect(self.create_annotation)
        self.board.itemActivated.connect(lambda kind, idx: self.populate_inspector())
        self.delete_button.clicked.connect(self.board.delete_selected)
        self.lock_button.clicked.connect(self.toggle_lock_selected)
        self.object_tree.itemClicked.connect(self.select_from_object_tree)
        self.warning_list.itemClicked.connect(self.focus_warning_item)
        self.show_all_colors_btn.clicked.connect(self.show_all_wire_colors)
        self.library_list.itemDoubleClicked.connect(self.apply_library_preset)
        self.export_bom_button.clicked.connect(self.export_bom_csv)

    def set_tool(self, tool: str) -> None:
        self.board.set_tool(tool)
        if tool in self.mode_actions:
            self.mode_actions[tool].setChecked(True)
        self.statusBar().showMessage(f"Mode: {tool}")
        self.populate_inspector()

    def set_side(self, side: str) -> None:
        self.board.set_side(side)
        self.front_action.setChecked(side == "front")
        self.back_action.setChecked(side == "back")
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
        self.board.warning_points = {(w.row, w.col) for w in self._warnings if w.row is not None and w.col is not None}
        self.board.update()
        self.populate_objects()
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

    def populate_warnings(self) -> None:
        self.warning_list.clear()
        count = len(self._warnings)
        self.warning_summary_label.setText("Layout OK" if count == 0 else f"{count} layout warning{'s' if count != 1 else ''}")
        for warning in self._warnings:
            item = QListWidgetItem(f"{warning.code}: {warning.message}")
            item.setData(Qt.ItemDataRole.UserRole, warning)
            self.warning_list.addItem(item)

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
        counts: dict[str, int] = {}
        for wire in self.layout_model.wires:
            counts[wire.color] = counts.get(wire.color, 0) + 1
        for n, (color, count) in enumerate(sorted(counts.items())):
            button = QToolButton()
            button.setText("×" if color in self.board.hidden_wire_colors else str(count))
            button.setToolTip(f"{color}: {count} wire(s). Click to hide/show.")
            button.setCheckable(True); button.setChecked(color not in self.board.hidden_wire_colors)
            fg = "#ffffff" if QColor(color).lightness() < 120 else "#111827"
            bg = "#ffffff" if color in self.board.hidden_wire_colors else color
            border = color if color in self.board.hidden_wire_colors else "#cbd5e1"
            button.setStyleSheet(f"QToolButton {{background: {bg}; color: {fg}; border-radius: 9px; min-width: 34px; min-height: 28px; font-weight: 700; border: 2px solid {border};}}")
            button.clicked.connect(lambda checked=False, c=color: self.toggle_wire_color(c))
            self.color_swatch_layout.addWidget(button, n // 5, n % 5)

    def toggle_wire_color(self, color: str) -> None:
        if color in self.board.hidden_wire_colors:
            self.board.hidden_wire_colors.remove(color)
        else:
            self.board.hidden_wire_colors.add(color)
        self.populate_wire_colors(); self.board.update()

    def show_all_wire_colors(self) -> None:
        self.board.hidden_wire_colors.clear(); self.populate_wire_colors(); self.board.update()

    def populate_bom(self) -> None:
        rows = bom_rows(self.layout_model)
        self.bom_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, value in enumerate([row.quantity, row.category, row.component_type, row.value, row.names]):
                self.bom_table.setItem(r, c, QTableWidgetItem(str(value)))

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

        _, newpart = self._card("New component template")
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

        _, wire = self._card("New wire")
        wire.addRow("Color", self._color_button(self.board.current_wire_color, lambda v: setattr(self.board, "current_wire_color", v)))
        route_btn = QPushButton("Suggest dogleg route between two selected vias/pins")
        route_btn.clicked.connect(self.suggest_route_dialog)
        wire.addRow(route_btn)

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
        form.addRow("Color", self._color_button(wire.color, lambda v: self._apply_change("Wire color", lambda: setattr(wire, "color", v))))
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

    def suggest_route_dialog(self) -> None:
        selected_points = []
        for kind, idx in self.board.selected:
            if kind == "via" and idx < len(self.layout_model.vias):
                v = self.layout_model.vias[idx]; selected_points.append((v.row, v.col))
            elif kind == "wire" and idx < len(self.layout_model.wires):
                selected_points.extend(self.layout_model.wires[idx].points[:1])
        if len(selected_points) < 2:
            QMessageBox.information(self, "Suggest route", "Select two vias or a wire endpoint first. The helper creates a simple dogleg route.")
            return
        self.push_undo("Suggest route")
        points = simple_dogleg_route(selected_points[0], selected_points[1])
        self.layout_model.wires.append(Wire("", points, self.board.current_wire_color, side=self.board.side))
        self.board.selected = {("wire", len(self.layout_model.wires)-1)}
        self._refresh_all()


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
        self.setWindowTitle("Edit pins and internal jumpers")
        self.resize(620, 520)
        self.pins = [ComponentPin(p.name, p.row, p.col) for p in component.pins]
        self.jumpers = [ComponentJumper(j.pin_a, j.pin_b, j.color) for j in component.jumpers]
        self.width = component.width
        self.height = component.height
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Pins are relative to the component's top-left hole. External pins are allowed."))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Name", "Relative row", "Relative col"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)
        row = QHBoxLayout()
        add_btn = QPushButton("Add pin"); remove_btn = QPushButton("Remove selected pin")
        preset_2 = QPushButton("2-pin horizontal"); preset_dip = QPushButton("DIP sides")
        row.addWidget(add_btn); row.addWidget(remove_btn); row.addWidget(preset_2); row.addWidget(preset_dip)
        layout.addLayout(row)
        layout.addWidget(QLabel("Internal jumpers: one per line as PinA;PinB;#color"))
        self.jumpers_edit = QTextEdit(); self.jumpers_edit.setMinimumHeight(90)
        layout.addWidget(self.jumpers_edit)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        add_btn.clicked.connect(self.add_pin); remove_btn.clicked.connect(self.remove_pin)
        preset_2.clicked.connect(self.preset_two_pin); preset_dip.clicked.connect(self.preset_dip)
        self.populate()

    def populate(self) -> None:
        self.table.setRowCount(len(self.pins))
        for r, pin in enumerate(self.pins):
            self.table.setItem(r, 0, QTableWidgetItem(pin.name))
            self.table.setItem(r, 1, QTableWidgetItem(str(pin.row)))
            self.table.setItem(r, 2, QTableWidgetItem(str(pin.col)))
        self.jumpers_edit.setPlainText("\n".join(f"{j.pin_a};{j.pin_b};{j.color}" for j in self.jumpers))

    def add_pin(self) -> None:
        self.pins.append(ComponentPin(f"P{len(self.pins)+1}", 0, 0)); self.populate()

    def remove_pin(self) -> None:
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()}, reverse=True)
        for row in rows:
            if 0 <= row < len(self.pins): del self.pins[row]
        self.populate()

    def preset_two_pin(self) -> None:
        self.pins = [ComponentPin("P1", 0, 0), ComponentPin("P2", 0, max(1, self.width-1))]
        self.jumpers = []
        self.populate()

    def preset_dip(self) -> None:
        n = max(2, self.height)
        self.pins = [ComponentPin(f"P{i+1}", i, -1) for i in range(n)] + [ComponentPin(f"P{i+n+1}", n-1-i, self.width) for i in range(n)]
        self.jumpers = []
        self.populate()

    def accept(self) -> None:
        pins: list[ComponentPin] = []
        for r in range(self.table.rowCount()):
            try:
                name = self.table.item(r, 0).text().strip() or f"P{r+1}"
                row = int(self.table.item(r, 1).text())
                col = int(self.table.item(r, 2).text())
                pins.append(ComponentPin(name, row, col))
            except Exception:
                QMessageBox.warning(self, "Invalid pin", f"Pin row {r+1} has invalid data.")
                return
        names = {p.name for p in pins}
        jumpers: list[ComponentJumper] = []
        for line in self.jumpers_edit.toPlainText().splitlines():
            if not line.strip():
                continue
            parts = [p.strip() for p in line.split(";")]
            if len(parts) >= 2 and parts[0] in names and parts[1] in names:
                jumpers.append(ComponentJumper(parts[0], parts[1], parts[2] if len(parts) >= 3 and parts[2] else "#00aaff"))
        self.pins = pins
        self.jumpers = jumpers
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
