import pytest

from perfboard_planner.core.commands import Command, CommandStack
from perfboard_planner.core.geometry import clamp_component_position, component_pin_absolute, rotate_component_footprint_90
from perfboard_planner.core.models import Component, ComponentPin, KeepoutZone, Layout, Wire, Via
from perfboard_planner.core.storage import layout_from_dict, layout_to_dict
from perfboard_planner.core.selection import is_selectable_item
from perfboard_planner.core.connectivity import build_graph, connected_nodes

from perfboard_planner.ui.qt.routing import BoardRoutingMixin


class _NoopSignal:
    def emit(self, *args, **kwargs):
        return None


class _HeadlessRoutingBoard(BoardRoutingMixin):
    def __init__(self, layout: Layout, *, side: str = "front"):
        self.layout_model = layout
        self.side = side
        self.hidden_wire_colors = set()
        self.hidden_groups = set()
        self.avoid_wire_overlaps = False
        self.allow_route_component_side_changes = False
        self.route_movable_component_indexes = None
        self.selected = set()
        self.beforeLayoutChange = _NoopSignal()
        self.layoutChanged = _NoopSignal()
        self.selectionChanged = _NoopSignal()

    def update(self):
        return None

    def _is_item_hidden_by_group(self, item) -> bool:
        group = getattr(item, "group", "")
        return bool(group and group in self.hidden_groups)

    def _component_body_cells_at(self, comp: Component, row: int, col: int) -> set[tuple[int, int]]:
        return {
            (r, c)
            for r in range(row, row + comp.height)
            for c in range(col, col + comp.width)
            if 0 <= r < self.layout_model.rows and 0 <= c < self.layout_model.cols
        }

    def _component_pin_points_at(self, comp: Component, row: int, col: int) -> dict[str, tuple[int, int]]:
        return {pin.name: (row + pin.row, col + pin.col) for pin in comp.pins}

    def _component_can_move_to(self, comp_idx: int, row: int, col: int) -> bool:
        comp = self.layout_model.components[comp_idx]
        if row < 0 or col < 0 or row + comp.height > self.layout_model.rows or col + comp.width > self.layout_model.cols:
            return False
        candidate = self._component_body_cells_at(comp, row, col)
        for other_idx, other in enumerate(self.layout_model.components):
            if other_idx == comp_idx or other.side != comp.side or self._is_item_hidden_by_group(other):
                continue
            if candidate & self._component_body_cells_at(other, other.row, other.col):
                return False
        return True

    def _wire_endpoint_points(self, wire: Wire) -> tuple[tuple[int, int], tuple[int, int]]:
        return wire.points[0], wire.points[-1]

    def _component_pin_substitutions(
        self,
        comp: Component,
        old_row: int,
        old_col: int,
        new_row: int,
        new_col: int,
    ) -> dict[tuple[int, int], tuple[int, int]]:
        old_pins = self._component_pin_points_at(comp, old_row, old_col)
        new_pins = self._component_pin_points_at(comp, new_row, new_col)
        return {old_pins[name]: new_pins[name] for name in old_pins.keys() & new_pins.keys()}

    def _move_wire_endpoints_for_pin_substitutions(
        self,
        side: str,
        substitutions: dict[tuple[int, int], tuple[int, int]],
        *,
        skip_wire_indexes: set[int] | None = None,
    ) -> None:
        if not substitutions:
            return
        skip_wire_indexes = skip_wire_indexes or set()
        for wire_idx, wire in enumerate(self.layout_model.wires):
            if wire_idx in skip_wire_indexes or wire.locked or wire.side != side or len(wire.points) < 2:
                continue
            updated = list(wire.points)
            updated[0] = substitutions.get(updated[0], updated[0])
            updated[-1] = substitutions.get(updated[-1], updated[-1])
            wire.points = updated

    def _component_has_locked_wire_endpoint(self, comp_idx: int) -> bool:
        comp = self.layout_model.components[comp_idx]
        pin_points = set(self._component_pin_points_at(comp, comp.row, comp.col).values())
        for wire in self.layout_model.wires:
            if wire.locked and wire.side == comp.side and len(wire.points) >= 2 and set(self._wire_endpoint_points(wire)) & pin_points:
                return True
        return False


def test_storage_roundtrip():
    layout = layout_from_dict({
        "version": 6,
        "board": {"rows": 10, "cols": 10, "spacing": 22},
        "components": [{
            "name": "U1", "row": 1, "col": 1, "width": 2, "height": 2, "color": "#ffcc66",
            "pins": [{"name": "A", "row": 0, "col": 0}, {"name": "B", "row": 0, "col": 1}],
            "jumpers": [{"pin_a": "A", "pin_b": "B", "color": "#00aaff"}],
        }],
        "wires": [{"points": [[1, 1], [1, 4]], "color": "#ff0000", "side": "front", "lane": 3, "layer": "aux"}],
    })
    out = layout_to_dict(layout)
    assert out["schema_version"] >= 11
    assert out["board"]["rows"] == 10
    assert "lane" not in out["wires"][0]
    assert "layer" not in out["wires"][0]


def test_connectivity_uses_explicit_wire_points_only():
    wires = [
        Wire("", [(0, 0), (0, 4)], "#f00", "front"),
        Wire("", [(1, 2), (-1, 2)], "#00f", "front"),
    ]
    graph = build_graph([], wires, [])
    # These wires visually cross at (0,2), but neither has an explicit point there.
    assert ("front", "hole", 0, 2) not in graph


def test_via_links_front_and_back():
    graph = build_graph([], [], [Via(2, 3)])
    net = connected_nodes(graph, ("front", "hole", 2, 3))
    assert ("back", "hole", 2, 3) in net


def test_storage_ignores_malformed_nested_collections():
    layout = layout_from_dict({
        "board": {"rows": 5, "cols": 5},
        "components": [
            {
                "name": "U1",
                "pins": [None, {"name": "A", "row": 1, "col": 2}],
                "jumpers": [None, {"pin_a": "A", "pin_b": "missing"}],
            },
        ],
        "wires": [{"points": None}],
        "vias": None,
    })

    assert len(layout.components) == 1
    assert [pin.name for pin in layout.components[0].pins] == ["A"]
    assert layout.components[0].jumpers == []
    assert layout.wires[0].points == []
    assert layout.vias == []


def test_command_stack_clears_redo_history_on_new_execute():
    events = []
    stack = CommandStack(limit=1)
    stack.execute(Command("first", lambda: events.append("do1"), lambda: events.append("undo1")))
    stack.execute(Command("second", lambda: events.append("do2"), lambda: events.append("undo2")))

    assert [command.label for command in stack.undo_stack] == ["second"]
    assert stack.undo()
    stack.execute(Command("third", lambda: events.append("do3"), lambda: events.append("undo3")))

    assert not stack.redo()
    assert [command.label for command in stack.undo_stack] == ["third"]
    assert events == ["do1", "do2", "undo2", "do3"]


def test_rotate_component_footprint_moves_pins_with_body():
    component = Component(
        "U1",
        4,
        5,
        4,
        2,
        "#ffcc66",
        pins=[ComponentPin("A", 0, 0), ComponentPin("B", 1, 3), ComponentPin("EXT", 0, -1)],
    )

    rotate_component_footprint_90(component)

    assert (component.width, component.height) == (2, 4)
    assert [(pin.name, pin.row, pin.col) for pin in component.pins] == [
        ("A", 0, 1),
        ("B", 3, 0),
        ("EXT", -1, 1),
    ]


def test_clamp_component_position_keeps_full_footprint_on_board():
    assert clamp_component_position(4, 4, 2, 3, 5, 5) == (2, 3)
    assert clamp_component_position(-3, -2, 2, 2, 5, 5) == (0, 0)
    assert clamp_component_position(10, 10, 8, 8, 5, 5) == (0, 0)


def test_selectable_item_matches_current_view_visibility():
    front_part = Component("U1", 0, 0, 1, 1, "#fff", side="front")
    back_part = Component("U2", 0, 0, 1, 1, "#fff", side="back")
    hidden_group_part = Component("U3", 0, 0, 1, 1, "#fff", side="front", group="hidden")
    front_wire = Wire("", [(0, 0), (0, 1)], "#d00000", side="front")
    hidden_color_wire = Wire("", [(1, 0), (1, 1)], "#00aa00", side="front")
    back_wire = Wire("", [(2, 0), (2, 1)], "#d00000", side="back")
    current_keepout = KeepoutZone("K1", 0, 0, 1, 1, side="front")
    back_keepout = KeepoutZone("K2", 0, 0, 1, 1, side="back")

    common = {
        "current_side": "front",
        "hidden_groups": {"hidden"},
        "hidden_wire_colors": {"#00aa00"},
        "show_other_parts": True,
        "show_other_wires": True,
        "show_current_keepouts": True,
        "show_other_keepouts": True,
    }

    assert is_selectable_item("component", front_part, **common)
    assert not is_selectable_item("component", back_part, **common)
    assert not is_selectable_item("component", hidden_group_part, **common)
    assert is_selectable_item("wire", front_wire, **common)
    assert not is_selectable_item("wire", hidden_color_wire, **common)
    assert not is_selectable_item("wire", back_wire, **common)
    assert is_selectable_item("keepout", current_keepout, **common)
    assert not is_selectable_item("keepout", back_keepout, **common)
    assert is_selectable_item("via", Via(2, 2, group="visible"), **common)
    assert not is_selectable_item("keepout", current_keepout, **{**common, "show_current_keepouts": False})
    assert is_selectable_item("component", back_part, **common, include_ghosts=True)


def test_qt_duplicate_and_paste_selected_items(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    QApplication = qt_widgets.QApplication

    from perfboard_planner.core.models import Component
    from perfboard_planner.ui.qt.app import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        window.board.hidden_wire_colors.add("#d00000")
        window.board.set_layout(Layout())
        assert window.board.hidden_wire_colors == set()

        window.layout_model.components.append(Component("R1", 0, 0, 2, 1, "#ffcc66", component_type="resistor"))
        window.board.selected = {("component", 0)}

        window.duplicate_selected()
        assert [component.name for component in window.layout_model.components] == ["R1", "R2"]
        assert (window.layout_model.components[1].row, window.layout_model.components[1].col) == (1, 1)
        assert window.board.selected == {("component", 1)}

        window.copy_selected()
        window.paste_clipboard()
        window.paste_clipboard()
        assert [component.name for component in window.layout_model.components] == ["R1", "R2", "R3", "R4"]
        assert (window.layout_model.components[2].row, window.layout_model.components[2].col) == (2, 2)
        assert (window.layout_model.components[3].row, window.layout_model.components[3].col) == (3, 3)
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()

def test_qt_unsaved_prompt_saves_before_continuing(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    QApplication = qt_widgets.QApplication
    QMessageBox = qt_widgets.QMessageBox

    from perfboard_planner.ui.qt.app import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        window._set_dirty(True)
        monkeypatch.setattr(
            QMessageBox,
            "question",
            lambda *args, **kwargs: QMessageBox.StandardButton.Save,
        )
        saved = []
        monkeypatch.setattr(window, "save_file", lambda: saved.append(True) or True)

        assert window.confirm_discard_unsaved()
        assert saved == [True]
    finally:
        window._set_dirty(False)
        window.close()
        window.deleteLater()
        app.processEvents()


def test_qt_suggest_route_can_use_other_side_when_enabled(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    QApplication = qt_widgets.QApplication

    from perfboard_planner.ui.qt.app import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        layout = Layout(rows=5, cols=5)
        layout.components.append(Component("Wall", 0, 2, 1, 5, "#ffcc66", side="front"))
        window.board.set_layout(layout)
        window.board.side = "front"

        assert window.board._suggest_route((2, 0), (2, 4)) == []

        window.board.allow_route_suggestion_cross_side = True
        window.board._handle_route_suggestion_click((2, 0))
        window.board._handle_route_suggestion_click((2, 4))

        assert len(window.layout_model.vias) == 2
        assert [wire.side for wire in window.layout_model.wires] == ["front", "back", "front"]
        assert window.board.selected == {("wire", 0), ("wire", 1), ("wire", 2)}
    finally:
        window._set_dirty(False)
        window.close()
        window.deleteLater()
        app.processEvents()


def test_qt_suggest_all_prioritizes_constrained_wires(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    QApplication = qt_widgets.QApplication

    from perfboard_planner.ui.qt.app import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        layout = Layout(rows=5, cols=5)
        layout.components.extend([
            Component("B1", 1, 0, 1, 1, "#ffcc66", side="front"),
            Component("B2", 2, 1, 1, 1, "#ffcc66", side="front"),
            Component("B3", 3, 0, 1, 1, "#ffcc66", side="front"),
        ])
        constrained = Wire("constrained", [(2, 0), (2, 4)], "#d00000", "front")
        open_wire = Wire("open", [(0, 0), (4, 4)], "#00aa00", "front")
        layout.wires.extend([open_wire, constrained])
        window.board.set_layout(layout)

        priorities = [window.board._wire_route_priority(wire) for wire in layout.wires]
        assert priorities[1] < priorities[0]
    finally:
        window._set_dirty(False)
        window.close()
        window.deleteLater()
        app.processEvents()


def test_qt_solderability_cost_prefers_straight_and_via_free_routes(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    QApplication = qt_widgets.QApplication

    from perfboard_planner.ui.qt.app import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        wire = Wire("signal", [(0, 0), (0, 4)], "#d00000", "front")
        straight = [(0, 0), (0, 4)]
        bent = [(0, 0), (1, 0), (1, 4), (0, 4)]

        assert window.board._route_cost(straight, wire) < window.board._route_cost(bent, wire)
        assert window.board._route_cost(straight, wire) < window.board._route_cost(straight, wire, via_count=2)
    finally:
        window._set_dirty(False)
        window.close()
        window.deleteLater()
        app.processEvents()


def test_qt_route_priority_orders_rails_constrained_local_then_flexible(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    QApplication = qt_widgets.QApplication

    from perfboard_planner.ui.qt.app import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        layout = Layout(rows=12, cols=20)
        layout.components.extend([
            Component("C1", 4, 0, 1, 1, "#ffcc66", side="front"),
            Component("C2", 5, 1, 1, 1, "#ffcc66", side="front"),
            Component("C3", 6, 0, 1, 1, "#ffcc66", side="front"),
        ])
        window.board.set_layout(layout)

        rail = Wire("GND rail", [(0, 0), (0, 18)], "#111111", "front")
        constrained = Wire("signal", [(5, 0), (5, 8)], "#d00000", "front")
        local = Wire("local", [(8, 2), (8, 4)], "#d00000", "front")
        flexible = Wire("flex", [(2, 2), (10, 15)], "#d00000", "front")

        priorities = [window.board._wire_route_priority(wire)[0] for wire in [rail, constrained, local, flexible]]
        assert priorities == [0, 1, 2, 3]
    finally:
        window._set_dirty(False)
        window.close()
        window.deleteLater()
        app.processEvents()


def test_qt_suggest_all_can_reorder_same_net_chain(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    QApplication = qt_widgets.QApplication

    from perfboard_planner.ui.qt.app import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        layout = Layout(rows=5, cols=5)
        layout.components.append(Component("Wall", 0, 2, 1, 4, "#ffcc66", side="front"))
        a = (0, 0)
        b = (0, 4)
        c = (4, 4)
        layout.wires.extend([
            Wire("net", [a, b], "#d00000", "front"),
            Wire("net", [b, c], "#d00000", "front"),
        ])
        window.board.set_layout(layout)

        routed, failed, vias, moved = window.board.optimize_wire_routes(scope="current_side", allow_cross_side=False)

        assert (routed, failed, vias, moved) == (2, 0, 0, 0)
        endpoint_pairs = {frozenset((wire.points[0], wire.points[-1])) for wire in window.layout_model.wires}
        assert endpoint_pairs == {frozenset((a, c)), frozenset((b, c))}
        assert frozenset((a, b)) not in endpoint_pairs
    finally:
        window._set_dirty(False)
        window.close()
        window.deleteLater()
        app.processEvents()


def test_qt_suggest_route_avoids_existing_wire_cells(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    QApplication = qt_widgets.QApplication

    from perfboard_planner.ui.qt.app import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        layout = Layout(rows=5, cols=5)
        existing = Wire("existing", [(2, 0), (2, 3)], "#00aa00", "front")
        layout.wires.append(existing)
        window.board.set_layout(layout)
        window.board.side = "front"

        route = window.board._suggest_route((0, 0), (4, 4))
        occupied = window.board._route_cells(existing.points)

        assert route
        assert window.board._route_cells(route).isdisjoint(occupied)
    finally:
        window._set_dirty(False)
        window.close()
        window.deleteLater()
        app.processEvents()


def test_qt_route_cost_penalizes_overlapping_spans(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    QApplication = qt_widgets.QApplication

    from perfboard_planner.ui.qt.app import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        layout = Layout(rows=5, cols=8)
        existing = Wire("existing", [(1, 1), (1, 5)], "#00aa00", "front")
        layout.wires.append(existing)
        window.board.set_layout(layout)
        crowded = [(1, 1), (1, 5)]
        clear = [(3, 1), (3, 5)]

        assert window.board._route_cost(crowded, Wire("", crowded, "#d00000", "front")) > window.board._route_cost(clear, Wire("", clear, "#d00000", "front"))
    finally:
        window._set_dirty(False)
        window.close()
        window.deleteLater()
        app.processEvents()


def test_qt_suggest_all_moves_large_components_only_locally(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    QApplication = qt_widgets.QApplication

    from perfboard_planner.ui.qt.app import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        pins = [ComponentPin(f"P{i}", i, 0) for i in range(8)]
        connector = Component("J1", 2, 12, 1, 8, "#ffcc66", side="front", pins=pins)
        layout = Layout(rows=12, cols=16, components=[connector])
        for pin in pins:
            layout.wires.append(Wire(pin.name, [(connector.row + pin.row, 0), (connector.row + pin.row, connector.col)], "#d00000", "front"))
        window.board.set_layout(layout)

        _, _, _, moved = window.board.optimize_wire_routes(scope="whole_board", allow_cross_side=False, allow_move_components=True)

        assert moved == 1
        assert abs(connector.row - 2) + abs(connector.col - 12) <= 1
        assert connector.col == 11
    finally:
        window._set_dirty(False)
        window.close()
        window.deleteLater()
        app.processEvents()


def test_qt_no_overlap_router_avoids_same_direction_wire_spans(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    QApplication = qt_widgets.QApplication

    from perfboard_planner.ui.qt.app import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        layout = Layout(rows=5, cols=5)
        existing = Wire("existing", [(1, 0), (1, 4)], "#00aa00", "front")
        layout.wires.append(existing)
        window.board.set_layout(layout)
        window.board.avoid_wire_overlaps = True

        route = window.board._suggest_route((1, 0), (1, 4))

        assert route
        assert window.board._route_edges(route).isdisjoint(window.board._route_edges(existing.points))
        assert route != [(1, 0), (1, 4)]
    finally:
        window._set_dirty(False)
        window.close()
        window.deleteLater()
        app.processEvents()


def test_qt_no_overlap_router_still_allows_crossings(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    QApplication = qt_widgets.QApplication

    from perfboard_planner.ui.qt.app import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        layout = Layout(rows=5, cols=5)
        layout.wires.append(Wire("existing", [(0, 2), (4, 2)], "#00aa00", "front"))
        window.board.set_layout(layout)
        window.board.avoid_wire_overlaps = True

        route = window.board._suggest_route((2, 0), (2, 4))

        assert route == [(2, 0), (2, 4)]
        assert (2, 2) in window.board._route_cells(route)
    finally:
        window._set_dirty(False)
        window.close()
        window.deleteLater()
        app.processEvents()

def test_qt_router_prefers_clear_detour_over_crowded_equal_path(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    QApplication = qt_widgets.QApplication

    from perfboard_planner.ui.qt.app import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        layout = Layout(rows=5, cols=5)
        layout.keepouts.append(KeepoutZone("block", 2, 2, 2, 2, side="front"))
        layout.wires.append(Wire("crowd", [(0, 0), (0, 4)], "#00aa00", "front"))
        window.board.set_layout(layout)

        route = window.board._suggest_route((2, 0), (2, 4))

        assert route == [(2, 0), (3, 0), (3, 4), (2, 4)]
    finally:
        window._set_dirty(False)
        window.close()
        window.deleteLater()
        app.processEvents()


def test_qt_router_prioritizes_reusing_existing_vias(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    QApplication = qt_widgets.QApplication

    from perfboard_planner.ui.qt.app import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        layout = Layout(rows=10, cols=10)
        layout.vias.append(Via(8, 8))
        window.board.set_layout(layout)

        candidates = window.board._candidate_via_points((0, 0), (0, 9), "front", "back", limit=3)

        assert (8, 8) in candidates
    finally:
        window._set_dirty(False)
        window.close()
        window.deleteLater()
        app.processEvents()

def test_qt_rotate_component_moves_attached_wire_endpoints(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    QApplication = qt_widgets.QApplication

    from perfboard_planner.ui.qt.board_view import BoardView

    app = QApplication.instance() or QApplication([])
    board = BoardView()
    try:
        component = Component(
            "U1",
            2,
            2,
            3,
            2,
            "#ffcc66",
            side="front",
            pins=[ComponentPin("A", 0, 0), ComponentPin("B", 1, 2)],
        )
        layout = Layout(rows=8, cols=8, components=[component])
        layout.wires.append(Wire("signal", [(2, 2), (6, 6)], "#d00000", "front"))
        layout.wires.append(Wire("other", [(0, 0), (6, 6)], "#00aa00", "front"))
        board.set_layout(layout)
        board.selected = {("component", 0)}

        board.rotate_selected()

        assert (component.width, component.height) == (2, 3)
        assert layout.wires[0].points == [(2, 3), (6, 6)]
        assert layout.wires[1].points == [(0, 0), (6, 6)]
    finally:
        board.close()
        board.deleteLater()
        app.processEvents()


def test_qt_drag_component_moves_attached_wire_endpoints(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    QApplication = qt_widgets.QApplication

    from perfboard_planner.ui.qt.board_view import BoardView

    app = QApplication.instance() or QApplication([])
    board = BoardView()
    try:
        component = Component(
            "U1",
            1,
            1,
            2,
            2,
            "#ffcc66",
            side="front",
            pins=[ComponentPin("A", 0, 0), ComponentPin("B", 1, 1)],
        )
        layout = Layout(rows=8, cols=8, components=[component])
        layout.wires.append(Wire("signal", [(1, 1), (5, 5)], "#d00000", "front"))
        board.set_layout(layout)
        board.selected = {("component", 0)}
        board._cache_drag_originals()

        board._apply_drag_delta(2, 3)

        assert (component.row, component.col) == (3, 4)
        assert layout.wires[0].points == [(3, 4), (5, 5)]
    finally:
        board.close()
        board.deleteLater()
        app.processEvents()



def test_qt_pin_editor_click_moves_selected_pin(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    QApplication = qt_widgets.QApplication

    from perfboard_planner.ui.qt.app import PinEditorDialog

    app = QApplication.instance() or QApplication([])
    component = Component(
        "U1",
        0,
        0,
        3,
        2,
        "#ffcc66",
        pins=[ComponentPin("A", 0, 0), ComponentPin("B", 1, 2)],
    )
    dialog = PinEditorDialog(component)
    try:
        dialog.selected_pin_name = "A"

        dialog.toggle_pin_cell(0, 1)

        assert [(pin.name, pin.row, pin.col) for pin in dialog.pins] == [("A", 0, 1), ("B", 1, 2)]
        assert dialog.selected_pin_name == "A"
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def test_qt_pin_editor_can_still_add_pins(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    QApplication = qt_widgets.QApplication

    from perfboard_planner.ui.qt.app import PinEditorDialog

    app = QApplication.instance() or QApplication([])
    component = Component("U1", 0, 0, 3, 2, "#ffcc66", pins=[])
    dialog = PinEditorDialog(component)
    try:
        added = dialog.add_pin_at(0, 1)

        assert added is not None
        assert [(pin.name, pin.row, pin.col) for pin in dialog.pins] == [("P1", 0, 1)]
        assert dialog.selected_pin_name == "P1"
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def test_route_optimizer_move_filter_keeps_unselected_components_fixed():
    layout = Layout(rows=10, cols=14)
    left_a = Component("JA", 1, 1, 1, 1, "#ffcc66", side="front", pins=[ComponentPin("P", 0, 0)], locked=True)
    left_b = Component("JB", 6, 1, 1, 1, "#ffcc66", side="front", pins=[ComponentPin("P", 0, 0)], locked=True)
    unselected = Component("Header", 1, 12, 1, 1, "#ffcc66", side="front", pins=[ComponentPin("P", 0, 0)])
    selected = Component("Gyro", 6, 12, 1, 1, "#ffcc66", side="front", pins=[ComponentPin("P", 0, 0)])
    layout.components.extend([left_a, left_b, unselected, selected])
    layout.wires.append(Wire("header-signal", [(1, 1), (1, 12)], "#d00000", "front"))
    layout.wires.append(Wire("gyro-signal", [(6, 1), (6, 12)], "#00aa00", "front"))
    board = _HeadlessRoutingBoard(layout)
    board.route_movable_component_indexes = {3}

    routed, failed, vias, moved = board.optimize_wire_routes(
        scope="whole_board",
        allow_cross_side=False,
        allow_move_components=True,
    )

    assert (routed, failed, vias, moved) == (2, 0, 0, 1)
    assert (unselected.row, unselected.col) == (1, 12)
    assert (selected.row, selected.col) != (6, 12)
    assert layout.wires[-1].points[-1] == component_pin_absolute(selected, selected.pins[0])


def test_route_optimizer_empty_move_filter_disables_component_moves():
    layout = Layout(rows=8, cols=12)
    fixed = Component("A", 1, 1, 1, 1, "#ffcc66", side="front", pins=[ComponentPin("P", 0, 0)], locked=True)
    movable = Component("B", 1, 10, 1, 1, "#ffcc66", side="front", pins=[ComponentPin("P", 0, 0)])
    layout.components.extend([fixed, movable])
    layout.wires.append(Wire("signal", [(1, 1), (1, 10)], "#d00000", "front"))
    board = _HeadlessRoutingBoard(layout)
    board.route_movable_component_indexes = set()

    routed, failed, vias, moved = board.optimize_wire_routes(
        scope="whole_board",
        allow_cross_side=False,
        allow_move_components=True,
    )

    assert (routed, failed, vias, moved) == (1, 0, 0, 0)
    assert (movable.row, movable.col) == (1, 10)
    assert layout.wires[0].points[-1] == (1, 10)


def test_route_optimizer_unfiltered_moves_all_beneficial_unlocked_components():
    layout = Layout(rows=10, cols=14)
    left_a = Component("JA", 1, 1, 1, 1, "#ffcc66", side="front", pins=[ComponentPin("P", 0, 0)], locked=True)
    left_b = Component("JB", 6, 1, 1, 1, "#ffcc66", side="front", pins=[ComponentPin("P", 0, 0)], locked=True)
    header = Component("Header", 1, 12, 1, 1, "#ffcc66", side="front", pins=[ComponentPin("P", 0, 0)])
    gyro = Component("Gyro", 6, 12, 1, 1, "#ffcc66", side="front", pins=[ComponentPin("P", 0, 0)])
    layout.components.extend([left_a, left_b, header, gyro])
    layout.wires.append(Wire("header-signal", [(1, 1), (1, 12)], "#d00000", "front"))
    layout.wires.append(Wire("gyro-signal", [(6, 1), (6, 12)], "#00aa00", "front"))
    board = _HeadlessRoutingBoard(layout)

    routed, failed, vias, moved = board.optimize_wire_routes(
        scope="whole_board",
        allow_cross_side=False,
        allow_move_components=True,
    )

    assert (routed, failed, vias, moved) == (2, 0, 0, 2)
    assert (header.row, header.col) != (1, 12)
    assert (gyro.row, gyro.col) != (6, 12)


def test_qt_route_optimize_worker_runs_without_gui_thread_widgets(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    QApplication = qt_widgets.QApplication

    from perfboard_planner.ui.qt.app import RouteOptimizeWorker

    app = QApplication.instance() or QApplication([])
    layout = Layout(rows=5, cols=5)
    layout.wires.append(Wire("signal", [(0, 0), (4, 4)], "#d00000", "front"))
    worker = RouteOptimizeWorker(
        layout_to_dict(layout),
        {
            "scope": "whole_board",
            "allow_cross_side": False,
            "allow_move_components": False,
            "side": "front",
            "hidden_wire_colors": set(),
            "hidden_groups": set(),
            "avoid_wire_overlaps": False,
        },
    )
    results = []
    errors = []
    worker.finished.connect(results.append)
    worker.failed.connect(errors.append)

    worker.run()

    assert not errors
    assert results
    assert results[0]["routed"] == 1
    assert results[0]["failed"] == 0
    assert layout_from_dict(results[0]["layout"]).wires[0].points[0] == (0, 0)
    app.processEvents()


def test_qt_suggest_all_can_move_unlocked_components_for_shorter_routes(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    QApplication = qt_widgets.QApplication

    from perfboard_planner.ui.qt.app import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        layout = Layout(rows=8, cols=12)
        fixed = Component("A", 1, 1, 1, 1, "#ffcc66", side="front", pins=[ComponentPin("P", 0, 0)], locked=True)
        movable = Component("B", 1, 10, 1, 1, "#ffcc66", side="front", pins=[ComponentPin("P", 0, 0)])
        layout.components.extend([fixed, movable])
        layout.wires.append(Wire("signal", [(1, 1), (1, 10)], "#d00000", "front"))
        window.board.set_layout(layout)

        routed, failed, vias, moved = window.board.optimize_wire_routes(scope="whole_board", allow_cross_side=False, allow_move_components=True)

        assert (routed, failed, vias, moved) == (1, 0, 0, 1)
        assert (layout.components[0].row, layout.components[0].col) == (1, 1)
        assert (layout.components[1].row, layout.components[1].col) != (1, 10)
        assert layout.wires[0].points[-1] == component_pin_absolute(layout.components[1], layout.components[1].pins[0])
        assert window.board._route_cost(layout.wires[0].points, layout.wires[0]) < window.board._route_cost([(1, 1), (1, 10)], layout.wires[0])
    finally:
        window._set_dirty(False)
        window.close()
        window.deleteLater()
        app.processEvents()


def test_qt_suggest_all_does_not_move_locked_components(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    QApplication = qt_widgets.QApplication

    from perfboard_planner.ui.qt.app import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        layout = Layout(rows=8, cols=12)
        a = Component("A", 1, 1, 1, 1, "#ffcc66", side="front", pins=[ComponentPin("P", 0, 0)], locked=True)
        b = Component("B", 1, 10, 1, 1, "#ffcc66", side="front", pins=[ComponentPin("P", 0, 0)], locked=True)
        layout.components.extend([a, b])
        layout.wires.append(Wire("signal", [(1, 1), (1, 10)], "#d00000", "front"))
        window.board.set_layout(layout)

        _, _, _, moved = window.board.optimize_wire_routes(scope="whole_board", allow_cross_side=False, allow_move_components=True)

        assert moved == 0
        assert [(component.row, component.col) for component in layout.components] == [(1, 1), (1, 10)]
    finally:
        window._set_dirty(False)
        window.close()
        window.deleteLater()
        app.processEvents()

def test_qt_select_all_and_edge_duplicate(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    qt_widgets = pytest.importorskip("PySide6.QtWidgets", exc_type=ImportError)
    QApplication = qt_widgets.QApplication

    from perfboard_planner.core.models import Component, ComponentPin, Wire, Via
    from perfboard_planner.ui.qt.app import MainWindow, PinEditorDialog

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        window.board.set_layout(Layout(rows=5, cols=5))
        window.layout_model.components.append(Component("U1", 3, 3, 2, 2, "#ffcc66"))
        window.layout_model.wires.append(Wire("", [(0, 0), (0, 2)], "#d00000", "front"))
        window.layout_model.wires.append(Wire("", [(1, 0), (1, 2)], "#00aa00", "front"))
        window.layout_model.vias.append(Via(2, 2))
        window.board.hidden_wire_colors.add("#00aa00")

        window.select_all_items()
        assert window.board.selected == {("component", 0), ("wire", 0), ("via", 0)}

        window.toggle_wire_color("#d00000")
        assert window.board.selected == {("component", 0), ("via", 0)}
        window.toggle_wire_color("#d00000")

        window.set_side("back")
        assert window.board.selected == {("via", 0)}
        window.set_side("front")

        window.clear_selection()
        assert window.board.selected == set()

        window.swap_component_sides()
        assert [component.side for component in window.layout_model.components] == ["back"]

        window._set_board_value("rows", 6)
        window._set_board_value("cols", 7)
        window._set_board_value("spacing", 30)
        assert (window.layout_model.rows, window.layout_model.cols, window.layout_model.spacing) == (6, 7, 30)

        editor = PinEditorDialog(
            Component(
                "U1",
                0,
                0,
                8,
                13,
                "#ffcc66",
                pins=[ComponentPin(str(i), i, -1) for i in range(13)] + [ComponentPin(f"R{i}", i, 8) for i in range(13)],
            )
        )
        try:
            editor.refresh_grid()
            rows, cols = editor._range()
            expected_cells = len(rows) * len(cols)
            assert len(editor.grid_frame.findChildren(qt_widgets.QToolButton)) == expected_cells
            old_buttons = dict(editor.grid_buttons)
            editor.toggle_pin_cell(0, -1)
            assert len(editor.grid_frame.findChildren(qt_widgets.QToolButton)) == expected_cells
            assert editor.grid_buttons == old_buttons
            assert editor.selected_pin_name == "0"
            editor.toggle_pin_cell(0, 0)
            assert len(editor.grid_frame.findChildren(qt_widgets.QToolButton)) == expected_cells
            assert editor.selected_pin_name == "P1"
            assert editor.grid_frame.width() < 700
            assert editor.grid_frame.height() < 700
        finally:
            editor.close()
            editor.deleteLater()

        window.board.selected = {("component", 0)}
        window.duplicate_selected()
        assert (window.layout_model.components[1].row, window.layout_model.components[1].col) == (2, 2)
        assert window.layout_model.components[1].row + window.layout_model.components[1].height <= window.layout_model.rows
        assert window.layout_model.components[1].col + window.layout_model.components[1].width <= window.layout_model.cols
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()
