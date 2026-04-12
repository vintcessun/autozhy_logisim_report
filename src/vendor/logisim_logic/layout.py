from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import heapq

from .geometry import get_component_geometry, get_component_visual_bounds
from .model import RawCircuit, RawComponent, RawProject


Bounds = tuple[int, int, int, int]
ComponentSpec = tuple[str, dict[str, str], str | None]
Padding = int | tuple[int, int] | tuple[int, int, int, int]
Point = tuple[int, int]
_GRID = 10

_DEFAULT_IGNORES = {"Text", "Tunnel"}


@dataclass(frozen=True, slots=True)
class ComponentOverlap:
    first: RawComponent
    second: RawComponent
    first_bounds: Bounds
    second_bounds: Bounds

    @property
    def intersection(self) -> Bounds:
        left = max(self.first_bounds[0], self.second_bounds[0])
        top = max(self.first_bounds[1], self.second_bounds[1])
        right = min(self.first_bounds[0] + self.first_bounds[2], self.second_bounds[0] + self.second_bounds[2])
        bottom = min(self.first_bounds[1] + self.first_bounds[3], self.second_bounds[1] + self.second_bounds[3])
        return (left, top, max(0, right - left), max(0, bottom - top))

    def describe(self) -> str:
        return (
            f"{self.first.name} {self.first.loc} overlaps "
            f"{self.second.name} {self.second.loc}"
        )


@dataclass(frozen=True, slots=True)
class PortAttachmentPlacement:
    port_point: Point
    lead_point: Point
    loc: Point
    facing: str
    path: tuple[Point, ...]


def _normalize_padding(padding: Padding = 0) -> tuple[int, int, int, int]:
    if isinstance(padding, int):
        return (padding, padding, padding, padding)
    if len(padding) == 2:
        horizontal, vertical = padding
        return (horizontal, horizontal, vertical, vertical)
    if len(padding) == 4:
        left, right, top, bottom = padding
        return (left, right, top, bottom)
    raise ValueError(f"invalid padding {padding!r}")


def _snap(value: int, grid: int = _GRID) -> int:
    return round(value / grid) * grid


def _snap_up(value: int, grid: int = _GRID) -> int:
    remainder = value % grid
    return value if remainder == 0 else value + (grid - remainder)


def _floor_grid(value: int, grid: int = _GRID) -> int:
    return (value // grid) * grid


def _ceil_grid(value: int, grid: int = _GRID) -> int:
    return ((value + grid - 1) // grid) * grid


def _grid_values(start: int, end: int, grid: int = _GRID) -> range:
    return range(_floor_grid(start, grid), _ceil_grid(end, grid) + grid, grid)


def expand_bounds(bounds: Bounds, padding: Padding = 0) -> Bounds:
    left_pad, right_pad, top_pad, bottom_pad = _normalize_padding(padding)
    return (
        bounds[0] - left_pad,
        bounds[1] - top_pad,
        bounds[2] + left_pad + right_pad,
        bounds[3] + top_pad + bottom_pad,
    )


def combine_bounds(bounds_list: list[Bounds]) -> Bounds:
    if not bounds_list:
        return (0, 0, 0, 0)
    left = min(bounds[0] for bounds in bounds_list)
    top = min(bounds[1] for bounds in bounds_list)
    right = max(bounds[0] + bounds[2] for bounds in bounds_list)
    bottom = max(bounds[1] + bounds[3] for bounds in bounds_list)
    return (left, top, right - left, bottom - top)


def component_bounds(
    component: RawComponent,
    *,
    project: RawProject | None = None,
    padding: Padding = 0,
    visual: bool = False,
) -> Bounds:
    bounds = get_component_visual_bounds(component, project=project) if visual else get_component_geometry(component, project=project).absolute_bounds(component.loc)
    return expand_bounds(bounds, padding)


def component_extents(
    component: RawComponent,
    *,
    project: RawProject | None = None,
    padding: Padding = 0,
    visual: bool = False,
) -> tuple[int, int, int, int]:
    left, top, width, height = component_bounds(component, project=project, padding=padding, visual=visual)
    return (-left + component.loc[0], left + width - component.loc[0], -top + component.loc[1], top + height - component.loc[1])


def spec_component(name: str, attrs: dict[str, str], *, lib: str | None = None) -> RawComponent:
    return RawComponent(name=name, loc=(0, 0), lib=lib, attrs=[])


def spec_extents(
    spec: ComponentSpec,
    *,
    project: RawProject | None = None,
    padding: Padding = 0,
    visual: bool = True,
) -> tuple[int, int, int, int]:
    name, attrs, lib = spec
    component = RawComponent(
        name=name,
        loc=(0, 0),
        lib=lib,
        attrs=[],
    )
    for key, value in attrs.items():
        component.set(key, value)
    return component_extents(component, project=project, padding=padding, visual=visual)


def layout_row_locations(
    specs: list[ComponentSpec],
    *,
    left: int,
    y: int,
    gap: int,
    project: RawProject | None = None,
    padding: Padding = 20,
    visual: bool = True,
) -> list[tuple[int, int]]:
    if not specs:
        return []
    locs: list[tuple[int, int]] = []
    current_left = left
    previous_right = 0
    for index, spec in enumerate(specs):
        comp_left, comp_right, _, _ = spec_extents(spec, project=project, padding=padding, visual=visual)
        if index == 0:
            loc_x = _snap(current_left + comp_left)
        else:
            loc_x = _snap(current_left + previous_right + gap + comp_left)
        locs.append((loc_x, y))
        current_left = loc_x
        previous_right = comp_right
    return locs


def layout_column_locations(
    specs: list[ComponentSpec],
    *,
    x: int,
    top: int,
    gap: int,
    project: RawProject | None = None,
    padding: Padding = 20,
    visual: bool = True,
) -> list[tuple[int, int]]:
    if not specs:
        return []
    locs: list[tuple[int, int]] = []
    current_top = top
    previous_bottom = 0
    for index, spec in enumerate(specs):
        _, _, comp_top, comp_bottom = spec_extents(spec, project=project, padding=padding, visual=visual)
        if index == 0:
            loc_y = _snap(current_top + comp_top)
        else:
            loc_y = _snap(current_top + previous_bottom + gap + comp_top)
        locs.append((x, loc_y))
        current_top = loc_y
        previous_bottom = comp_bottom
    return locs


def default_splitter_pitch(
    bus_width: int,
    *,
    facing: str = "north",
    project: RawProject | None = None,
    padding: Padding = 10,
) -> int:
    attrs = {"facing": facing, "fanout": "1", "incoming": str(bus_width), "appear": "center"}
    for index in range(bus_width):
        attrs[f"bit{index}"] = "0" if index == 0 else "none"
    left, right, _, _ = spec_extents(("Splitter", attrs, "0"), project=project, padding=padding, visual=False)
    return _snap_up(max(40, left + right + 20))


def _bounds_overlap(first: Bounds, second: Bounds) -> bool:
    first_right = first[0] + first[2]
    second_right = second[0] + second[2]
    first_bottom = first[1] + first[3]
    second_bottom = second[1] + second[3]
    return max(first[0], second[0]) < min(first_right, second_right) and max(first[1], second[1]) < min(first_bottom, second_bottom)


def _nearest_side(bounds: Bounds, offset: Point) -> Point:
    left = bounds[0]
    right = bounds[0] + bounds[2]
    top = bounds[1]
    bottom = bounds[1] + bounds[3]
    distances = {
        (-1, 0): abs(offset[0] - left),
        (1, 0): abs(offset[0] - right),
        (0, -1): abs(offset[1] - top),
        (0, 1): abs(offset[1] - bottom),
    }
    best = min(distances.values())
    vertical = [side for side in ((0, -1), (0, 1)) if distances[side] == best]
    if vertical:
        return vertical[0]
    horizontal = [side for side in ((-1, 0), (1, 0)) if distances[side] == best]
    return horizontal[0]


def _side_for_port(component: RawComponent, port_name: str, *, project: RawProject | None = None) -> tuple[Point, Point]:
    geometry = get_component_geometry(component, project=project)
    port = geometry.port(port_name)
    side = _nearest_side(geometry.bounds, port.offset)
    tangent = (0, 1) if side[0] else (1, 0)
    return side, tangent


def attachment_facing_for_port(component: RawComponent, port_name: str, *, project: RawProject | None = None) -> str:
    side, _ = _side_for_port(component, port_name, project=project)
    if side[0] < 0:
        return "east"
    if side[0] > 0:
        return "west"
    if side[1] < 0:
        return "south"
    return "north"


def _candidate_offsets(step: int, limit: int) -> list[int]:
    offsets = [0]
    current = step
    while current <= limit:
        offsets.extend((current, -current))
        current += step
    return offsets


def _compact_points(points: list[Point]) -> tuple[Point, ...]:
    compact: list[Point] = []
    for point in points:
        if compact and point == compact[-1]:
            continue
        compact.append(point)
    if len(compact) >= 3:
        reduced = [compact[0]]
        for index in range(1, len(compact) - 1):
            prev = reduced[-1]
            point = compact[index]
            nxt = compact[index + 1]
            if (prev[0] == point[0] == nxt[0]) or (prev[1] == point[1] == nxt[1]):
                continue
            reduced.append(point)
        reduced.append(compact[-1])
        compact = reduced
    return tuple(compact)


def _segment_points(start: Point, end: Point, *, grid: int = _GRID) -> tuple[Point, ...]:
    if start[0] == end[0]:
        x = start[0]
        step = grid if end[1] >= start[1] else -grid
        return tuple((x, y) for y in range(start[1], end[1] + step, step))
    if start[1] == end[1]:
        y = start[1]
        step = grid if end[0] >= start[0] else -grid
        return tuple((x, y) for x in range(start[0], end[0] + step, step))
    raise ValueError(f"segment must be orthogonal: {start!r} -> {end!r}")


def _path_is_clear(path: tuple[Point, ...], blocked: set[Point], *, allow: set[Point], grid: int = _GRID) -> bool:
    for start, end in zip(path, path[1:]):
        for index, point in enumerate(_segment_points(start, end, grid=grid)):
            if index == 0 and point == start:
                continue
            if point in blocked and point not in allow:
                return False
    return True


def _wire_points(circuit: RawCircuit, *, grid: int = _GRID) -> set[Point]:
    occupied: set[Point] = set()
    for wire in circuit.wires:
        if wire.start[0] == wire.end[0]:
            x = wire.start[0]
            y0, y1 = sorted((wire.start[1], wire.end[1]))
            for y in range(y0, y1 + grid, grid):
                occupied.add((x, y))
        elif wire.start[1] == wire.end[1]:
            y = wire.start[1]
            x0, x1 = sorted((wire.start[0], wire.end[0]))
            for x in range(x0, x1 + grid, grid):
                occupied.add((x, y))
    return occupied


def _component_blockers(
    circuit: RawCircuit,
    *,
    project: RawProject | None = None,
    grid: int = _GRID,
    padding: int = 10,
    ignore_components: set[int] | None = None,
) -> set[Point]:
    ignored = set() if ignore_components is None else set(ignore_components)
    blocked: set[Point] = set()
    for component in circuit.components:
        if id(component) in ignored or component.name == "Text":
            continue
        x, y, wid, ht = component_bounds(component, project=project, padding=padding)
        for gx in _grid_values(x - grid, x + wid + grid, grid):
            for gy in _grid_values(y - grid, y + ht + grid, grid):
                blocked.add((gx, gy))
    openings: set[Point] = set()
    for component in circuit.components:
        if id(component) in ignored or component.name == "Text":
            continue
        geometry = get_component_geometry(component, project=project)
        left = geometry.bounds[0]
        right = geometry.bounds[0] + geometry.bounds[2]
        top = geometry.bounds[1]
        bottom = geometry.bounds[1] + geometry.bounds[3]
        for port in geometry.ports:
            px = component.loc[0] + port.offset[0]
            py = component.loc[1] + port.offset[1]
            openings.add((px, py))
            side = min(
                (
                    (abs(port.offset[0] - left), (-grid, 0)),
                    (abs(port.offset[0] - right), (grid, 0)),
                    (abs(port.offset[1] - top), (0, -grid)),
                    (abs(port.offset[1] - bottom), (0, grid)),
                ),
                key=lambda item: item[0],
            )[1]
            for step in range(1, 5):
                openings.add((px + side[0] * step, py + side[1] * step))
    blocked.difference_update(openings)
    return blocked


def route_circuit_path(
    circuit: RawCircuit,
    start: Point,
    goal: Point,
    *,
    project: RawProject | None = None,
    grid: int = _GRID,
    margin: int = 80,
    component_padding: int = 10,
    avoid_wires: bool = True,
    ignore_components: set[int] | None = None,
) -> tuple[Point, ...] | None:
    blocked = _component_blockers(
        circuit,
        project=project,
        grid=grid,
        padding=component_padding,
        ignore_components=ignore_components,
    )
    if avoid_wires:
        blocked.update(_wire_points(circuit, grid=grid))
    blocked.discard(start)
    blocked.discard(goal)
    min_x = _floor_grid(min(start[0], goal[0]) - margin, grid)
    max_x = _ceil_grid(max(start[0], goal[0]) + margin, grid)
    min_y = _floor_grid(min(start[1], goal[1]) - margin, grid)
    max_y = _ceil_grid(max(start[1], goal[1]) + margin, grid)
    directions = ((grid, 0), (-grid, 0), (0, grid), (0, -grid))
    start_state = (start, -1)
    queue: list[tuple[float, float, tuple[Point, int]]] = [(0.0, 0.0, start_state)]
    costs = {start_state: 0.0}
    previous: dict[tuple[Point, int], tuple[Point, int] | None] = {start_state: None}
    while queue:
        _, cost, (point, direction_index) = heapq.heappop(queue)
        if point == goal:
            path: list[Point] = []
            state: tuple[Point, int] | None = (point, direction_index)
            while state is not None:
                path.append(state[0])
                state = previous[state]
            path.reverse()
            return _compact_points(path)
        if cost > costs.get((point, direction_index), float("inf")):
            continue
        for next_dir_index, (dx, dy) in enumerate(directions):
            nxt = (point[0] + dx, point[1] + dy)
            if nxt[0] < min_x or nxt[0] > max_x or nxt[1] < min_y or nxt[1] > max_y:
                continue
            if nxt in blocked and nxt != goal:
                continue
            bend_penalty = 0.6 if direction_index != -1 and direction_index != next_dir_index else 0.0
            new_cost = cost + 1.0 + bend_penalty
            state = (nxt, next_dir_index)
            if new_cost >= costs.get(state, float("inf")):
                continue
            costs[state] = new_cost
            previous[state] = (point, direction_index)
            heuristic = (abs(nxt[0] - goal[0]) + abs(nxt[1] - goal[1])) / grid
            heapq.heappush(queue, (new_cost + heuristic, new_cost, state))
    return None


def place_attached_component(
    circuit: RawCircuit,
    anchor: RawComponent,
    port_name: str,
    attached: RawComponent,
    *,
    attached_port_name: str | None = None,
    project: RawProject | None = None,
    distance: int = 20,
    step: int = _GRID,
    tangent_limit: int = 240,
    distance_limit: int = 240,
    ignore_names: set[str] | None = None,
    component_padding: int = 10,
) -> PortAttachmentPlacement:
    geometry = get_component_geometry(anchor, project=project)
    port = geometry.port(port_name)
    port_point = (anchor.loc[0] + port.offset[0], anchor.loc[1] + port.offset[1])
    side, tangent = _side_for_port(anchor, port_name, project=project)
    facing = attachment_facing_for_port(anchor, port_name, project=project)
    attached_geometry = get_component_geometry(attached, project=project)
    attached_offset = (0, 0)
    if attached_port_name is not None:
        attached_port = attached_geometry.port(attached_port_name)
        attached_offset = attached_port.offset
    ignored = {"Text"} if ignore_names is None else set(ignore_names)
    existing = [component for component in circuit.components if component is not attached and component.name not in ignored]
    blocked = _component_blockers(circuit, project=project, grid=step, padding=component_padding)
    blocked.update(_wire_points(circuit, grid=step))
    blocked.discard(port_point)

    def loc_for_attachment_point(attach_point: Point) -> Point:
        return (attach_point[0] - attached_offset[0], attach_point[1] - attached_offset[1])

    def make_candidate(loc: Point) -> RawComponent:
        candidate = deepcopy(attached)
        candidate.loc = loc
        return candidate

    fallback_candidates: list[tuple[Point, Point, Point]] = []
    for extra_distance in range(0, distance_limit + step, step):
        lead_distance = distance + extra_distance
        lead_point = (
            port_point[0] + side[0] * lead_distance,
            port_point[1] + side[1] * lead_distance,
        )
        for tangent_offset in _candidate_offsets(step, tangent_limit):
            attach_point = (
                lead_point[0] + tangent[0] * tangent_offset,
                lead_point[1] + tangent[1] * tangent_offset,
            )
            loc = loc_for_attachment_point(attach_point)
            candidate = make_candidate(loc)
            candidate_bounds = component_bounds(candidate, project=project)
            if any(_bounds_overlap(candidate_bounds, component_bounds(other, project=project)) for other in existing):
                continue
            path = _compact_points([port_point, lead_point, attach_point])
            if _path_is_clear(path, blocked, allow={port_point, attach_point}, grid=step):
                return PortAttachmentPlacement(
                    port_point=port_point,
                    lead_point=lead_point,
                    loc=loc,
                    facing=facing,
                    path=path,
                )
            if len(fallback_candidates) < 8:
                fallback_candidates.append((lead_point, attach_point, loc))

    for lead_point, attach_point, loc in fallback_candidates:
        path = route_circuit_path(
            circuit,
            port_point,
            attach_point,
            project=project,
            component_padding=component_padding,
            grid=step,
            margin=120,
        )
        if path is None:
            continue
        return PortAttachmentPlacement(
            port_point=port_point,
            lead_point=lead_point,
            loc=loc,
            facing=facing,
            path=path,
        )

    fallback_loc = (
        port_point[0] + side[0] * distance,
        port_point[1] + side[1] * distance,
    )
    fallback_attach_point = (
        fallback_loc[0] + attached_offset[0],
        fallback_loc[1] + attached_offset[1],
    )
    return PortAttachmentPlacement(
        port_point=port_point,
        lead_point=fallback_attach_point,
        loc=fallback_loc,
        facing=facing,
        path=_compact_points([port_point, fallback_attach_point]),
    )


def find_component_overlaps(
    circuit: RawCircuit,
    *,
    project: RawProject | None = None,
    ignore_names: set[str] | None = None,
    visual: bool = False,
) -> list[ComponentOverlap]:
    ignored = _DEFAULT_IGNORES if ignore_names is None else set(ignore_names)
    placed = [(component, component_bounds(component, project=project, visual=visual)) for component in circuit.components if component.name not in ignored]
    overlaps: list[ComponentOverlap] = []
    for index, (first, first_bounds) in enumerate(placed):
        for second, second_bounds in placed[index + 1 :]:
            if _bounds_overlap(first_bounds, second_bounds):
                overlaps.append(
                    ComponentOverlap(
                        first=first,
                        second=second,
                        first_bounds=first_bounds,
                        second_bounds=second_bounds,
                    )
                )
    return overlaps
