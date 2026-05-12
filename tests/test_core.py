from perfboard_planner.core.models import Component, ComponentPin, ComponentJumper, Wire, Via
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
