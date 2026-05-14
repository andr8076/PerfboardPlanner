from __future__ import annotations

import heapq
from typing import Dict, Iterable, Optional, Tuple

from ...core.geometry import board_contains, component_pin_absolute
from ...core.models import Via, Wire

GridPoint = Tuple[int, int]


class BoardRoutingMixin:
    """Route suggestion and wire optimization behavior for ``BoardView``.

    The mixin expects the host widget to provide board geometry/view helpers,
    layout state, selection state, and Qt signals. Keeping the routing feature
    here prevents the main board view from becoming a catch-all for pathfinding
    and optimization code.
    """

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

    def _route_clearance_penalty(self, point: GridPoint, blocked: set[GridPoint], wire_cells: set[GridPoint], endpoints: set[GridPoint]) -> int:
        """Soft cost for hugging obstacles even when the cell itself is legal."""
        if point in endpoints:
            return 0
        rows, cols = self.layout_model.rows, self.layout_model.cols
        penalty = 0
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            neighbor = (point[0] + dr, point[1] + dc)
            if not board_contains(neighbor[0], neighbor[1], rows, cols):
                penalty += 1
            elif neighbor in blocked:
                penalty += 5
            elif neighbor in wire_cells and neighbor not in endpoints:
                penalty += 3
        for dr, dc in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
            neighbor = (point[0] + dr, point[1] + dc)
            if neighbor in blocked:
                penalty += 1
        return penalty

    @staticmethod
    def _preferred_directions(current: GridPoint, end: GridPoint) -> list[tuple[int, int]]:
        vertical = (1, 0) if end[0] >= current[0] else (-1, 0)
        horizontal = (0, 1) if end[1] >= current[1] else (0, -1)
        directions: list[tuple[int, int]] = []
        if abs(end[1] - current[1]) >= abs(end[0] - current[0]):
            directions.extend([horizontal, vertical])
        else:
            directions.extend([vertical, horizontal])
        for direction in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            if direction not in directions:
                directions.append(direction)
        return directions

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

        Component bodies, keepout cells, and already-routed wire cells are hard
        blocks, except for the two explicit endpoints. This keeps generated
        wires from being stacked on top of existing wires.
        """
        if start == end:
            return [start]
        rows = self.layout_model.rows
        cols = self.layout_model.cols
        if not (board_contains(start[0], start[1], rows, cols) and board_contains(end[0], end[1], rows, cols)):
            return []

        blocked = self._route_blocked_cells_for_side(side, start, end)
        wire_cells = self._route_wire_cells_for_side(side, ignore_wire_indexes=ignore_wire_indexes, extra_occupied=extra_occupied)
        endpoints = {start, end}

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

            for direction in self._preferred_directions(current, end):
                nr, nc = current[0] + direction[0], current[1] + direction[1]
                nxt = (nr, nc)
                if not board_contains(nr, nc, rows, cols) or nxt in blocked:
                    continue
                if nxt in wire_cells and nxt not in {start, end}:
                    continue

                step_cost = 10 + self._route_clearance_penalty(nxt, blocked, wire_cells, endpoints)
                if prev_dir is not None and direction != prev_dir:
                    step_cost += 14

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

    def _suggest_route_plan(self, start: GridPoint, end: GridPoint) -> tuple[list[tuple[list[GridPoint], str]], list[GridPoint]]:
        helper_wire = Wire("", [start, end], self.current_wire_color, side=self.side)
        direct = self._suggest_route_on_side(start, end, self.side)
        direct_score = self._route_cost(direct, helper_wire) if len(direct) >= 2 else 10**9
        via_choice = None
        if self.allow_route_suggestion_cross_side:
            occupied_by_side = {
                "front": self._wire_cells_from_wires(self.layout_model.wires, "front"),
                "back": self._wire_cells_from_wires(self.layout_model.wires, "back"),
            }
            via_choice = self._best_cross_side_route(
                helper_wire,
                start,
                end,
                target_indexes=set(),
                occupied_by_side=occupied_by_side,
            )

        if via_choice is not None and via_choice[0] < direct_score:
            _, via_a, via_b, route_a, route_b, route_c = via_choice
            other_side = "back" if self.side == "front" else "front"
            return (
                [(route_a, self.side), (route_b, other_side), (route_c, self.side)],
                [via_a, via_b],
            )
        if len(direct) >= 2:
            return ([(direct, self.side)], [])
        return ([], [])

    def _route_metrics(self, route: list[GridPoint]) -> tuple[int, int]:
        length = 0
        bends = 0
        prev_dir: Optional[tuple[int, int]] = None
        for a, b in zip(route, route[1:]):
            length += abs(b[0] - a[0]) + abs(b[1] - a[1])
            direction = (0 if b[0] == a[0] else (1 if b[0] > a[0] else -1), 0 if b[1] == a[1] else (1 if b[1] > a[1] else -1))
            if prev_dir is not None and direction != prev_dir:
                bends += 1
            prev_dir = direction
        return length, bends

    def _wire_is_power_ground_or_rail(self, wire: Wire) -> bool:
        label = f"{wire.name} {wire.group}".lower()
        power_tokens = ("gnd", "ground", "vcc", "vdd", "vss", "+5", "5v", "3v3", "3.3v", "vin", "vbat", "power", "rail")
        if any(token in label for token in power_tokens):
            return True
        if len(wire.points) < 2:
            return False
        start, end = wire.points[0], wire.points[-1]
        length = abs(end[0] - start[0]) + abs(end[1] - start[1])
        straight = start[0] == end[0] or start[1] == end[1]
        near_edge = (
            start[0] <= 1 and end[0] <= 1
            or start[0] >= self.layout_model.rows - 2 and end[0] >= self.layout_model.rows - 2
            or start[1] <= 1 and end[1] <= 1
            or start[1] >= self.layout_model.cols - 2 and end[1] >= self.layout_model.cols - 2
        )
        return straight and near_edge and length >= max(6, max(self.layout_model.rows, self.layout_model.cols) // 3)

    def _route_cost(self, route: list[GridPoint], wire: Optional[Wire] = None, *, via_count: int = 0) -> int:
        if not route or len(route) < 2:
            return 10**9
        length, bends = self._route_metrics(route)
        bend_penalty = 24 if wire is not None and self._wire_is_power_ground_or_rail(wire) else 16
        # Solderability score: short jumpers are best, bends cost time and
        # mistakes, and vias are deliberately expensive so cross-side routing
        # wins only when it materially improves or unblocks the route.
        return length * 10 + bends * bend_penalty + via_count * 95

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

    def _candidate_via_points(self, start: GridPoint, end: GridPoint, side: str, other_side: str, limit: Optional[int] = None) -> list[GridPoint]:
        if limit is None:
            # Small boards only need a handful of options, but larger or more
            # congested layouts benefit from a wider search around the corridor.
            limit = min(96, max(24, self.layout_model.rows + self.layout_model.cols))
        blocked_side = self._route_blocked_cells_for_side(side, start, end)
        blocked_other = self._route_blocked_cells_for_side(other_side, start, end)
        rows, cols = self.layout_model.rows, self.layout_model.cols
        sr, sc = start; er, ec = end
        existing_vias = {(via.row, via.col) for via in self.layout_model.vias}
        candidates: list[tuple[int, GridPoint]] = []
        for r in range(rows):
            for c in range(cols):
                pt = (r, c)
                if pt in {start, end} or pt in blocked_side or pt in blocked_other:
                    continue
                # Prefer existing via holes, then places near the start/end
                # corridor. Keep enough candidates for routes that must leave
                # the obvious corridor to get around components or keepouts.
                dist_start = abs(r - sr) + abs(c - sc)
                dist_end = abs(r - er) + abs(c - ec)
                corridor = abs((r - sr) * (ec - sc) - (c - sc) * (er - sr)) if start != end else 0
                balance = abs(dist_start - dist_end)
                existing_bonus = -220 if pt in existing_vias else 0
                candidates.append((existing_bonus + min(dist_start, dist_end) * 5 + corridor + balance * 2, pt))
        candidates.sort(key=lambda item: item[0])
        return [pt for _, pt in candidates[:limit]]

    def _ranked_via_points(self, candidates: list[GridPoint], anchor: GridPoint, *, limit: int = 24) -> list[GridPoint]:
        existing_vias = {(via.row, via.col) for via in self.layout_model.vias}
        ranked = sorted(
            candidates,
            key=lambda pt: (
                0 if pt in existing_vias else 1,
                abs(pt[0] - anchor[0]) + abs(pt[1] - anchor[1]),
                pt[0],
                pt[1],
            ),
        )
        return ranked[: min(limit, len(ranked))]

    def _route_endpoint_freedom(self, point: GridPoint, side: str, other_endpoint: GridPoint) -> int:
        blocked = self._route_blocked_cells_for_side(side, point, other_endpoint)
        rows, cols = self.layout_model.rows, self.layout_model.cols
        free = 0
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nr, nc = point[0] + dr, point[1] + dc
            if board_contains(nr, nc, rows, cols) and (nr, nc) not in blocked:
                free += 1
        return free

    def _wire_route_priority(self, wire: Wire) -> tuple[int, int, int, int]:
        start, end = wire.points[0], wire.points[-1]
        start_free = self._route_endpoint_freedom(start, wire.side, end)
        end_free = self._route_endpoint_freedom(end, wire.side, start)
        manhattan = abs(end[0] - start[0]) + abs(end[1] - start[1])
        min_free = min(start_free, end_free)
        if self._wire_is_power_ground_or_rail(wire):
            bucket = 0
            distance_tiebreak = -manhattan
        elif min_free <= 1:
            bucket = 1
            distance_tiebreak = -manhattan
        elif manhattan <= max(4, min(self.layout_model.rows, self.layout_model.cols) // 4):
            bucket = 2
            distance_tiebreak = manhattan
        else:
            bucket = 3
            distance_tiebreak = -manhattan
        # Routing order follows the preferred manual workflow: draw direct
        # endpoint wires, then optimize rails first, constrained pins next,
        # short local jumpers next, and flexible signal runs last.
        return (bucket, min_free, start_free + end_free, distance_tiebreak)

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
        near_start = self._ranked_via_points(candidates, start)
        near_end = self._ranked_via_points(candidates, end)
        best: Optional[tuple[int, GridPoint, GridPoint, list[GridPoint], list[GridPoint], list[GridPoint]]] = None
        route_a_cache: dict[GridPoint, list[GridPoint]] = {}
        route_b_cache: dict[tuple[GridPoint, GridPoint], list[GridPoint]] = {}
        for via_a in near_start:
            route_a = route_a_cache.get(via_a)
            if route_a is None:
                route_a = self._suggest_route_on_side(start, via_a, side, ignore_wire_indexes=target_indexes, extra_occupied=occupied_by_side.get(side, set()))
                route_a_cache[via_a] = route_a
            if len(route_a) < 2:
                continue
            occ_side_a = set(occupied_by_side.get(side, set())) | self._route_cells(route_a)
            for via_b in near_end:
                if via_a == via_b:
                    continue
                route_b_key = (via_a, via_b)
                route_b = route_b_cache.get(route_b_key)
                if route_b is None:
                    route_b = self._suggest_route_on_side(via_a, via_b, other_side, ignore_wire_indexes=target_indexes, extra_occupied=occupied_by_side.get(other_side, set()))
                    route_b_cache[route_b_key] = route_b
                if len(route_b) < 2:
                    continue
                route_c = self._suggest_route_on_side(via_b, end, side, ignore_wire_indexes=target_indexes, extra_occupied=occ_side_a)
                if len(route_c) < 2:
                    continue
                score = self._route_cost(route_a, wire) + self._route_cost(route_b, wire) + self._route_cost(route_c, wire, via_count=2)
                if best is None or score < best[0]:
                    best = (score, via_a, via_b, route_a, route_b, route_c)
        return best

    def _target_net_groups(self, target_indexes: set[int], wires: list[Wire]) -> list[set[int]]:
        by_key: dict[tuple[str, str, str], list[int]] = {}
        for idx in target_indexes:
            wire = wires[idx]
            by_key.setdefault((wire.side, wire.color, wire.group), []).append(idx)

        groups: list[set[int]] = []
        for indexes in by_key.values():
            remaining = set(indexes)
            while remaining:
                seed = remaining.pop()
                group = {seed}
                endpoints = {wires[seed].points[0], wires[seed].points[-1]}
                changed = True
                while changed:
                    changed = False
                    for idx in list(remaining):
                        pts = {wires[idx].points[0], wires[idx].points[-1]}
                        if endpoints & pts:
                            remaining.remove(idx)
                            group.add(idx)
                            endpoints.update(pts)
                            changed = True
                if len(group) > 1:
                    groups.append(group)
        return groups

    def _route_connection_segments(
        self,
        source: Wire,
        start: GridPoint,
        end: GridPoint,
        *,
        target_indexes: set[int],
        occupied_by_side: Dict[str, set[GridPoint]],
        allow_cross_side: bool,
    ) -> Optional[tuple[int, list[Wire], list[GridPoint]]]:
        direct = self._suggest_route_on_side(
            start,
            end,
            source.side,
            ignore_wire_indexes=target_indexes,
            extra_occupied=occupied_by_side.get(source.side, set()),
        )
        direct_score = self._route_cost(direct, source) if len(direct) >= 2 else 10**9
        via_choice = None
        if allow_cross_side:
            via_choice = self._best_cross_side_route(source, start, end, target_indexes=target_indexes, occupied_by_side=occupied_by_side)

        if via_choice is not None and via_choice[0] < direct_score:
            _, via_a, via_b, route_a, route_b, route_c = via_choice
            other_side = "back" if source.side == "front" else "front"
            return (
                via_choice[0],
                [
                    self._copy_wire_with_route(source, route_a, source.side),
                    self._copy_wire_with_route(source, route_b, other_side),
                    self._copy_wire_with_route(source, route_c, source.side),
                ],
                [via_a, via_b],
            )
        if len(direct) >= 2:
            return (direct_score, [self._copy_wire_with_route(source, direct, source.side)], [])
        return None

    def _route_net_topology(
        self,
        group: set[int],
        original_wires: list[Wire],
        *,
        target_indexes: set[int],
        occupied_by_side: Dict[str, set[GridPoint]],
        allow_cross_side: bool,
    ) -> Optional[tuple[list[Wire], list[GridPoint]]]:
        points = sorted({pt for idx in group for pt in (original_wires[idx].points[0], original_wires[idx].points[-1])})
        if len(points) < 3 or len(points) > 8 or len(group) != len(points) - 1:
            return None

        source = original_wires[min(group)]
        edges: list[tuple[int, int, int, list[Wire], list[GridPoint]]] = []
        for a_idx, start in enumerate(points):
            for b_idx in range(a_idx + 1, len(points)):
                end = points[b_idx]
                plan = self._route_connection_segments(
                    source,
                    start,
                    end,
                    target_indexes=target_indexes,
                    occupied_by_side=occupied_by_side,
                    allow_cross_side=allow_cross_side,
                )
                if plan is None:
                    continue
                score, segments, vias = plan
                edges.append((score, a_idx, b_idx, segments, vias))
        if len(edges) < len(points) - 1:
            return None

        parent = list(range(len(points)))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        chosen_segments: list[Wire] = []
        chosen_vias: list[GridPoint] = []
        chosen_edges = 0
        topology_occupied: Dict[str, set[GridPoint]] = {side: set(cells) for side, cells in occupied_by_side.items()}
        for _, a_idx, b_idx, _segments, _vias in sorted(edges, key=lambda item: item[0]):
            root_a = find(a_idx)
            root_b = find(b_idx)
            if root_a == root_b:
                continue
            # Re-plan against the segments already chosen for this net, so an
            # equivalent A-C-B topology does not stack its own new wires.
            replanned = self._route_connection_segments(
                source,
                points[a_idx],
                points[b_idx],
                target_indexes=target_indexes,
                occupied_by_side=topology_occupied,
                allow_cross_side=allow_cross_side,
            )
            if replanned is None:
                continue
            _, segments, vias = replanned
            parent[root_b] = root_a
            chosen_segments.extend(segments)
            chosen_vias.extend(vias)
            for segment in segments:
                topology_occupied.setdefault(segment.side, set()).update(self._route_cells(segment.points))
            chosen_edges += 1
            if chosen_edges >= len(points) - 1:
                break

        if len({find(i) for i in range(len(points))}) != 1:
            return None
        return chosen_segments, chosen_vias

    def _route_endpoint_score_for_component(self, comp_idx: int, row: int, col: int, target_indexes: set[int]) -> int:
        comp = self.layout_model.components[comp_idx]
        current_pins = self._component_pin_points_at(comp, comp.row, comp.col)
        candidate_pins = self._component_pin_points_at(comp, row, col)
        substitutions = {current_pins[name]: candidate_pins[name] for name in current_pins.keys() & candidate_pins.keys()}
        score = 0
        for wire_idx in target_indexes:
            wire = self.layout_model.wires[wire_idx]
            if wire.side != comp.side or len(wire.points) < 2:
                continue
            start, end = self._wire_endpoint_points(wire)
            start = substitutions.get(start, start)
            end = substitutions.get(end, end)
            score += abs(end[0] - start[0]) + abs(end[1] - start[1])
        return score

    def _apply_component_move_for_routes(self, comp_idx: int, row: int, col: int) -> None:
        comp = self.layout_model.components[comp_idx]
        substitutions = self._component_pin_substitutions(comp, comp.row, comp.col, row, col)
        comp.row = row
        comp.col = col
        self._move_wire_endpoints_for_pin_substitutions(comp.side, substitutions)

    def _move_unlocked_components_for_routes(self, target_indexes: set[int], scope: str) -> int:
        target_sides = {self.side} if scope == "current_side" else {"front", "back"}
        endpoint_points_by_side: dict[str, set[GridPoint]] = {"front": set(), "back": set()}
        for wire_idx in target_indexes:
            wire = self.layout_model.wires[wire_idx]
            if len(wire.points) >= 2:
                endpoint_points_by_side.setdefault(wire.side, set()).update(self._wire_endpoint_points(wire))

        movable = []
        for idx, comp in enumerate(self.layout_model.components):
            if comp.locked or comp.side not in target_sides or self._is_item_hidden_by_group(comp):
                continue
            if self._component_has_locked_wire_endpoint(idx):
                continue
            pin_points = set(self._component_pin_points_at(comp, comp.row, comp.col).values())
            if pin_points & endpoint_points_by_side.get(comp.side, set()):
                movable.append(idx)

        moved = 0
        for idx in sorted(movable, key=lambda comp_idx: self._route_endpoint_score_for_component(comp_idx, self.layout_model.components[comp_idx].row, self.layout_model.components[comp_idx].col, target_indexes), reverse=True):
            comp = self.layout_model.components[idx]
            current_score = self._route_endpoint_score_for_component(idx, comp.row, comp.col, target_indexes)
            best = (current_score, comp.row, comp.col)
            for row in range(0, self.layout_model.rows - comp.height + 1):
                for col in range(0, self.layout_model.cols - comp.width + 1):
                    if row == comp.row and col == comp.col:
                        continue
                    if not self._component_can_move_to(idx, row, col):
                        continue
                    score = self._route_endpoint_score_for_component(idx, row, col, target_indexes)
                    travel = abs(row - comp.row) + abs(col - comp.col)
                    if (score, travel) < (best[0], abs(best[1] - comp.row) + abs(best[2] - comp.col)):
                        best = (score, row, col)
            if best[0] < current_score:
                self._apply_component_move_for_routes(idx, best[1], best[2])
                moved += 1
        return moved

    def optimize_wire_routes(self, *, scope: str = "current_side", allow_cross_side: bool = False, allow_move_components: bool = False) -> tuple[int, int, int, int]:
        """Reroute existing wires while preserving their endpoints.

        Returns (updated_wire_count, failed_wire_count, added_via_count, moved_component_count).
        Scope is either ``current_side`` or ``whole_board``. If cross-side routing
        is allowed, a wire may be split into same-color segments on both sides
        with vias inserted at the transitions. If component movement is allowed,
        unlocked components may move on their existing side before routing.
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
            return (0, 0, 0, 0)

        self.beforeLayoutChange.emit("Suggest all wires")
        moved_components = self._move_unlocked_components_for_routes(target_indexes, scope) if allow_move_components else 0
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

        routed_segments: dict[int, list[Wire]] = {}
        failed_indexes: set[int] = set()
        consumed_indexes: set[int] = set()
        target_order = sorted(target_indexes, key=lambda idx: self._wire_route_priority(original_wires[idx]))

        def add_vias(points: Iterable[GridPoint], source: Wire) -> int:
            count = 0
            for point in points:
                if not self._via_exists(point):
                    self.layout_model.vias.append(Via(point[0], point[1], color=source.color, group=source.group))
                    count += 1
            return count

        def reserve_segments(segments: list[Wire]) -> None:
            for seg in segments:
                occupied_by_side.setdefault(seg.side, set()).update(self._route_cells(seg.points))

        for group in sorted(self._target_net_groups(target_indexes, original_wires), key=lambda grp: min(target_order.index(idx) for idx in grp)):
            if group & consumed_indexes:
                continue
            topology = self._route_net_topology(
                group,
                original_wires,
                target_indexes=target_indexes,
                occupied_by_side=occupied_by_side,
                allow_cross_side=allow_cross_side,
            )
            if topology is None:
                continue
            segments, via_points = topology
            anchor = min(group)
            source = original_wires[anchor]
            added_vias += add_vias(via_points, source)
            reserve_segments(segments)
            routed_segments[anchor] = segments
            consumed_indexes.update(group)
            routed += len(group)

        for i in target_order:
            if i in consumed_indexes:
                continue
            source = original_wires[i]
            start, end = source.points[0], source.points[-1]
            plan = self._route_connection_segments(
                source,
                start,
                end,
                target_indexes=target_indexes,
                occupied_by_side=occupied_by_side,
                allow_cross_side=allow_cross_side,
            )
            if plan is not None:
                _, segments, via_points = plan
                added_vias += add_vias(via_points, source)
                reserve_segments(segments)
                routed_segments[i] = segments
                routed += 1
            else:
                occupied_by_side.setdefault(source.side, set()).update(self._route_cells(source.points))
                failed_indexes.add(i)
                failed += 1

        for i, source in enumerate(original_wires):
            if i in routed_segments:
                new_wires.extend(routed_segments[i])
            elif i in consumed_indexes:
                continue
            elif i in failed_indexes or i not in target_indexes:
                new_wires.append(source)

        self.layout_model.wires = new_wires
        self.selected.clear()
        self.layoutChanged.emit()
        self.selectionChanged.emit()
        self.update()
        return routed, failed, added_vias, moved_components

