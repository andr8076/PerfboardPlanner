from perfboard_planner.core.commands import Command, CommandStack
from perfboard_planner.core.models import Wire, Via
from perfboard_planner.core.storage import layout_from_dict, layout_to_dict
from perfboard_planner.core.connectivity import build_graph, connected_nodes


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
