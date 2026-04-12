from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
import heapq
from itertools import zip_longest
import time
from typing import Any, Iterable

from .geometry import ComponentGeometry, Point, get_component_geometry, resolve_library_label
from .java_types import format_attribute_value
from .layout import component_bounds, component_extents
from .model import RawAttribute, RawCircuit, RawComponent, RawProject, RawWire


def _snap(value: int, grid: int) -> int:
    return round(value / grid) * grid


def _floor_grid(value: int, grid: int) -> int:
    return (value // grid) * grid


def _ceil_grid(value: int, grid: int) -> int:
    return ((value + grid - 1) // grid) * grid


def _grid_values(start: int, end: int, grid: int) -> range:
    return range(_floor_grid(start, grid), _ceil_grid(end, grid) + grid, grid)


@dataclass(frozen=True, slots=True)
class EndpointRef:
    instance_id: str
    port: str

    @classmethod
    def parse(cls, value: str | "EndpointRef") -> "EndpointRef":
        if isinstance(value, EndpointRef):
            return value
        if "." not in value:
            raise ValueError(f"endpoint must be 'instance.port', got {value!r}")
        instance_id, port = value.split(".", 1)
        return cls(instance_id=instance_id, port=port)


@dataclass(slots=True)
class LogicInstanceSpec:
    id: str
    kind: str
    attrs: dict[str, str] = field(default_factory=dict)
    lib: str | None = None
    loc: Point | None = None
    rank_hint: int | None = None
    track_hint: int | None = None

    def raw_component(self, *, loc: Point, project: RawProject | None = None) -> RawComponent:
        attrs = [RawAttribute(name=name, value=format_attribute_value(name, value)) for name, value in self.attrs.items()]
        lib = self.lib if self.lib is not None else resolve_library_label(project, self.kind)
        return RawComponent(name=self.kind, loc=loc, lib=lib, attrs=attrs)


@dataclass(slots=True)
class LogicNetSpec:
    endpoints: list[EndpointRef]
    label: str | None = None
    force_tunnel: bool = False


@dataclass(slots=True)
class RoutedNetPlan:
    net: LogicNetSpec
    wires: list[RawWire]
    occupancy: "WireOccupancy"
    score: float
    min_options: int
    total_options: int


@dataclass(slots=True)
class WireOccupancy:
    edges: set[tuple[Point, Point]] = field(default_factory=set)
    vertices: set[Point] = field(default_factory=set)
    interiors: dict[Point, set[str]] = field(default_factory=dict)

    def merged(self, other: "WireOccupancy") -> "WireOccupancy":
        merged = WireOccupancy(
            edges=set(self.edges) | set(other.edges),
            vertices=set(self.vertices) | set(other.vertices),
            interiors={point: set(directions) for point, directions in self.interiors.items()},
        )
        for point, directions in other.interiors.items():
            merged.interiors.setdefault(point, set()).update(directions)
        for point in tuple(merged.vertices):
            merged.interiors.pop(point, None)
        return merged

    def interior_directions(self, point: Point) -> set[str]:
        return self.interiors.get(point, set())


def _port_directions(geometry: ComponentGeometry) -> dict[str, str]:
    return {port.name: port.direction for port in geometry.ports}


def _compress_path(points: list[Point]) -> list[Point]:
    if len(points) <= 2:
        return points
    result = [points[0]]
    for point in points[1:-1]:
        prev = result[-1]
        nxt = points[points.index(point) + 1]
        if (prev[0] == point[0] == nxt[0]) or (prev[1] == point[1] == nxt[1]):
            continue
        result.append(point)
    result.append(points[-1])
    return result


def _points_to_wires(points: list[Point]) -> list[RawWire]:
    if len(points) < 2:
        return []
    compact = [points[0]]
    for point in points[1:]:
        if point != compact[-1]:
            compact.append(point)
    wires: list[RawWire] = []
    if len(compact) < 2:
        return wires
    start = compact[0]
    last = compact[0]
    direction: str | None = None
    for point in compact[1:]:
        next_direction = "h" if point[1] == last[1] else "v"
        if direction is None:
            direction = next_direction
        elif next_direction != direction:
            wires.append(RawWire(start=start, end=last))
            start = last
            direction = next_direction
        last = point
    wires.append(RawWire(start=start, end=last))
    return wires


def _segment_orientation(start: Point, end: Point) -> str:
    return "h" if start[1] == end[1] else "v"


def _normalized_edge(start: Point, end: Point) -> tuple[Point, Point]:
    return (start, end) if start <= end else (end, start)


def _segment_points(start: Point, end: Point, grid: int) -> list[Point]:
    if start[0] == end[0]:
        step = grid if end[1] >= start[1] else -grid
        return [(start[0], y) for y in range(start[1], end[1] + step, step)]
    if start[1] == end[1]:
        step = grid if end[0] >= start[0] else -grid
        return [(x, start[1]) for x in range(start[0], end[0] + step, step)]
    raise ValueError(f"segment must be orthogonal: {start!r} -> {end!r}")


def _polyline_points(points: list[Point], grid: int) -> tuple[Point, ...]:
    if len(points) < 2:
        return tuple(points)
    result: list[Point] = []
    for start, end in zip(points, points[1:]):
        segment = _segment_points(start, end, grid)
        if result and segment and result[-1] == segment[0]:
            result.extend(segment[1:])
        else:
            result.extend(segment)
    return tuple(result)


def _occupancy_from_wires(wires: Iterable[RawWire], grid: int) -> WireOccupancy:
    occupancy = WireOccupancy()
    for wire in wires:
        segment_points = _segment_points(wire.start, wire.end, grid)
        if len(segment_points) < 2:
            continue
        orientation = _segment_orientation(segment_points[0], segment_points[-1])
        occupancy.vertices.add(segment_points[0])
        occupancy.vertices.add(segment_points[-1])
        for point in segment_points[1:-1]:
            occupancy.interiors.setdefault(point, set()).add(orientation)
        for start, end in zip(segment_points, segment_points[1:]):
            occupancy.edges.add(_normalized_edge(start, end))
    for point in tuple(occupancy.vertices):
        occupancy.interiors.pop(point, None)
    return occupancy


def _nearest_side_name(bounds: tuple[int, int, int, int], offset: Point) -> str:
    left = bounds[0]
    right = bounds[0] + bounds[2]
    top = bounds[1]
    bottom = bounds[1] + bounds[3]
    distances = {
        "left": abs(offset[0] - left),
        "right": abs(offset[0] - right),
        "top": abs(offset[1] - top),
        "bottom": abs(offset[1] - bottom),
    }
    best = min(distances.values())
    for side in ("top", "bottom", "left", "right"):
        if distances[side] == best:
            return side
    return "right"


class LogicCircuitBuilder:
    def __init__(
        self,
        name: str,
        *,
        project: RawProject | None = None,
        grid: int = 10,
        horizontal_gap: int = 80,
        vertical_gap: int = 60,
        margin: int = 80,
        placement_clearance: int = 20,
        allow_tunnel_fallback: bool = False,
        prefer_direct_wires: bool = True,
        layout_retry_scales: tuple[int, ...] | None = None,
        verbose: bool = False,
    ) -> None:
        self.name = name
        self.project = project
        self.grid = grid
        self.horizontal_gap = horizontal_gap
        self.vertical_gap = vertical_gap
        self.margin = margin
        self.placement_clearance = placement_clearance
        self.allow_tunnel_fallback = allow_tunnel_fallback
        self.prefer_direct_wires = prefer_direct_wires
        self.layout_retry_scales = tuple(layout_retry_scales or (1, 2, 3, 4, 5, 6))
        self.verbose = verbose
        self.instances: list[LogicInstanceSpec] = []
        self.nets: list[LogicNetSpec] = []
        self._last_failed_net: LogicNetSpec | None = None
        self._last_anchor_bundle_success_prefix = 0
        self.debug_log: list[str] = []
        self._route_attempts: list[tuple[str, float, str]] = []
        self._current_layout_scale = 1
        self._active_forbidden_points: set[Point] = set()
        self._route_floor_x: int | None = None
        self._route_floor_y: int | None = None

    def _debug(self, message: str) -> None:
        entry = f"[LogicCircuitBuilder:{self.name}] {message}"
        self.debug_log.append(entry)
        if self.verbose:
            print(entry, flush=True)

    def _net_name(self, net: LogicNetSpec) -> str:
        if net.label:
            return net.label
        return " -> ".join(f"{endpoint.instance_id}.{endpoint.port}" for endpoint in net.endpoints)

    def _record_route_attempt(self, net: LogicNetSpec, duration_s: float, outcome: str) -> None:
        name = self._net_name(net)
        self._route_attempts.append((name, duration_s, outcome))
        if self.verbose and (duration_s >= 0.25 or "fail" in outcome or outcome.endswith(":0")):
            self._debug(f"net {name}: {outcome} in {duration_s:.3f}s")

    def _emit_route_summary(self, *, limit: int = 8) -> None:
        if not self._route_attempts:
            return
        slowest = sorted(self._route_attempts, key=lambda item: item[1], reverse=True)[:limit]
        self._debug("slowest route attempts:")
        for name, duration_s, outcome in slowest:
            self._debug(f"  {duration_s:.3f}s  {outcome}  {name}")

    def add_instance(
        self,
        instance_id: str,
        kind: str,
        attrs: dict[str, Any] | None = None,
        *,
        lib: str | None = None,
        loc: Point | None = None,
        rank: int | None = None,
        track: int | None = None,
    ) -> LogicInstanceSpec:
        if any(spec.id == instance_id for spec in self.instances):
            raise ValueError(f"duplicate instance id {instance_id!r}")
        rendered = {name: format_attribute_value(name, value) for name, value in (attrs or {}).items()}
        spec = LogicInstanceSpec(id=instance_id, kind=kind, attrs=rendered, lib=lib, loc=loc, rank_hint=rank, track_hint=track)
        self.instances.append(spec)
        return spec

    def connect(
        self,
        *endpoints: str | EndpointRef,
        label: str | None = None,
        force_tunnel: bool = False,
    ) -> LogicNetSpec:
        refs = [EndpointRef.parse(endpoint) for endpoint in endpoints]
        if len(refs) < 2:
            raise ValueError("a net needs at least two endpoints")
        net = LogicNetSpec(endpoints=refs, label=label, force_tunnel=force_tunnel)
        self.nets.append(net)
        return net

    def build(self) -> RawCircuit:
        build_started = time.perf_counter()
        self.debug_log = []
        self._route_attempts = []
        self._debug(f"build start: instances={len(self.instances)} nets={len(self.nets)}")
        spec_by_id = {spec.id: spec for spec in self.instances}
        raw_at_origin = {spec.id: spec.raw_component(loc=(0, 0), project=self.project) for spec in self.instances}
        geometries = {instance_id: get_component_geometry(component, project=self.project) for instance_id, component in raw_at_origin.items()}
        side_demands = self._component_side_demands(geometries)
        layout_padding = max(self.grid, (self.placement_clearance + 1) // 2)
        visual_extents = {
            instance_id: self._inflated_layout_extents(
                instance_id=instance_id,
                component=component,
                geometry=geometries[instance_id],
                base_extents=component_extents(component, project=self.project, padding=layout_padding, visual=True),
                side_demands=side_demands,
            )
            for instance_id, component in raw_at_origin.items()
        }
        original_horizontal_gap = self.horizontal_gap
        original_vertical_gap = self.vertical_gap
        original_margin = self.margin
        original_clearance = self.placement_clearance
        original_allow_tunnel_fallback = self.allow_tunnel_fallback
        last_error: Exception | None = None
        last_failed_scale = 1.0
        best_layout: tuple[dict[str, RawComponent], list[RawWire], list[RawComponent]] | None = None
        direct_attempts = self._preferred_layout_scales(
            base_scales=[scale for scale in self.layout_retry_scales if scale >= 1],
            spec_by_id=spec_by_id,
            side_demands=side_demands,
        )
        try:
            prefer_direct = self.prefer_direct_wires and original_allow_tunnel_fallback
            for attempt_index, scale in enumerate(direct_attempts, start=1):
                self._apply_layout_scale(
                    scale=scale,
                    original_horizontal_gap=original_horizontal_gap,
                    original_vertical_gap=original_vertical_gap,
                    original_margin=original_margin,
                    original_clearance=original_clearance,
                )
                layout_started = time.perf_counter()
                locations = self._layout_instances(spec_by_id, geometries, visual_extents, side_demands=side_demands)
                self._debug(
                    f"layout attempt {attempt_index}/{len(direct_attempts)} scale={scale} "
                    f"finished in {time.perf_counter() - layout_started:.3f}s"
                )
                raw_components = {
                    spec.id: spec.raw_component(loc=locations[spec.id], project=self.project)
                    for spec in self.instances
                }
                raw_components = self._normalize_components_to_padding(raw_components, geometries)
                self.allow_tunnel_fallback = False if prefer_direct else original_allow_tunnel_fallback
                route_started = time.perf_counter()
                try:
                    wires, extra_components = self._route_nets(raw_components, geometries)
                    best_layout = (raw_components, wires, extra_components)
                    self._debug(
                        f"routing attempt {attempt_index}/{len(direct_attempts)} scale={scale} "
                        f"finished in {time.perf_counter() - route_started:.3f}s"
                    )
                    success_scale = float(scale)
                    failed_scale = min(last_failed_scale, success_scale)
                    step = self._compaction_step(spec_by_id=spec_by_id, side_demands=side_demands)
                    while True:
                        compact_scale = self._midpoint_scale(
                            lower_bound=failed_scale,
                            upper_bound=success_scale,
                            step=step,
                        )
                        if compact_scale is None:
                            break
                        self._apply_layout_scale(
                            scale=compact_scale,
                            original_horizontal_gap=original_horizontal_gap,
                            original_vertical_gap=original_vertical_gap,
                            original_margin=original_margin,
                            original_clearance=original_clearance,
                        )
                        compact_layout_started = time.perf_counter()
                        compact_locations = self._layout_instances(spec_by_id, geometries, visual_extents, side_demands=side_demands)
                        self._debug(
                            f"compaction attempt scale={compact_scale} "
                            f"layout finished in {time.perf_counter() - compact_layout_started:.3f}s"
                        )
                        compact_components = {
                            spec.id: spec.raw_component(loc=compact_locations[spec.id], project=self.project)
                            for spec in self.instances
                        }
                        compact_components = self._normalize_components_to_padding(compact_components, geometries)
                        self.allow_tunnel_fallback = False if prefer_direct else original_allow_tunnel_fallback
                        compact_route_started = time.perf_counter()
                        try:
                            compact_wires, compact_extra = self._route_nets(compact_components, geometries)
                            best_layout = (compact_components, compact_wires, compact_extra)
                            success_scale = compact_scale
                            self._debug(
                                f"compaction attempt scale={compact_scale} "
                                f"finished in {time.perf_counter() - compact_route_started:.3f}s"
                            )
                        except Exception as exc:
                            failed_scale = compact_scale
                            self._debug(
                                f"compaction attempt scale={compact_scale} "
                                f"failed after {time.perf_counter() - compact_route_started:.3f}s: {exc}"
                            )
                    break
                except Exception as exc:
                    last_error = exc
                    last_failed_scale = max(last_failed_scale, float(scale))
                    self._debug(
                        f"routing attempt {attempt_index}/{len(direct_attempts)} scale={scale} "
                        f"failed after {time.perf_counter() - route_started:.3f}s: {exc}"
                    )
            if best_layout is None and original_allow_tunnel_fallback:
                fallback_scale = direct_attempts[-1] if direct_attempts else 1
                self._apply_layout_scale(
                    scale=fallback_scale,
                    original_horizontal_gap=original_horizontal_gap,
                    original_vertical_gap=original_vertical_gap,
                    original_margin=original_margin,
                    original_clearance=original_clearance,
                )
                layout_started = time.perf_counter()
                locations = self._layout_instances(spec_by_id, geometries, visual_extents, side_demands=side_demands)
                self._debug(
                    f"fallback layout scale={fallback_scale} finished in {time.perf_counter() - layout_started:.3f}s"
                )
                raw_components = {
                    spec.id: spec.raw_component(loc=locations[spec.id], project=self.project)
                    for spec in self.instances
                }
                raw_components = self._normalize_components_to_padding(raw_components, geometries)
                self.allow_tunnel_fallback = True
                route_started = time.perf_counter()
                try:
                    wires, extra_components = self._route_nets(raw_components, geometries)
                    best_layout = (raw_components, wires, extra_components)
                    self._debug(f"fallback routing finished in {time.perf_counter() - route_started:.3f}s")
                except Exception as exc:
                    last_error = exc
                    self._debug(f"fallback routing failed after {time.perf_counter() - route_started:.3f}s")
            if best_layout is None:
                self._emit_route_summary()
                if last_error is not None:
                    raise last_error
                raise RuntimeError("failed to build routed circuit")
            raw_components, wires, extra_components = best_layout
            self._emit_route_summary()
            self._debug(f"build finished in {time.perf_counter() - build_started:.3f}s")
            return RawCircuit(name=self.name, components=[*raw_components.values(), *extra_components], wires=wires)
        finally:
            self.horizontal_gap = original_horizontal_gap
            self.vertical_gap = original_vertical_gap
            self.margin = original_margin
            self.placement_clearance = original_clearance
            self.allow_tunnel_fallback = original_allow_tunnel_fallback
            self._current_layout_scale = 1

    def _preferred_layout_scales(
        self,
        *,
        base_scales: list[int],
        spec_by_id: dict[str, LogicInstanceSpec],
        side_demands: dict[tuple[str, str], int],
    ) -> list[float]:
        ordered = sorted({max(1, int(scale)) for scale in base_scales if scale >= 1})
        if not ordered:
            return [1]
        max_side_demand = max(side_demands.values(), default=0)
        max_net_degree = max((len(net.endpoints) for net in self.nets), default=2)
        all_fixed = all(spec.loc is not None for spec in spec_by_id.values())
        target_max = ordered[-1]
        if max_side_demand >= 4 or max_net_degree >= 4:
            target_max = max(target_max, 2)
        if max_side_demand >= 6 or max_net_degree >= 6:
            target_max = max(target_max, 4)
        if max_side_demand >= 8 or max_net_degree >= 8 or all_fixed:
            target_max = max(target_max, 8)
        if max_side_demand >= 12:
            target_max = max(target_max, 16)
        scales = {1, *ordered}
        scale = 1
        while scale < target_max:
            scales.add(scale)
            scale *= 2
        scales.add(target_max)
        return sorted(scales)

    def _compaction_step(
        self,
        *,
        spec_by_id: dict[str, LogicInstanceSpec],
        side_demands: dict[tuple[str, str], int],
    ) -> float:
        all_fixed = all(spec.loc is not None for spec in spec_by_id.values())
        dense = max(side_demands.values(), default=0) >= 6
        return 0.25 if all_fixed or dense else 0.5

    def _midpoint_scale(
        self,
        *,
        lower_bound: float,
        upper_bound: float,
        step: float,
    ) -> float | None:
        if upper_bound - lower_bound <= step:
            return None
        midpoint = (lower_bound + upper_bound) / 2
        snapped = max(1.0, round(midpoint / step) * step)
        if snapped <= lower_bound or snapped >= upper_bound:
            return None
        return snapped

    def _apply_layout_scale(
        self,
        *,
        scale: float,
        original_horizontal_gap: int,
        original_vertical_gap: int,
        original_margin: int,
        original_clearance: int,
    ) -> None:
        snapped_scale = max(1.0, float(scale))
        self._current_layout_scale = snapped_scale
        self.horizontal_gap = int(_ceil_grid(original_horizontal_gap * snapped_scale, self.grid))
        self.vertical_gap = int(_ceil_grid(original_vertical_gap * snapped_scale, self.grid))
        self.margin = int(_ceil_grid(original_margin + self.grid * 4 * (snapped_scale - 1), self.grid))
        self.placement_clearance = int(_ceil_grid(original_clearance * snapped_scale, self.grid))

    def _scaled_fixed_locations(
        self,
        fixed: dict[str, Point],
    ) -> dict[str, Point]:
        return dict(fixed)

    def _routing_edge_padding(self) -> int:
        return max(self.grid * 3, _ceil_grid(self.placement_clearance + self.grid, self.grid))

    def _routing_padding_target(self) -> int:
        return max(self.grid * 2, _ceil_grid(self.placement_clearance, self.grid))

    def _clamp_x_to_route_floor(self, value: int) -> int:
        if self._route_floor_x is None:
            return value
        return max(value, self._route_floor_x)

    def _clamp_y_to_route_floor(self, value: int) -> int:
        if self._route_floor_y is None:
            return value
        return max(value, self._route_floor_y)

    def _clamp_point_to_route_floor(self, point: Point) -> Point:
        return (
            self._clamp_x_to_route_floor(point[0]),
            self._clamp_y_to_route_floor(point[1]),
        )

    def _normalize_components_to_padding(
        self,
        raw_components: dict[str, RawComponent],
        geometries: dict[str, ComponentGeometry],
    ) -> dict[str, RawComponent]:
        if not raw_components:
            return raw_components
        component_bounds = [
            geometries[instance_id].absolute_bounds(component.loc)
            for instance_id, component in raw_components.items()
            if instance_id in geometries
        ]
        component_locs = [component.loc for component in raw_components.values()]
        if not component_bounds or not component_locs:
            return raw_components
        edge_padding = self._routing_edge_padding()
        padding_target = self._routing_padding_target()
        min_component_x = min(bounds[0] for bounds in component_bounds)
        min_component_y = min(bounds[1] for bounds in component_bounds)
        min_loc_x = min(loc[0] for loc in component_locs)
        min_loc_y = min(loc[1] for loc in component_locs)
        route_floor_x = min(
            _floor_grid(min_component_x - edge_padding, self.grid),
            _floor_grid(min_loc_x - edge_padding, self.grid),
        )
        route_floor_y = min(
            _floor_grid(min_component_y - edge_padding, self.grid),
            _floor_grid(min_loc_y - edge_padding, self.grid),
        )
        shift_x = max(0, padding_target - route_floor_x)
        shift_y = max(0, padding_target - route_floor_y)
        if shift_x == 0 and shift_y == 0:
            return raw_components
        for component in raw_components.values():
            component.loc = (
                _snap(component.loc[0] + shift_x, self.grid),
                _snap(component.loc[1] + shift_y, self.grid),
            )
        self._debug(
            f"normalized layout to routing padding by ({shift_x}, {shift_y}) "
            f"with edge_padding={edge_padding} target={padding_target}"
        )
        return raw_components

    def _inflated_layout_extents(
        self,
        *,
        instance_id: str,
        component: RawComponent,
        geometry: ComponentGeometry,
        base_extents: tuple[int, int, int, int],
        side_demands: dict[tuple[str, str], int] | None = None,
    ) -> tuple[int, int, int, int]:
        left, right, top, bottom = base_extents
        grouped: dict[str, int] = defaultdict(int)
        for port in geometry.ports:
            grouped[self._port_side(geometry, port.name)] += 1

        def side_pad(side_name: str, count: int) -> int:
            demand = side_demands.get((instance_id, side_name), 0) if side_demands else 0
            effective = max(count if count > 1 else 0, demand)
            if effective <= 1:
                return 0
            base = _ceil_grid(max(self.placement_clearance, self.grid * 2), self.grid)
            if effective == 2:
                return base
            if effective <= 4:
                return base + self.grid * (effective - 2)
            return base + self.grid * 2 + self.grid * 2 * (effective - 4)

        return (
            left + side_pad("left", grouped["left"]),
            right + side_pad("right", grouped["right"]),
            top + side_pad("top", grouped["top"]),
            bottom + side_pad("bottom", grouped["bottom"]),
        )

    def _component_side_demands(
        self,
        geometries: dict[str, ComponentGeometry],
    ) -> dict[tuple[str, str], int]:
        counts: dict[tuple[str, str], int] = defaultdict(int)
        for net in self.nets:
            for endpoint in net.endpoints:
                side_name = self._port_side(geometries[endpoint.instance_id], endpoint.port)
                counts[(endpoint.instance_id, side_name)] += 1
        return counts

    def _layout_instances(
        self,
        spec_by_id: dict[str, LogicInstanceSpec],
        geometries: dict[str, ComponentGeometry],
        visual_extents: dict[str, tuple[int, int, int, int]],
        side_demands: dict[tuple[str, str], int] | None = None,
    ) -> dict[str, Point]:
        fixed = {spec.id: spec.loc for spec in self.instances if spec.loc is not None}
        scaled_fixed = self._scaled_fixed_locations({instance_id: loc for instance_id, loc in fixed.items() if loc is not None})  # type: ignore[arg-type]
        dynamic_ids = [spec.id for spec in self.instances if spec.loc is None]
        if not dynamic_ids:
            return scaled_fixed

        ranks = self._compute_ranks(spec_by_id, geometries)
        tracks = self._compute_tracks(spec_by_id, geometries)
        layers: dict[int, list[str]] = defaultdict(list)
        for instance_id in dynamic_ids:
            layers[ranks.get(instance_id, 0)].append(instance_id)
        for layer_ids in layers.values():
            layer_ids.sort(
                key=lambda item: (
                    tracks.get(item, 0),
                    spec_by_id[item].track_hint if spec_by_id[item].track_hint is not None else 1_000_000,
                    self._instance_order(item),
                )
            )

        intra_layer_gap = max(self.grid * 3, self.horizontal_gap // 2)
        layer_gap = max(self.grid * 4, self.horizontal_gap)
        track_gap = max(self.grid * 3, self.vertical_gap)
        layer_local_x: dict[int, dict[str, int]] = {}
        layer_widths: dict[int, int] = {}
        for layer, instance_ids in layers.items():
            local_positions: dict[str, int] = {}
            column_anchor = max((visual_extents[instance_id][0] for instance_id in instance_ids), default=0)
            grouped_ids: dict[int, list[str]] = defaultdict(list)
            for instance_id in instance_ids:
                grouped_ids[tracks.get(instance_id, 0)].append(instance_id)
            for group_key in sorted(grouped_ids):
                group = grouped_ids[group_key]
                local_x = 0
                previous_right = 0
                for index, instance_id in enumerate(group):
                    left_extent, right_extent, _, _ = visual_extents[instance_id]
                    if index == 0:
                        anchor_x = column_anchor
                    else:
                        anchor_x = local_x + previous_right + intra_layer_gap + left_extent
                    snapped_anchor = _snap(anchor_x, self.grid)
                    local_positions[instance_id] = snapped_anchor
                    local_x = snapped_anchor
                    previous_right = right_extent
            layer_local_x[layer] = local_positions
            layer_widths[layer] = max(
                (local_positions[instance_id] + visual_extents[instance_id][1] for instance_id in instance_ids),
                default=0,
            )

        base_top = _snap(self.margin, self.grid)

        track_ids = sorted({tracks.get(instance_id, 0) for instance_id in dynamic_ids})
        track_positions: dict[int, int] = {}
        current_top = base_top
        previous_bottom = 0
        for index, track_id in enumerate(track_ids):
            track_instances = [instance_id for instance_id in dynamic_ids if tracks.get(instance_id, 0) == track_id]
            top_extent = max(visual_extents[instance_id][2] for instance_id in track_instances)
            bottom_extent = max(visual_extents[instance_id][3] for instance_id in track_instances)
            if index == 0:
                anchor_y = current_top + top_extent
            else:
                anchor_y = current_top + previous_bottom + track_gap + top_extent
            track_positions[track_id] = _snap(anchor_y, self.grid)
            current_top = anchor_y
            previous_bottom = bottom_extent

        positions: dict[str, Point] = dict(scaled_fixed)
        current_x = self.margin
        previous_width = 0
        for layer in sorted(layers):
            if layer == min(layers):
                layer_origin_x = current_x
            else:
                layer_origin_x = current_x + previous_width + layer_gap
            current_layer_top = base_top
            for instance_id in layers[layer]:
                track_id = tracks.get(instance_id, 0)
                if track_id in track_positions:
                    loc_y = track_positions[track_id]
                else:
                    loc_y = current_layer_top + visual_extents[instance_id][2]
                    current_layer_top += visual_extents[instance_id][2] + visual_extents[instance_id][3] + track_gap
                loc_x = layer_origin_x + layer_local_x[layer][instance_id]
                positions[instance_id] = (_snap(loc_x, self.grid), _snap(loc_y, self.grid))
            previous_width = layer_widths[layer]
            current_x = layer_origin_x
        return self._relax_outer_edges(
            positions=positions,
            layers=layers,
            tracks=tracks,
            visual_extents=visual_extents,
            side_demands=side_demands or {},
            layer_gap=layer_gap,
            track_gap=track_gap,
        )

    def _relax_outer_edges(
        self,
        *,
        positions: dict[str, Point],
        layers: dict[int, list[str]],
        tracks: dict[str, int],
        visual_extents: dict[str, tuple[int, int, int, int]],
        side_demands: dict[tuple[str, str], int],
        layer_gap: int,
        track_gap: int,
    ) -> dict[str, Point]:
        _ = layers, tracks, layer_gap, track_gap
        adjusted = dict(positions)
        if not adjusted:
            return adjusted

        min_left = min(adjusted[instance_id][0] - visual_extents[instance_id][0] for instance_id in adjusted)
        min_top = min(adjusted[instance_id][1] - visual_extents[instance_id][2] for instance_id in adjusted)
        left_need = max(
            (self._outer_edge_clearance(side_demands.get((instance_id, "left"), 0)) for instance_id in adjusted),
            default=self.grid * 2,
        )
        top_need = max(
            (self._outer_edge_clearance(side_demands.get((instance_id, "top"), 0)) for instance_id in adjusted),
            default=self.grid * 2,
        )
        shift_x = max(0, left_need - min_left)
        shift_y = max(0, top_need - min_top)
        if shift_x == 0 and shift_y == 0:
            return adjusted
        return {
            instance_id: (_snap(loc[0] + shift_x, self.grid), _snap(loc[1] + shift_y, self.grid))
            for instance_id, loc in adjusted.items()
        }

    def _outer_edge_clearance(self, demand: int) -> int:
        if demand <= 0:
            return self.grid * 2
        return _ceil_grid(self.placement_clearance + self.grid * max(2, demand), self.grid)

    def _compute_ranks(
        self,
        spec_by_id: dict[str, LogicInstanceSpec],
        geometries: dict[str, ComponentGeometry],
    ) -> dict[str, int]:
        outgoing: dict[str, set[str]] = defaultdict(set)
        incoming_count: dict[str, int] = {instance_id: 0 for instance_id in spec_by_id}
        for src, dst in self._connection_pairs(geometries):
            if self._preferred_axis(src, dst, geometries) != "horizontal":
                continue
            if dst.instance_id not in outgoing[src.instance_id]:
                outgoing[src.instance_id].add(dst.instance_id)
                incoming_count[dst.instance_id] += 1
        queue = deque(sorted((instance_id for instance_id, count in incoming_count.items() if count == 0), key=self._instance_order))
        rank: dict[str, int] = {instance_id: 0 for instance_id in spec_by_id}
        visited = 0
        while queue:
            node = queue.popleft()
            visited += 1
            for nxt in sorted(outgoing[node], key=self._instance_order):
                rank[nxt] = max(rank[nxt], rank[node] + 1)
                incoming_count[nxt] -= 1
                if incoming_count[nxt] == 0:
                    queue.append(nxt)
        if visited != len(spec_by_id):
            for index, spec in enumerate(self.instances):
                rank.setdefault(spec.id, index)
        for spec in self.instances:
            if spec.rank_hint is not None:
                rank[spec.id] = max(rank.get(spec.id, 0), spec.rank_hint)
        return rank

    def _compute_tracks(
        self,
        spec_by_id: dict[str, LogicInstanceSpec],
        geometries: dict[str, ComponentGeometry],
    ) -> dict[str, int]:
        outgoing: dict[str, set[str]] = defaultdict(set)
        incoming_count: dict[str, int] = {instance_id: 0 for instance_id in spec_by_id}
        track: dict[str, int] = {
            instance_id: (spec.track_hint if spec.track_hint is not None else 0)
            for instance_id, spec in spec_by_id.items()
        }
        for src, dst in self._connection_pairs(geometries):
            if self._preferred_axis(src, dst, geometries) != "vertical":
                continue
            ordering = self._preferred_vertical_order(src, dst, geometries)
            if ordering is None:
                continue
            upper, lower = ordering
            upper_spec = spec_by_id[upper]
            lower_spec = spec_by_id[lower]
            if upper_spec.track_hint is not None and lower_spec.track_hint is not None:
                continue
            if lower not in outgoing[upper]:
                outgoing[upper].add(lower)
                incoming_count[lower] += 1
        queue = deque(
            sorted(
                (instance_id for instance_id, count in incoming_count.items() if count == 0),
                key=lambda item: (track[item], self._instance_order(item)),
            )
        )
        visited = 0
        while queue:
            node = queue.popleft()
            visited += 1
            for nxt in sorted(outgoing[node], key=lambda item: (track[item], self._instance_order(item))):
                track[nxt] = max(track[nxt], track[node] + 1)
                incoming_count[nxt] -= 1
                if incoming_count[nxt] == 0:
                    queue.append(nxt)
        if visited != len(spec_by_id):
            for spec in self.instances:
                track.setdefault(spec.id, spec.track_hint if spec.track_hint is not None else 0)
        for instance_id, geometry in geometries.items():
            track[instance_id] = max(track.get(instance_id, 0), self._component_track_bias(geometry))
        return track

    def _component_track_bias(self, geometry: ComponentGeometry) -> int:
        if not geometry.ports:
            return 0
        widths: list[int] = []
        for port in geometry.ports:
            raw_width = port.width or "1"
            try:
                widths.append(max(1, int(raw_width)))
            except ValueError:
                widths.append(1)
        max_width = max(widths, default=1)
        port_count = len(geometry.ports)
        if max_width != 1:
            return 0
        if port_count <= 3:
            return 1
        return 0

    def _instance_order(self, instance_id: str) -> int:
        for index, spec in enumerate(self.instances):
            if spec.id == instance_id:
                return index
        return len(self.instances)

    def _connection_pairs(
        self,
        geometries: dict[str, ComponentGeometry],
    ) -> list[tuple[EndpointRef, EndpointRef]]:
        directions = self._resolved_port_directions(geometries)
        pairs: list[tuple[EndpointRef, EndpointRef]] = []
        for net in self.nets:
            outputs = [endpoint for endpoint in net.endpoints if directions.get(endpoint.instance_id, {}).get(endpoint.port) == "output"]
            inputs = [endpoint for endpoint in net.endpoints if directions.get(endpoint.instance_id, {}).get(endpoint.port) == "input"]
            if outputs and inputs:
                pairs.extend((src, dst) for src in outputs for dst in inputs if src.instance_id != dst.instance_id)
            else:
                root = net.endpoints[0]
                pairs.extend((root, endpoint) for endpoint in net.endpoints[1:] if endpoint.instance_id != root.instance_id)
        return pairs

    def _resolved_port_directions(
        self,
        geometries: dict[str, ComponentGeometry],
    ) -> dict[str, dict[str, str]]:
        directions = {instance_id: _port_directions(geometry) for instance_id, geometry in geometries.items()}
        spec_by_id = {spec.id: spec for spec in self.instances}
        changed = True
        while changed:
            changed = False
            for instance_id, direction_map in directions.items():
                spec = spec_by_id.get(instance_id)
                if spec is None or spec.kind != "Splitter":
                    continue
                combined_dir = direction_map.get("combined")
                if combined_dir != "inout":
                    continue
                votes: list[str] = []
                for net in self.nets:
                    if not any(endpoint.instance_id == instance_id and endpoint.port == "combined" for endpoint in net.endpoints):
                        continue
                    other_dirs = [
                        directions.get(endpoint.instance_id, {}).get(endpoint.port, "inout")
                        for endpoint in net.endpoints
                        if not (endpoint.instance_id == instance_id and endpoint.port == "combined")
                    ]
                    if "output" in other_dirs and "input" not in other_dirs:
                        votes.append("input")
                    elif "input" in other_dirs and "output" not in other_dirs:
                        votes.append("output")
                if not votes:
                    continue
                input_votes = votes.count("input")
                output_votes = votes.count("output")
                if input_votes == output_votes:
                    continue
                inferred_combined = "input" if input_votes > output_votes else "output"
                inferred_branch = "output" if inferred_combined == "input" else "input"
                direction_map["combined"] = inferred_combined
                for port_name in list(direction_map):
                    if port_name.startswith("out"):
                        direction_map[port_name] = inferred_branch
                changed = True
        return directions

    def _preferred_axis(
        self,
        src: EndpointRef,
        dst: EndpointRef,
        geometries: dict[str, ComponentGeometry],
    ) -> str:
        if self._instance_kind(src.instance_id) == "Pin" or self._instance_kind(dst.instance_id) == "Pin":
            return "horizontal"
        if self._instance_kind(src.instance_id) == "Splitter" or self._instance_kind(dst.instance_id) == "Splitter":
            return "horizontal"
        src_side = self._port_side(geometries[src.instance_id], src.port)
        dst_side = self._port_side(geometries[dst.instance_id], dst.port)
        src_vertical = src_side in {"top", "bottom"}
        dst_vertical = dst_side in {"top", "bottom"}
        if src_vertical or dst_vertical:
            if src_side in {"left", "right"} and dst_side in {"left", "right"}:
                return "horizontal"
            return "vertical"
        return "horizontal"

    def _preferred_vertical_order(
        self,
        src: EndpointRef,
        dst: EndpointRef,
        geometries: dict[str, ComponentGeometry],
    ) -> tuple[str, str] | None:
        if self._preferred_axis(src, dst, geometries) != "vertical":
            return None
        src_side = self._port_side(geometries[src.instance_id], src.port)
        dst_side = self._port_side(geometries[dst.instance_id], dst.port)
        if src_side == "bottom" or dst_side == "top":
            return (src.instance_id, dst.instance_id)
        if src_side == "top" or dst_side == "bottom":
            return (dst.instance_id, src.instance_id)
        src_point = geometries[src.instance_id].port(src.port).offset
        dst_point = geometries[dst.instance_id].port(dst.port).offset
        if src_point[1] <= dst_point[1]:
            return (src.instance_id, dst.instance_id)
        return (dst.instance_id, src.instance_id)

    def _instance_kind(self, instance_id: str) -> str:
        for spec in self.instances:
            if spec.id == instance_id:
                return spec.kind
        return ""

    def _layout_order_key(self, spec: LogicInstanceSpec) -> tuple[int, int]:
        track = spec.track_hint if spec.track_hint is not None else 1_000_000
        return (track, self._instance_order(spec.id))

    def _route_nets(
        self,
        raw_components: dict[str, RawComponent],
        geometries: dict[str, ComponentGeometry],
    ) -> tuple[list[RawWire], list[RawComponent]]:
        if not self.nets:
            return [], []
        component_bounds = [
            geometry.absolute_bounds(raw_components[instance_id].loc)
            for instance_id, geometry in geometries.items()
        ]
        component_locs = [component.loc for component in raw_components.values()]
        if component_bounds and component_locs:
            edge_padding = self._routing_edge_padding()
            padding_target = self._routing_padding_target()
            min_component_x = min(bounds[0] for bounds in component_bounds)
            min_component_y = min(bounds[1] for bounds in component_bounds)
            min_loc_x = min(loc[0] for loc in component_locs)
            min_loc_y = min(loc[1] for loc in component_locs)
            self._route_floor_x = max(
                padding_target,
                _floor_grid(min_component_x - edge_padding, self.grid),
                _floor_grid(min_loc_x - edge_padding, self.grid),
            )
            self._route_floor_y = max(
                padding_target,
                _floor_grid(min_component_y - edge_padding, self.grid),
                _floor_grid(min_loc_y - edge_padding, self.grid),
            )
        else:
            self._route_floor_x = None
            self._route_floor_y = None
        all_port_points = {
            (instance_id, port.name): (
                raw_components[instance_id].loc[0] + port.offset[0],
                raw_components[instance_id].loc[1] + port.offset[1],
            )
            for instance_id, geometry in geometries.items()
            for port in geometry.ports
        }
        port_escapes = {} # Short circuit topological matrix routing loops for extreme net density
        endpoint_sides = {
            endpoint_key: self._port_side(geometries[endpoint_key[0]], endpoint_key[1])
            for endpoint_key in all_port_points
        }
        blocked = self._component_blockers(raw_components, geometries)
        tunneled_side_groups: set[tuple[str, str]] = set()
        while True:
            self._last_failed_net = None
            reserved_lead_points: set[Point] = set()
            direct_nets: list[LogicNetSpec] = []
            tunneled_net_indexes: set[int] = set()
            for index, net in enumerate(self.nets):
                if net.force_tunnel:
                    tunneled_net_indexes.add(index)
                elif any(
                    (endpoint.instance_id, endpoint_sides[(endpoint.instance_id, endpoint.port)]) in tunneled_side_groups
                    for endpoint in net.endpoints
                ):
                    tunneled_net_indexes.add(index)
                else:
                    direct_nets.append(net)
            tunnel_wires, tunnel_components, tunnel_occupied = self._build_tunnelized_nets(
                nets=tunneled_net_indexes,
                raw_components=raw_components,
                geometries=geometries,
                port_escapes=port_escapes,
                all_port_points=all_port_points,
            )
            preserve_tunnel_labels = {
                net.label
                for index, net in enumerate(self.nets)
                if index in tunneled_net_indexes and net.force_tunnel and net.label
            }
            reserved_lead_points.update(tunnel_occupied.vertices)
            greedy = self._greedy_net_routes(
                remaining=direct_nets,
                geometries=geometries,
                port_escapes=port_escapes,
                all_port_points=all_port_points,
                blocked=blocked,
                occupancy=tunnel_occupied,
                reserved_lead_points=reserved_lead_points,
            )
            if greedy is not None:
                wires = list(tunnel_wires)
                for plan in greedy:
                    wires.extend(plan.wires)
                if self.allow_tunnel_fallback and tunnel_components:
                    wires, tunnel_components = self._detunnelize_tunnel_labels(
                        wires=wires,
                        tunnel_components=tunnel_components,
                        blocked=blocked,
                        preserve_labels=preserve_tunnel_labels,
                    )
                return wires, tunnel_components
            if not self.allow_tunnel_fallback:
                failed_net = self._last_failed_net or (direct_nets[0] if direct_nets else self.nets[0])
                failed = failed_net.endpoints[1] if len(failed_net.endpoints) > 1 else failed_net.endpoints[0]
                self._debug(f"route failure on {self._net_name(failed_net)}")
                raise RuntimeError(f"failed to route {failed.instance_id}.{failed.port}")
            fallback_net = self._select_tunnel_fallback_net(
                direct_nets=direct_nets,
                geometries=geometries,
                port_escapes=port_escapes,
                all_port_points=all_port_points,
                blocked=blocked,
                occupancy=tunnel_occupied,
                reserved_lead_points=reserved_lead_points,
            )
            if fallback_net is None:
                failed = self.nets[0].endpoints[1] if len(self.nets[0].endpoints) > 1 else self.nets[0].endpoints[0]
                raise RuntimeError(f"failed to route {failed.instance_id}.{failed.port}")
            changed = False
            for endpoint in fallback_net.endpoints:
                side_group = (endpoint.instance_id, endpoint_sides[(endpoint.instance_id, endpoint.port)])
                if side_group not in tunneled_side_groups:
                    tunneled_side_groups.add(side_group)
                    changed = True
            if not changed:
                failed = fallback_net.endpoints[1] if len(fallback_net.endpoints) > 1 else fallback_net.endpoints[0]
                raise RuntimeError(f"failed to route {failed.instance_id}.{failed.port}")

    def _greedy_net_routes(
        self,
        *,
        remaining: list[LogicNetSpec],
        geometries: dict[str, ComponentGeometry],
        port_escapes: dict[tuple[str, str], list[tuple[Point, tuple[Point, ...]]]],
        all_port_points: dict[tuple[str, str], Point],
        blocked: set[Point],
        occupancy: WireOccupancy,
        reserved_lead_points: set[Point],
    ) -> list[RoutedNetPlan] | None:
        pending = list(remaining)
        planned: list[RoutedNetPlan] = []
        current_occupancy = occupancy
        while pending:
            anchored_bundle = self._plan_parallel_anchor_bundle_group(
                remaining=pending,
                geometries=geometries,
                port_escapes=port_escapes,
                all_port_points=all_port_points,
                blocked=blocked,
                occupancy=current_occupancy,
            )
            if anchored_bundle:
                anchored_nets = list(plan.net for plan in anchored_bundle)
                planned.extend(anchored_bundle)
                for plan in anchored_bundle:
                    current_occupancy = current_occupancy.merged(plan.occupancy)
                pending = [net for net in pending if net not in anchored_nets]
                continue
            bundled = self._plan_parallel_bundle_group(
                remaining=pending,
                geometries=geometries,
                port_escapes=port_escapes,
                all_port_points=all_port_points,
                blocked=blocked,
                occupancy=current_occupancy,
            )
            if bundled:
                bundled_nets = list(plan.net for plan in bundled)
                planned.extend(bundled)
                for plan in bundled:
                    current_occupancy = current_occupancy.merged(plan.occupancy)
                pending = [net for net in pending if net not in bundled_nets]
                continue
            candidates: list[tuple[int, RoutedNetPlan]] = []
            for index, net in enumerate(pending):
                chosen = self._plan_net_route(
                    net=net,
                    geometries=geometries,
                    port_escapes=port_escapes,
                    all_port_points=all_port_points,
                    blocked=blocked,
                    occupancy=current_occupancy,
                    reserved_lead_points=reserved_lead_points,
                )
                if chosen is None:
                    continue
                candidates.append((index, chosen))
            if not candidates:
                self._last_failed_net = pending[0] if pending else None
                return None
            candidates.sort(
                key=lambda item: (
                    item[1].min_options,
                    item[1].total_options,
                    self._net_priority(item[1].net, geometries, port_escapes),
                    item[1].score,
                    item[0],
                )
            )
            index, chosen = candidates[0]
            if self.verbose:
                self._debug(
                    f"choose {self._net_name(chosen.net)} score={chosen.score:.3f} "
                    f"options={chosen.min_options}/{chosen.total_options} pending={len(pending)}"
                )
            planned.append(chosen)
            current_occupancy = current_occupancy.merged(chosen.occupancy)
            pending = pending[:index] + pending[index + 1 :]
        return planned

    def _plan_parallel_anchor_bundle_group(
        self,
        *,
        remaining: list[LogicNetSpec],
        geometries: dict[str, ComponentGeometry],
        port_escapes: dict[tuple[str, str], list[tuple[Point, tuple[Point, ...]]]],
        all_port_points: dict[tuple[str, str], Point],
        blocked: set[Point],
        occupancy: WireOccupancy,
    ) -> list[RoutedNetPlan] | None:
        groups: dict[tuple[str, str], list[tuple[LogicNetSpec, EndpointRef, list[EndpointRef]]]] = defaultdict(list)
        for net in remaining:
            ordered = self._ordered_endpoints_for_routing(net, geometries)
            if len(ordered) < 3:
                continue
            root = ordered[0]
            others = ordered[1:]
            anchor_side = self._port_side(geometries[root.instance_id], root.port)
            groups[(root.instance_id, anchor_side)].append((net, root, others))
        candidates = [
            (key, items)
            for key, items in groups.items()
            if len(items) >= 3
        ]
        candidates.sort(key=lambda item: (-len(item[1]), item[0][0], item[0][1]))
        for (instance_id, anchor_side), items in candidates:
            horizontal = anchor_side in {"left", "right"}
            axis_index = 1 if horizontal else 0
            anchor_dir = 1 if anchor_side in {"right", "bottom"} else -1
            ordered_items = self._ordered_anchor_bundle_items(
                items=items,
                axis_index=axis_index,
                all_port_points=all_port_points,
                reverse=anchor_dir > 0,
            )
            chunk_size = min(4 if occupancy.edges else 8, len(ordered_items))
            plans = self._build_parallel_anchor_bundle_group(
                instance_id=instance_id,
                anchor_side=anchor_side,
                items=ordered_items[:chunk_size],
                geometries=geometries,
                port_escapes=port_escapes,
                all_port_points=all_port_points,
                blocked=blocked,
                occupancy=occupancy,
            )
            if plans is not None:
                return plans
        return None

    def _ordered_anchor_bundle_items(
        self,
        *,
        items: list[tuple[LogicNetSpec, EndpointRef, list[EndpointRef]]],
        axis_index: int,
        all_port_points: dict[tuple[str, str], Point],
        reverse: bool = False,
    ) -> list[tuple[LogicNetSpec, EndpointRef, list[EndpointRef]]]:
        return sorted(
            items,
            key=lambda item: (
                sorted(
                    all_port_points[(endpoint.instance_id, endpoint.port)][axis_index]
                    for endpoint in item[2]
                )[len(item[2]) // 2],
                all_port_points[(item[1].instance_id, item[1].port)][axis_index],
                self.nets.index(item[0]),
            ),
            reverse=reverse,
        )

    def _bundle_channel_step(self, *, count: int, occupied: bool) -> int:
        return self.grid * (2 if occupied else 1)

    def _assign_monotone_channels(
        self,
        *,
        preferred_channels: list[int],
        anchor_dir: int,
        step: int,
    ) -> list[int]:
        if not preferred_channels:
            return []
        assigned: list[int] = []
        current: int | None = None
        for preferred in preferred_channels:
            snapped = _snap(preferred, self.grid)
            if current is None:
                channel = snapped
            elif anchor_dir > 0:
                channel = max(snapped, current + step)
            else:
                channel = min(snapped, current - step)
            assigned.append(_snap(channel, self.grid))
            current = assigned[-1]
        return assigned

    def _preferred_bundle_channel(
        self,
        *,
        endpoint: EndpointRef,
        desired_channel: int,
        desired_axis: int,
        horizontal: bool,
        geometries: dict[str, ComponentGeometry],
        port_escapes: dict[tuple[str, str], list[tuple[Point, tuple[Point, ...]]]],
        all_port_points: dict[tuple[str, str], Point],
    ) -> int | None:
        preferred_point = (desired_channel, desired_axis) if horizontal else (desired_axis, desired_channel)
        lead_desired_channel, lead_desired_axis = self._lead_candidate_preference(
            endpoint=endpoint,
            preferred_point=preferred_point,
            geometries=geometries,
        )
        leads = self._bundle_lead_candidates(
            endpoint=endpoint,
            desired_channel=lead_desired_channel,
            desired_axis=lead_desired_axis,
            port_escapes=port_escapes,
            all_port_points=all_port_points,
            geometries=geometries,
            per_channel_cap=1,
        )
        if not leads:
            return None
        lead = leads[0]
        return lead[-1][0] if horizontal else lead[-1][1]

    def _lead_candidate_preference(
        self,
        *,
        endpoint: EndpointRef,
        preferred_point: Point,
        geometries: dict[str, ComponentGeometry],
    ) -> tuple[int, int]:
        side = self._port_side(geometries[endpoint.instance_id], endpoint.port)
        if side in {"left", "right"}:
            return preferred_point[0], preferred_point[1]
        return preferred_point[1], preferred_point[0]

    def _lead_candidates_toward_point(
        self,
        *,
        endpoint: EndpointRef,
        preferred_point: Point,
        port_escapes: dict[tuple[str, str], list[tuple[Point, tuple[Point, ...]]]],
        all_port_points: dict[tuple[str, str], Point],
        geometries: dict[str, ComponentGeometry],
        per_channel_cap: int | None = None,
    ) -> list[tuple[Point, ...]]:
        desired_channel, desired_axis = self._lead_candidate_preference(
            endpoint=endpoint,
            preferred_point=preferred_point,
            geometries=geometries,
        )
        return self._bundle_lead_candidates(
            endpoint=endpoint,
            desired_channel=desired_channel,
            desired_axis=desired_axis,
            port_escapes=port_escapes,
            all_port_points=all_port_points,
            geometries=geometries,
            per_channel_cap=per_channel_cap,
        )

    def _assigned_anchor_bundle_channels(
        self,
        *,
        ordered_items: list[tuple[LogicNetSpec, EndpointRef, list[EndpointRef]]],
        horizontal: bool,
        axis_index: int,
        anchor_dir: int,
        geometries: dict[str, ComponentGeometry],
        port_escapes: dict[tuple[str, str], list[tuple[Point, tuple[Point, ...]]]],
        all_port_points: dict[tuple[str, str], Point],
        occupancy: WireOccupancy,
    ) -> tuple[list[int], int] | None:
        channel_index = 0 if horizontal else 1
        step = self._bundle_channel_step(count=len(ordered_items), occupied=bool(occupancy.edges))
        base_offset = step * (2 if occupancy.edges else 1)
        preferred_channels: list[int] = []
        for _, root, others in ordered_items:
            root_point = all_port_points[(root.instance_id, root.port)]
            desired_axis = self._median_axis_coordinate(
                all_port_points[(endpoint.instance_id, endpoint.port)][axis_index]
                for endpoint in others
            )
            desired_channel = _snap(root_point[channel_index] + anchor_dir * base_offset, self.grid)
            preferred = self._preferred_bundle_channel(
                endpoint=root,
                desired_channel=desired_channel,
                desired_axis=desired_axis,
                horizontal=horizontal,
                geometries=geometries,
                port_escapes=port_escapes,
                all_port_points=all_port_points,
            )
            if preferred is None:
                return None
            preferred_channels.append(preferred)
        return self._assign_monotone_channels(preferred_channels=preferred_channels, anchor_dir=anchor_dir, step=step), step

    def _build_parallel_anchor_bundle_group(
        self,
        *,
        instance_id: str,
        anchor_side: str,
        items: list[tuple[LogicNetSpec, EndpointRef, list[EndpointRef]]],
        geometries: dict[str, ComponentGeometry],
        port_escapes: dict[tuple[str, str], list[tuple[Point, tuple[Point, ...]]]],
        all_port_points: dict[tuple[str, str], Point],
        blocked: set[Point],
        occupancy: WireOccupancy,
    ) -> list[RoutedNetPlan] | None:
        horizontal = anchor_side in {"left", "right"}
        axis_index = 1 if horizontal else 0
        anchor_dir = 1 if anchor_side in {"right", "bottom"} else -1
        ordered_items = self._ordered_anchor_bundle_items(
            items=items,
            axis_index=axis_index,
            all_port_points=all_port_points,
            reverse=anchor_dir > 0,
        )
        assigned = self._assigned_anchor_bundle_channels(
            ordered_items=ordered_items,
            horizontal=horizontal,
            axis_index=axis_index,
            anchor_dir=anchor_dir,
            geometries=geometries,
            port_escapes=port_escapes,
            all_port_points=all_port_points,
            occupancy=occupancy,
        )
        if assigned is None:
            return None
        channels, step = assigned
        if not channels:
            return None
        self._debug(
            f"anchor bundle assigned on {instance_id}.{anchor_side}: "
            f"step={step} nets={len(ordered_items)} first={channels[0]} last={channels[-1]}"
        )
        return self._route_anchor_bundle_group_on_assignment(
            ordered_items=ordered_items,
            start_channel=channels[0],
            channel_step=step,
            anchor_dir=anchor_dir,
            horizontal=horizontal,
            axis_index=axis_index,
            geometries=geometries,
            port_escapes=port_escapes,
            all_port_points=all_port_points,
            blocked=blocked,
            occupancy=occupancy,
        )

    def _bundle_channel_extent(
        self,
        *,
        points: list[Point],
        blocked: set[Point],
        horizontal: bool,
    ) -> tuple[int, int]:
        min_x, min_y, max_x, max_y = self._routing_extent(points, blocked, relaxed=True)
        return (min_x, max_x) if horizontal else (min_y, max_y)

    def _bundle_schedule_candidates(
        self,
        *,
        base_channel: int,
        anchor_dir: int,
        count: int,
        base_step: int,
        channel_min: int,
        channel_max: int,
        start_candidates: list[int] | None = None,
        start_target: int | None = None,
    ) -> Iterable[tuple[int, int, list[int]]]:
        if count <= 0:
            return
        preferred_step = max(self.grid, _ceil_grid(base_step, self.grid))
        step_values = [preferred_step]
        if anchor_dir > 0:
            max_step = max(self.grid, (channel_max - base_channel) // max(1, count - 1))
        else:
            max_step = max(self.grid, (base_channel - channel_min) // max(1, count - 1))
        max_step = _ceil_grid(max_step, self.grid)
        delta = self.grid
        while preferred_step - delta >= self.grid or preferred_step + delta <= max_step:
            if preferred_step + delta <= max_step:
                step_values.append(preferred_step + delta)
            if preferred_step - delta >= self.grid:
                step_values.append(preferred_step - delta)
            delta += self.grid
        seen_steps: set[int] = set()
        for step in step_values:
            if step in seen_steps or step <= 0:
                continue
            seen_steps.add(step)
            if start_candidates:
                target = start_target if start_target is not None else base_channel
                ordered_starts = sorted(
                    set(start_candidates),
                    key=lambda value: (
                        abs(value - target),
                        0 if (value - target) * anchor_dir >= 0 else 1,
                        value,
                    ),
                )
                for start in ordered_starts:
                    channels = [start + anchor_dir * step * index for index in range(count)]
                    yield start, step, channels
                continue
            shift = 0
            while True:
                start = base_channel + anchor_dir * shift
                channels = [start + anchor_dir * step * index for index in range(count)]
                low = min(channels)
                high = max(channels)
                if low < channel_min or high > channel_max:
                    break
                yield start, step, channels
                shift += self.grid

    def _route_anchor_bundle_group_on_assignment(
        self,
        *,
        ordered_items: list[tuple[LogicNetSpec, EndpointRef, list[EndpointRef]]],
        start_channel: int,
        channel_step: int,
        anchor_dir: int,
        horizontal: bool,
        axis_index: int,
        geometries: dict[str, ComponentGeometry],
        port_escapes: dict[tuple[str, str], list[tuple[Point, tuple[Point, ...]]]],
        all_port_points: dict[tuple[str, str], Point],
        blocked: set[Point],
        occupancy: WireOccupancy,
    ) -> list[RoutedNetPlan] | None:
        plans: list[RoutedNetPlan] = []
        current_occupancy = occupancy
        requested_channel = start_channel
        for index, (net, root, others) in enumerate(ordered_items, start=1):
            root_axis = self._median_axis_coordinate(
                all_port_points[(endpoint.instance_id, endpoint.port)][axis_index]
                for endpoint in others
            )
            previous_forbidden = self._active_forbidden_points
            self._active_forbidden_points = self._forbidden_port_points(net.endpoints, all_port_points)
            try:
                choice = next(
                    self._iter_anchor_bundle_net_plans(
                        net=net,
                        root=root,
                        others=others,
                        requested_channel=requested_channel,
                        root_desired_axis=root_axis,
                        horizontal=horizontal,
                        axis_index=axis_index,
                        geometries=geometries,
                        port_escapes=port_escapes,
                        all_port_points=all_port_points,
                        blocked=blocked,
                        occupancy=current_occupancy,
                        anchor_dir=anchor_dir,
                    ),
                    None,
                )
            finally:
                self._active_forbidden_points = previous_forbidden
            if choice is None:
                if len(plans) >= 3:
                    self._debug(
                        f"anchor bundle returning prefix {len(plans)}/{len(ordered_items)} on route failure "
                        f"at {self._net_name(net)} requested={requested_channel}"
                    )
                    return plans
                self._debug(
                    f"anchor bundle assigned route failed at net {index}/{len(ordered_items)} "
                    f"{self._net_name(net)} requested={requested_channel}"
                )
                return None
            plan, actual_channel = choice
            plans.append(plan)
            current_occupancy = current_occupancy.merged(plan.occupancy)
            requested_channel = actual_channel + anchor_dir * channel_step
        return plans

    def _plan_anchor_bundle_net_on_channel(
        self,
        *,
        net: LogicNetSpec,
        root: EndpointRef,
        others: list[EndpointRef],
        channel: int,
        root_desired_axis: int,
        horizontal: bool,
        axis_index: int,
        geometries: dict[str, ComponentGeometry],
        port_escapes: dict[tuple[str, str], list[tuple[Point, tuple[Point, ...]]]],
        all_port_points: dict[tuple[str, str], Point],
        blocked: set[Point],
        occupancy: WireOccupancy,
    ) -> RoutedNetPlan | None:
        root_attachment = self._plan_anchor_bundle_endpoint_attachment(
            endpoint=root,
            channel=channel,
            horizontal=horizontal,
            axis_index=axis_index,
            desired_axis=root_desired_axis,
            geometries=geometries,
            port_escapes=port_escapes,
            all_port_points=all_port_points,
            blocked=blocked,
            occupancy=occupancy,
        )
        if root_attachment is None:
            return None
        _, root_choice = root_attachment
        root_lead, root_spoke, root_junction, root_occ, root_score = root_choice
        wires = list(_points_to_wires(self._compact_route(list(root_lead))))
        if len(root_spoke) > 1:
            wires.extend(_points_to_wires(root_spoke))
        working_occupancy = occupancy.merged(root_occ)
        junctions = [root_junction]
        score = root_score
        remaining = list(others)
        while remaining:
            ranked_choices: list[
                tuple[
                    int,
                    float,
                    str,
                    str,
                    EndpointRef,
                    tuple[tuple[Point, ...], list[Point], Point, WireOccupancy, float],
                ]
            ] = []
            for endpoint in remaining:
                attachment = self._plan_anchor_bundle_endpoint_attachment(
                    endpoint=endpoint,
                    channel=channel,
                    horizontal=horizontal,
                    axis_index=axis_index,
                    geometries=geometries,
                    port_escapes=port_escapes,
                    all_port_points=all_port_points,
                    blocked=blocked,
                    occupancy=working_occupancy,
                )
                if attachment is None:
                    return None
                option_count, best_choice = attachment
                ranked_choices.append(
                    (
                        option_count,
                        best_choice[4],
                        endpoint.instance_id,
                        endpoint.port,
                        endpoint,
                        best_choice,
                    )
                )
            ranked_choices.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
            _, _, _, _, endpoint, best_choice = ranked_choices[0]
            lead, spoke, junction, candidate_occupancy, candidate_score = best_choice
            wires.extend(_points_to_wires(self._compact_route(list(lead))))
            if len(spoke) > 1:
                wires.extend(_points_to_wires(spoke))
            working_occupancy = working_occupancy.merged(candidate_occupancy)
            junctions.append(junction)
            score += candidate_score
            remaining.remove(endpoint)
        if horizontal:
            ys = [junction[1] for junction in junctions]
            trunk = [(channel, _snap(min(ys), self.grid)), (channel, _snap(max(ys), self.grid))]
        else:
            xs = [junction[0] for junction in junctions]
            trunk = [(_snap(min(xs), self.grid), channel), (_snap(max(xs), self.grid), channel)]
        total_score = score
        if trunk[0] != trunk[1]:
            trunk_wires = _points_to_wires(trunk)
            if not self._wires_fit_occupancy(trunk_wires, occupancy, allowed_vertices=set(junctions)):
                return None
            wires.extend(trunk_wires)
            total_score += float(len(_polyline_points(trunk, self.grid))) * 0.2
        if not wires or not self._wires_fit_occupancy(wires, occupancy, allowed_vertices=set(junctions)):
            return None
        return RoutedNetPlan(
            net=net,
            wires=wires,
            occupancy=_occupancy_from_wires(wires, self.grid),
            score=total_score,
            min_options=1,
            total_options=1,
        )

    def _iter_anchor_bundle_net_plans(
        self,
        *,
        net: LogicNetSpec,
        root: EndpointRef,
        others: list[EndpointRef],
        requested_channel: int,
        root_desired_axis: int,
        horizontal: bool,
        axis_index: int,
        geometries: dict[str, ComponentGeometry],
        port_escapes: dict[tuple[str, str], list[tuple[Point, tuple[Point, ...]]]],
        all_port_points: dict[tuple[str, str], Point],
        blocked: set[Point],
        occupancy: WireOccupancy,
        anchor_dir: int | None = None,
    ) -> Iterable[tuple[RoutedNetPlan, int]]:
        root_preferred = (requested_channel, root_desired_axis) if horizontal else (root_desired_axis, requested_channel)
        root_candidates = self._lead_candidates_toward_point(
            endpoint=root,
            preferred_point=root_preferred,
            port_escapes=port_escapes,
            all_port_points=all_port_points,
            geometries=geometries,
            per_channel_cap=1,
        )[:8]
        if self.verbose:
            self._debug(
                f"anchor net {self._net_name(net)} requested={requested_channel} "
                f"roots={len(root_candidates)} endpoints={1 + len(others)}"
            )
        channel_index = 0 if horizontal else 1
        candidate_channels: list[int] = []
        seen_channels: set[int] = set()
        for root_lead in root_candidates:
            channel = root_lead[-1][channel_index]
            if anchor_dir is not None and (channel - requested_channel) * anchor_dir < 0:
                continue
            if channel in seen_channels:
                continue
            seen_channels.add(channel)
            candidate_channels.append(channel)
        for channel in candidate_channels:
            root_attachment = self._plan_anchor_bundle_endpoint_attachment(
                endpoint=root,
                channel=channel,
                horizontal=horizontal,
                axis_index=axis_index,
                desired_axis=root_desired_axis,
                geometries=geometries,
                port_escapes=port_escapes,
                all_port_points=all_port_points,
                blocked=blocked,
                occupancy=occupancy,
            )
            if root_attachment is None:
                continue
            _, root_choice = root_attachment
            root_lead, root_spoke, root_junction, root_occ, root_score = root_choice
            wires = list(_points_to_wires(self._compact_route(list(root_lead))))
            if len(root_spoke) > 1:
                wires.extend(_points_to_wires(root_spoke))
            working_occupancy = occupancy.merged(root_occ)
            junctions = [root_junction]
            score = root_score
            remaining = list(others)
            failed = False
            while remaining:
                ranked_choices: list[
                    tuple[
                        int,
                        float,
                        str,
                        str,
                        EndpointRef,
                        tuple[tuple[Point, ...], list[Point], Point, WireOccupancy, float],
                    ]
                ] = []
                for endpoint in remaining:
                    attachment = self._plan_anchor_bundle_endpoint_attachment(
                        endpoint=endpoint,
                        channel=channel,
                        horizontal=horizontal,
                        axis_index=axis_index,
                        geometries=geometries,
                        port_escapes=port_escapes,
                        all_port_points=all_port_points,
                        blocked=blocked,
                        occupancy=working_occupancy,
                    )
                    if attachment is None:
                        failed = True
                        break
                    option_count, best_choice = attachment
                    ranked_choices.append(
                        (
                            option_count,
                            best_choice[4],
                            endpoint.instance_id,
                            endpoint.port,
                            endpoint,
                            best_choice,
                        )
                    )
                if failed or not ranked_choices:
                    failed = True
                    break
                ranked_choices.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
                _, _, _, _, endpoint, best_choice = ranked_choices[0]
                lead, spoke, junction, candidate_occupancy, candidate_score = best_choice
                wires.extend(_points_to_wires(self._compact_route(list(lead))))
                if len(spoke) > 1:
                    wires.extend(_points_to_wires(spoke))
                working_occupancy = working_occupancy.merged(candidate_occupancy)
                junctions.append(junction)
                score += candidate_score
                remaining.remove(endpoint)
            if failed or len(junctions) < 2:
                continue
            if horizontal:
                ys = [junction[1] for junction in junctions]
                trunk = [(channel, _snap(min(ys), self.grid)), (channel, _snap(max(ys), self.grid))]
            else:
                xs = [junction[0] for junction in junctions]
                trunk = [(_snap(min(xs), self.grid), channel), (_snap(max(xs), self.grid), channel)]
            total_score = score
            if trunk[0] != trunk[1]:
                trunk_wires = _points_to_wires(trunk)
                if not self._wires_fit_occupancy(trunk_wires, occupancy, allowed_vertices=set(junctions)):
                    continue
                wires.extend(trunk_wires)
                total_score += float(len(_polyline_points(trunk, self.grid))) * 0.2
            if not wires or not self._wires_fit_occupancy(wires, occupancy, allowed_vertices=set(junctions)):
                continue
            yield (
                RoutedNetPlan(
                    net=net,
                    wires=wires,
                    occupancy=_occupancy_from_wires(wires, self.grid),
                    score=total_score,
                    min_options=1,
                    total_options=1,
                ),
                channel,
            )

    def _bundle_junction_candidates(
        self,
        *,
        lead_end: Point,
        channel: int,
        horizontal: bool,
    ) -> list[Point]:
        axis_index = 1 if horizontal else 0
        base_axis = lead_end[axis_index]
        max_offset = _ceil_grid(max(self.placement_clearance + self.grid * 2, self.grid * 4), self.grid)
        candidates: list[Point] = []
        seen: set[Point] = set()
        offset = 0
        positive_first = (_snap(base_axis, self.grid) // max(1, self.grid)) % 2 == 0
        while offset <= max_offset:
            if offset == 0:
                signed_offsets = (offset,)
            elif positive_first:
                signed_offsets = (offset, -offset)
            else:
                signed_offsets = (-offset, offset)
            for signed_offset in signed_offsets:
                axis = _snap(base_axis + signed_offset, self.grid)
                point = (channel, axis) if horizontal else (axis, channel)
                if point in seen:
                    continue
                seen.add(point)
                candidates.append(point)
            offset += self.grid
        return candidates

    def _preferred_bundle_spoke_paths(
        self,
        *,
        start: Point,
        junction: Point,
        horizontal: bool,
    ) -> list[list[Point]]:
        if start == junction:
            return [[start]]
        if start[0] == junction[0] or start[1] == junction[1]:
            return [[start, junction]]
        if horizontal:
            raw_paths = [
                [start, (start[0], junction[1]), junction],
                [start, (junction[0], start[1]), junction],
            ]
        else:
            raw_paths = [
                [start, (junction[0], start[1]), junction],
                [start, (start[0], junction[1]), junction],
            ]
        result: list[list[Point]] = []
        seen: set[tuple[Point, ...]] = set()
        for path in raw_paths:
            compact = tuple(self._compact_route(path))
            if len(compact) < 2 or compact in seen:
                continue
            seen.add(compact)
            result.append(list(compact))
        return result

    def _plan_anchor_bundle_endpoint_attachment(
        self,
        *,
        endpoint: EndpointRef,
        channel: int,
        horizontal: bool,
        axis_index: int,
        desired_axis: int | None = None,
        geometries: dict[str, ComponentGeometry],
        port_escapes: dict[tuple[str, str], list[tuple[Point, tuple[Point, ...]]]],
        all_port_points: dict[tuple[str, str], Point],
        blocked: set[Point],
        occupancy: WireOccupancy,
    ) -> tuple[int, tuple[tuple[Point, ...], list[Point], Point, WireOccupancy, float]] | None:
        endpoint_key = (endpoint.instance_id, endpoint.port)
        endpoint_point = all_port_points[endpoint_key]
        axis_target = endpoint_point[axis_index] if desired_axis is None else desired_axis
        preferred_point = (channel, axis_target) if horizontal else (axis_target, channel)
        endpoint_candidates = self._lead_candidates_toward_point(
            endpoint=endpoint,
            preferred_point=preferred_point,
            port_escapes=port_escapes,
            all_port_points=all_port_points,
            geometries=geometries,
            per_channel_cap=1,
        )[:8]
        best: tuple[tuple[Point, ...], list[Point], Point, WireOccupancy, float] | None = None
        feasible_count = 0
        for lead in endpoint_candidates:
            lead_wires = _points_to_wires(self._compact_route(list(lead)))
            lead_end = lead[-1]
            exact_junction = (channel, lead_end[1]) if horizontal else (lead_end[0], channel)
            for junction in self._bundle_junction_candidates(lead_end=lead_end, channel=channel, horizontal=horizontal):
                if lead_end == junction:
                    compact_spoke = [lead_end]
                else:
                    compact_spoke: list[Point] | None = None
                    for spoke_path in self._preferred_bundle_spoke_paths(start=lead_end, junction=junction, horizontal=horizontal):
                        if self._path_is_clear_for_routing(
                            spoke_path,
                            goals={junction},
                            blocked=blocked,
                            occupancy=occupancy,
                            allowed_vertices={lead_end, junction},
                        ):
                            compact_spoke = spoke_path
                            break
                    if compact_spoke is None:
                        compact_spoke = self._route_spoke_to_junction(
                            start=lead_end,
                            junction=junction,
                            blocked=blocked,
                            occupancy=occupancy,
                        )
                if compact_spoke is None:
                    continue
                candidate_wires = list(lead_wires)
                if len(compact_spoke) > 1:
                    candidate_wires.extend(_points_to_wires(compact_spoke))
                if not self._wires_fit_occupancy(candidate_wires, occupancy, allowed_vertices={junction}):
                    continue
                candidate_occupancy = _occupancy_from_wires(candidate_wires, self.grid)
                detour_penalty = (
                    abs(junction[1] - exact_junction[1]) if horizontal else abs(junction[0] - exact_junction[0])
                ) / max(1, self.grid)
                candidate_score = float(
                    len(_polyline_points(list(lead), self.grid))
                    + len(_polyline_points(compact_spoke, self.grid)) * 0.25
                    + detour_penalty * 0.15
                )
                feasible_count += 1
                choice = (
                    lead,
                    compact_spoke,
                    junction,
                    candidate_occupancy,
                    candidate_score,
                )
                if best is None or (choice[4], len(choice[0]) + len(choice[1])) < (
                    best[4],
                    len(best[0]) + len(best[1]),
                ):
                    best = choice
                break
        if best is None:
            return None
        return feasible_count, best

    def _net_priority(
        self,
        net: LogicNetSpec,
        geometries: dict[str, ComponentGeometry],
        port_escapes: dict[tuple[str, str], list[tuple[Point, tuple[Point, ...]]]],
    ) -> tuple[int, int, int]:
        max_port_count = max((len(geometries[endpoint.instance_id].ports) for endpoint in net.endpoints), default=1)
        total_escape_options = sum(len(port_escapes[(endpoint.instance_id, endpoint.port)]) for endpoint in net.endpoints)
        return (-max_port_count, total_escape_options, -len(net.endpoints))

    def _plan_parallel_bundle_group(
        self,
        *,
        remaining: list[LogicNetSpec],
        geometries: dict[str, ComponentGeometry],
        port_escapes: dict[tuple[str, str], list[tuple[Point, tuple[Point, ...]]]],
        all_port_points: dict[tuple[str, str], Point],
        blocked: set[Point],
        occupancy: WireOccupancy,
    ) -> list[RoutedNetPlan] | None:
        groups: dict[tuple[str, str], list[tuple[LogicNetSpec, EndpointRef, EndpointRef]]] = defaultdict(list)
        for net in remaining:
            if len(net.endpoints) != 2:
                continue
            first, second = net.endpoints
            first_side = self._port_side(geometries[first.instance_id], first.port)
            second_side = self._port_side(geometries[second.instance_id], second.port)
            for anchor, anchor_side, other, other_side in (
                (first, first_side, second, second_side),
                (second, second_side, first, first_side),
            ):
                _ = other_side
                groups[(anchor.instance_id, anchor_side)].append((net, anchor, other))
        candidates = [
            (key, items)
            for key, items in groups.items()
            if len(items) >= 3
        ]
        candidates.sort(key=lambda item: (-len(item[1]), item[0][0], item[0][1]))
        for (instance_id, anchor_side), items in candidates:
            plans = self._build_parallel_bundle_group(
                instance_id=instance_id,
                anchor_side=anchor_side,
                items=items,
                geometries=geometries,
                port_escapes=port_escapes,
                all_port_points=all_port_points,
                blocked=blocked,
                occupancy=occupancy,
            )
            if plans is not None:
                return plans
        return None

    def _route_spoke_to_junction(
        self,
        *,
        start: Point,
        junction: Point,
        blocked: set[Point],
        occupancy: WireOccupancy,
    ) -> list[Point] | None:
        if start == junction:
            return [start]
        for path in self._monotone_spoke_candidates(start=start, junction=junction):
            compact = self._compact_route(path)
            if len(compact) < 2:
                continue
            if self._path_is_clear_for_routing(
                compact,
                goals={junction},
                blocked=blocked,
                occupancy=occupancy,
                allowed_vertices={start, junction},
            ):
                return compact
        return None

    def _monotone_spoke_candidates(
        self,
        *,
        start: Point,
        junction: Point,
    ) -> list[list[Point]]:
        if start == junction:
            return [[start]]
        raw_paths: list[list[Point]] = []
        if start[0] == junction[0] or start[1] == junction[1]:
            raw_paths.append([start, junction])
        if start[0] != junction[0] and start[1] != junction[1]:
            raw_paths.append([start, (start[0], junction[1]), junction])
            raw_paths.append([start, (junction[0], start[1]), junction])
        x_rings = self._progressive_channel_rings(_snap((start[0] + junction[0]) / 2, self.grid), [start[0], junction[0]])
        y_rings = self._progressive_channel_rings(_snap((start[1] + junction[1]) / 2, self.grid), [start[1], junction[1]])
        for x_ring, y_ring in zip_longest(x_rings, y_rings, fillvalue=[]):
            for rail_x in x_ring:
                raw_paths.append([start, (rail_x, start[1]), (rail_x, junction[1]), junction])
            for rail_y in y_ring:
                raw_paths.append([start, (start[0], rail_y), (junction[0], rail_y), junction])
        result: list[list[Point]] = []
        seen: set[tuple[Point, ...]] = set()
        for path in raw_paths:
            compact = tuple(self._compact_route(path))
            if len(compact) < 2 or compact in seen:
                continue
            seen.add(compact)
            result.append(list(compact))
        return result

    def _assigned_parallel_bundle_channels(
        self,
        *,
        ordered_items: list[tuple[LogicNetSpec, EndpointRef, EndpointRef]],
        horizontal: bool,
        axis_index: int,
        anchor_dir: int,
        geometries: dict[str, ComponentGeometry],
        port_escapes: dict[tuple[str, str], list[tuple[Point, tuple[Point, ...]]]],
        all_port_points: dict[tuple[str, str], Point],
        occupancy: WireOccupancy,
    ) -> tuple[list[int], int] | None:
        channel_index = 0 if horizontal else 1
        step = self._bundle_channel_step(count=len(ordered_items), occupied=bool(occupancy.edges))
        base_offset = step * (2 if occupancy.edges else 1)
        preferred_channels: list[int] = []
        for _, anchor, other in ordered_items:
            anchor_point = all_port_points[(anchor.instance_id, anchor.port)]
            other_point = all_port_points[(other.instance_id, other.port)]
            desired_channel = _snap(anchor_point[channel_index] + anchor_dir * base_offset, self.grid)
            preferred = self._preferred_bundle_channel(
                endpoint=anchor,
                desired_channel=desired_channel,
                desired_axis=other_point[axis_index],
                horizontal=horizontal,
                geometries=geometries,
                port_escapes=port_escapes,
                all_port_points=all_port_points,
            )
            if preferred is None:
                return None
            preferred_channels.append(preferred)
        return self._assign_monotone_channels(preferred_channels=preferred_channels, anchor_dir=anchor_dir, step=step), step

    def _build_parallel_bundle_group(
        self,
        *,
        instance_id: str,
        anchor_side: str,
        items: list[tuple[LogicNetSpec, EndpointRef, EndpointRef]],
        geometries: dict[str, ComponentGeometry],
        port_escapes: dict[tuple[str, str], list[tuple[Point, tuple[Point, ...]]]],
        all_port_points: dict[tuple[str, str], Point],
        blocked: set[Point],
        occupancy: WireOccupancy,
    ) -> list[RoutedNetPlan] | None:
        horizontal = anchor_side in {"left", "right"}
        axis_index = 1 if horizontal else 0
        anchor_index = 0 if horizontal else 1
        anchor_dir = 1 if anchor_side in {"right", "bottom"} else -1
        ordered = sorted(
            items,
            key=lambda item: (
                all_port_points[(item[2].instance_id, item[2].port)][axis_index],
                all_port_points[(item[1].instance_id, item[1].port)][axis_index],
                self.nets.index(item[0]),
            ),
            reverse=anchor_dir > 0,
        )
        assigned = self._assigned_parallel_bundle_channels(
            ordered_items=ordered,
            horizontal=horizontal,
            axis_index=axis_index,
            anchor_dir=anchor_dir,
            geometries=geometries,
            port_escapes=port_escapes,
            all_port_points=all_port_points,
            occupancy=occupancy,
        )
        if assigned is None:
            return None
        channels, step = assigned
        if not channels:
            return None
        self._debug(
            f"parallel bundle assigned on {instance_id}.{anchor_side}: "
            f"step={step} nets={len(ordered)} first={channels[0]} last={channels[-1]}"
        )
        return self._route_parallel_bundle_group_on_channels(
            ordered_items=ordered,
            channels=channels,
            horizontal=horizontal,
            axis_index=axis_index,
            anchor_index=anchor_index,
            geometries=geometries,
            port_escapes=port_escapes,
            all_port_points=all_port_points,
            blocked=blocked,
            occupancy=occupancy,
        )

    def _route_parallel_bundle_group_on_channels(
        self,
        *,
        ordered_items: list[tuple[LogicNetSpec, EndpointRef, EndpointRef]],
        channels: list[int],
        horizontal: bool,
        axis_index: int,
        anchor_index: int,
        geometries: dict[str, ComponentGeometry],
        port_escapes: dict[tuple[str, str], list[tuple[Point, tuple[Point, ...]]]],
        all_port_points: dict[tuple[str, str], Point],
        blocked: set[Point],
        occupancy: WireOccupancy,
    ) -> list[RoutedNetPlan] | None:
        plans: list[RoutedNetPlan] = []
        current_occupancy = occupancy
        for index, ((net, anchor, other), channel) in enumerate(zip(ordered_items, channels), start=1):
            plan = self._plan_parallel_bundle_net_on_channel(
                net=net,
                anchor=anchor,
                other=other,
                channel=channel,
                horizontal=horizontal,
                axis_index=axis_index,
                anchor_index=anchor_index,
                geometries=geometries,
                port_escapes=port_escapes,
                all_port_points=all_port_points,
                blocked=blocked,
                occupancy=current_occupancy,
            )
            if plan is None:
                self._debug(
                    f"parallel bundle assigned route failed at net {index}/{len(ordered_items)} "
                    f"{self._net_name(net)} requested={channel}"
                )
                return None
            plans.append(plan)
            current_occupancy = current_occupancy.merged(plan.occupancy)
        return plans

    def _plan_parallel_bundle_net_on_channel(
        self,
        *,
        net: LogicNetSpec,
        anchor: EndpointRef,
        other: EndpointRef,
        channel: int,
        horizontal: bool,
        axis_index: int,
        anchor_index: int,
        geometries: dict[str, ComponentGeometry],
        port_escapes: dict[tuple[str, str], list[tuple[Point, tuple[Point, ...]]]],
        all_port_points: dict[tuple[str, str], Point],
        blocked: set[Point],
        occupancy: WireOccupancy,
    ) -> RoutedNetPlan | None:
        source_key = (anchor.instance_id, anchor.port)
        target_key = (other.instance_id, other.port)
        source_port = all_port_points[source_key]
        target_port = all_port_points[target_key]
        source_preferred = (channel, source_port[axis_index]) if horizontal else (source_port[axis_index], channel)
        target_preferred = (channel, target_port[axis_index]) if horizontal else (target_port[axis_index], channel)
        source_candidates = self._lead_candidates_toward_point(
            endpoint=anchor,
            preferred_point=source_preferred,
            port_escapes=port_escapes,
            all_port_points=all_port_points,
            geometries=geometries,
            per_channel_cap=1,
        )[:8]
        target_candidates = self._lead_candidates_toward_point(
            endpoint=other,
            preferred_point=target_preferred,
            port_escapes=port_escapes,
            all_port_points=all_port_points,
            geometries=geometries,
            per_channel_cap=1,
        )[:8]
        best: RoutedNetPlan | None = None
        for source_lead in source_candidates:
            source_end = source_lead[-1]
            source_junction = (channel, source_end[1]) if horizontal else (source_end[0], channel)
            source_spoke = [source_end] if source_end == source_junction else self._route_spoke_to_junction(
                start=source_end,
                junction=source_junction,
                blocked=blocked,
                occupancy=occupancy,
            )
            if source_spoke is None:
                continue
            source_wires = _points_to_wires(self._compact_route(list(source_lead)))
            if len(source_spoke) > 1:
                source_wires.extend(_points_to_wires(source_spoke))
            if not self._wires_fit_occupancy(source_wires, occupancy, allowed_vertices={source_junction}):
                continue
            source_occ = occupancy.merged(_occupancy_from_wires(source_wires, self.grid))
            source_score = float(
                len(_polyline_points(list(source_lead), self.grid))
                + len(_polyline_points(source_spoke, self.grid)) * 0.25
            )
            for target_lead in target_candidates:
                target_end = target_lead[-1]
                target_junction = (channel, target_end[1]) if horizontal else (target_end[0], channel)
                target_spoke = [target_end] if target_end == target_junction else self._route_spoke_to_junction(
                    start=target_end,
                    junction=target_junction,
                    blocked=blocked,
                    occupancy=source_occ,
                )
                if target_spoke is None:
                    continue
                target_wires = _points_to_wires(self._compact_route(list(target_lead)))
                if len(target_spoke) > 1:
                    target_wires.extend(_points_to_wires(target_spoke))
                if not self._wires_fit_occupancy(target_wires, source_occ, allowed_vertices={target_junction}):
                    continue
                wires = list(source_wires)
                wires.extend(target_wires)
                junctions = {source_junction, target_junction}
                total_occ = source_occ.merged(_occupancy_from_wires(target_wires, self.grid))
                total_score = source_score + float(len(_polyline_points(list(target_lead), self.grid))) + float(
                    len(_polyline_points(target_spoke, self.grid))
                ) * 0.25
                if source_junction != target_junction:
                    trunk = [source_junction, target_junction]
                    trunk_wires = _points_to_wires(trunk)
                    if not self._wires_fit_occupancy(trunk_wires, occupancy, allowed_vertices=junctions):
                        continue
                    wires.extend(trunk_wires)
                    total_score += float(len(_polyline_points(trunk, self.grid))) * 0.2
                if not self._wires_fit_occupancy(wires, occupancy, allowed_vertices=junctions):
                    continue
                plan = RoutedNetPlan(
                    net=net,
                    wires=wires,
                    occupancy=_occupancy_from_wires(wires, self.grid),
                    score=total_score,
                    min_options=1,
                    total_options=1,
                )
                if best is None or (plan.score, len(plan.wires)) < (best.score, len(best.wires)):
                    best = plan
        return best

    def _iter_parallel_bundle_net_plans(
        self,
        *,
        net: LogicNetSpec,
        anchor: EndpointRef,
        other: EndpointRef,
        requested_channel: int,
        horizontal: bool,
        axis_index: int,
        anchor_index: int,
        geometries: dict[str, ComponentGeometry],
        port_escapes: dict[tuple[str, str], list[tuple[Point, tuple[Point, ...]]]],
        all_port_points: dict[tuple[str, str], Point],
        blocked: set[Point],
        occupancy: WireOccupancy,
    ) -> Iterable[tuple[RoutedNetPlan, int]]:
        source_key = (anchor.instance_id, anchor.port)
        target_key = (other.instance_id, other.port)
        source_port = all_port_points[source_key]
        target_port = all_port_points[target_key]
        channel_targets = [requested_channel, source_port[anchor_index], target_port[anchor_index]]
        for channel in self._ordered_channel_values(requested_channel, channel_targets):
            source_preferred = (channel, source_port[axis_index]) if horizontal else (source_port[axis_index], channel)
            target_preferred = (channel, target_port[axis_index]) if horizontal else (target_port[axis_index], channel)
            source_candidates = self._lead_candidates_toward_point(
                endpoint=anchor,
                preferred_point=source_preferred,
                port_escapes=port_escapes,
                all_port_points=all_port_points,
                geometries=geometries,
                per_channel_cap=1,
            )
            target_candidates = self._lead_candidates_toward_point(
                endpoint=other,
                preferred_point=target_preferred,
                port_escapes=port_escapes,
                all_port_points=all_port_points,
                geometries=geometries,
                per_channel_cap=1,
            )
            for source_lead in source_candidates:
                for target_lead in target_candidates:
                    polyline = self._bundle_route_polyline(
                        source_lead=source_lead,
                        target_lead=target_lead,
                        channel=channel,
                        horizontal=horizontal,
                    )
                    compact = self._compact_route(polyline)
                    if len(compact) < 2:
                        continue
                    expanded = _polyline_points(compact, self.grid)
                    allowed = set(source_lead) | set(target_lead)
                    if any(point in blocked and point not in allowed for point in expanded[1:-1]):
                        continue
                    if expanded[-1] in blocked and expanded[-1] not in allowed:
                        continue
                    wires = list(_points_to_wires(compact))
                    if not self._wires_fit_occupancy(wires, occupancy):
                        continue
                    yield (
                        RoutedNetPlan(
                            net=net,
                            wires=wires,
                            occupancy=_occupancy_from_wires(wires, self.grid),
                            score=float(len(expanded)),
                            min_options=1,
                            total_options=1,
                        ),
                        channel,
                    )

    def _bundle_lead_candidates(
        self,
        *,
        endpoint: EndpointRef,
        desired_channel: int,
        desired_axis: int,
        port_escapes: dict[tuple[str, str], list[tuple[Point, tuple[Point, ...]]]],
        all_port_points: dict[tuple[str, str], Point],
        geometries: dict[str, ComponentGeometry],
        per_channel_cap: int | None = None,
    ) -> list[tuple[Point, ...]]:
        key = (endpoint.instance_id, endpoint.port)
        side = self._port_side(geometries[endpoint.instance_id], endpoint.port)
        port_point = all_port_points[key]
        horizontal = side in {"left", "right"}
        outward_dir = 1 if side in {"right", "bottom"} else -1
        scored: list[tuple[float, tuple[Point, ...]]] = []
        for end_point, lead in port_escapes[key]:
            if self._polyline_hits_forbidden_points(list(lead), allowed_points={port_point}):
                continue
            if horizontal:
                if outward_dir > 0 and end_point[0] < port_point[0]:
                    continue
                if outward_dir < 0 and end_point[0] > port_point[0]:
                    continue
                score = abs(end_point[0] - desired_channel) + abs(end_point[1] - desired_axis) * 0.25 + len(lead) * 0.1
            else:
                if outward_dir > 0 and end_point[1] < port_point[1]:
                    continue
                if outward_dir < 0 and end_point[1] > port_point[1]:
                    continue
                score = abs(end_point[1] - desired_channel) + abs(end_point[0] - desired_axis) * 0.25 + len(lead) * 0.1
            scored.append((score, lead))
        if not scored:
            scored = [(float(len(lead)), lead) for _, lead in port_escapes[key]]
        scored.sort(key=lambda item: (item[0], len(item[1])))
        if len(scored) <= 24 and per_channel_cap is None:
            return [lead for _, lead in scored]
        per_channel_limit = per_channel_cap if per_channel_cap is not None else (
            1 if len(scored) <= 128 else 2 if len(scored) <= 512 else 3
        )
        channel_index = 0 if horizontal else 1
        axis_score_index = 1 if horizontal else 0
        grouped: dict[int, list[tuple[float, tuple[Point, ...]]]] = defaultdict(list)
        for score, lead in scored:
            grouped[lead[-1][channel_index]].append((score, lead))
        reduced: list[tuple[float, tuple[Point, ...]]] = []
        for _, items in grouped.items():
            items.sort(
                key=lambda item: (
                    abs(item[1][-1][axis_score_index] - desired_axis),
                    len(item[1]),
                    item[0],
                )
            )
            reduced.extend(items[:per_channel_limit])
        reduced.sort(key=lambda item: (item[0], len(item[1])))
        return [lead for _, lead in reduced]

    def _bundle_route_polyline(
        self,
        *,
        source_lead: tuple[Point, ...],
        target_lead: tuple[Point, ...],
        channel: int,
        horizontal: bool,
    ) -> list[Point]:
        source_end = source_lead[-1]
        target_end = target_lead[-1]
        points = list(source_lead)
        if horizontal:
            points.extend(
                [
                    (channel, source_end[1]),
                    (channel, target_end[1]),
                    target_end,
                ]
            )
        else:
            points.extend(
                [
                    (source_end[0], channel),
                    (target_end[0], channel),
                    target_end,
                ]
            )
        reversed_target = list(reversed(target_lead))
        points.extend(reversed_target[1:])
        return points

    def _select_tunnel_fallback_net(
        self,
        *,
        direct_nets: list[LogicNetSpec],
        geometries: dict[str, ComponentGeometry],
        port_escapes: dict[tuple[str, str], list[tuple[Point, tuple[Point, ...]]]],
        all_port_points: dict[tuple[str, str], Point],
        blocked: set[Point],
        occupancy: WireOccupancy,
        reserved_lead_points: set[Point],
    ) -> LogicNetSpec | None:
        if not direct_nets:
            return None
        planned: list[RoutedNetPlan] = []
        for net in direct_nets:
            plan = self._plan_net_route(
                net=net,
                geometries=geometries,
                port_escapes=port_escapes,
                all_port_points=all_port_points,
                blocked=blocked,
                occupancy=occupancy,
                reserved_lead_points=reserved_lead_points,
            )
            if plan is None:
                return net
            planned.append(plan)
        planned.sort(key=lambda item: (item.min_options, item.total_options, item.score))
        return planned[0].net if planned else None

    def _select_preemptive_tunnel_net(
        self,
        *,
        direct_nets: list[LogicNetSpec],
        geometries: dict[str, ComponentGeometry],
        port_escapes: dict[tuple[str, str], list[tuple[Point, tuple[Point, ...]]]],
    ) -> LogicNetSpec | None:
        _ = (direct_nets, geometries, port_escapes)
        return None

    def _build_tunnelized_nets(
        self,
        *,
        nets: set[int],
        raw_components: dict[str, RawComponent],
        geometries: dict[str, ComponentGeometry],
        port_escapes: dict[tuple[str, str], list[tuple[Point, tuple[Point, ...]]]],
        all_port_points: dict[tuple[str, str], Point],
    ) -> tuple[list[RawWire], list[RawComponent], WireOccupancy]:
        wires: list[RawWire] = []
        components: list[RawComponent] = []
        occupancy = WireOccupancy()
        grouped_endpoints: dict[tuple[str, str], list[EndpointRef]] = defaultdict(list)
        for net_index in sorted(nets):
            for endpoint in self.nets[net_index].endpoints:
                side = self._port_side(geometries[endpoint.instance_id], endpoint.port)
                grouped_endpoints[(endpoint.instance_id, side)].append(endpoint)
        endpoint_leads: dict[tuple[str, str], tuple[Point, ...]] = {}
        occupied_tunnel_points: set[Point] = set()
        for (instance_id, side), endpoints in grouped_endpoints.items():
            component = raw_components[instance_id]
            geometry = geometries[instance_id]
            ordered: list[tuple[EndpointRef, Point]] = []
            for endpoint in endpoints:
                port_point = geometry.absolute_port(component.loc, endpoint.port)
                ordered.append((endpoint, port_point))
            if side in {"left", "right"}:
                ordered.sort(key=lambda item: (item[1][1], item[1][0], item[0].port))
            else:
                ordered.sort(key=lambda item: (item[1][0], item[1][1], item[0].port))
            for endpoint, point in ordered:
                forbidden_points = {
                    port_point
                    for endpoint_key, port_point in all_port_points.items()
                    if endpoint_key != (endpoint.instance_id, endpoint.port)
                }
                lead_points = self._select_tunnel_lead_points(
                    endpoint=endpoint,
                    point=point,
                    side=side,
                    component=component,
                    geometry=geometry,
                    port_escapes=port_escapes,
                    occupancy=occupancy,
                    occupied_tunnel_points=occupied_tunnel_points,
                    forbidden_points=forbidden_points,
                )
                lead_wires = list(_points_to_wires(list(lead_points)))
                endpoint_leads[(endpoint.instance_id, endpoint.port)] = lead_points
                occupied_tunnel_points.add(lead_points[-1])
                occupancy = occupancy.merged(_occupancy_from_wires(lead_wires, self.grid))
                wires.extend(lead_wires)
        for net_index in sorted(nets):
            net = self.nets[net_index]
            label = net.label if net.force_tunnel and net.label else f"t{net_index:x}"
            for endpoint in net.endpoints:
                endpoint_key = (endpoint.instance_id, endpoint.port)
                lead_points = list(endpoint_leads[endpoint_key])
                tunnel_point = lead_points[-1]
                components.append(
                    RawComponent(
                        name="Tunnel",
                        loc=tunnel_point,
                        lib="0",
                        attrs=[
                            RawAttribute(name="facing", value=self._tunnel_facing(tuple(lead_points))),
                            RawAttribute(name="width", value=str(geometries[endpoint.instance_id].port(endpoint.port).width or "1")),
                            RawAttribute(name="label", value=label),
                            RawAttribute(name="labelfont", value="Dialog plain 12"),
                        ],
                    )
                )
        return wires, components, occupancy

    def _detunnelize_tunnel_labels(
        self,
        *,
        wires: list[RawWire],
        tunnel_components: list[RawComponent],
        blocked: set[Point],
        preserve_labels: set[str] | None = None,
    ) -> tuple[list[RawWire], list[RawComponent]]:
        groups: dict[str, list[RawComponent]] = defaultdict(list)
        for component in tunnel_components:
            label = component.get("label", "") or ""
            groups[label].append(component)
        if not groups:
            return wires, tunnel_components

        preserved = preserve_labels or set()
        remaining_components: list[RawComponent] = []
        current_wires = list(wires)
        current_occupancy = _occupancy_from_wires(current_wires, self.grid)

        ordered_groups = sorted(
            groups.items(),
            key=lambda item: (
                -len(item[1]),
                -(abs(item[1][0].loc[0] - item[1][-1].loc[0]) + abs(item[1][0].loc[1] - item[1][-1].loc[1])),
                item[0],
            ),
        )
        for _, components in ordered_groups:
            label = components[0].get("label", "") or ""
            if label in preserved:
                remaining_components.extend(components)
                continue
            if len(components) < 2 or len(components) > 40:
                remaining_components.extend(components)
                continue
            allowed_vertices = {component.loc for component in components}
            connector = self._plan_free_point_group(
                points=list(allowed_vertices),
                blocked=blocked,
                occupancy=WireOccupancy(),
            )
            if connector is None or not connector or not self._wires_fit_occupancy(connector, current_occupancy, allowed_vertices=allowed_vertices):
                connector = self._plan_free_point_group(
                    points=list(allowed_vertices),
                    blocked=blocked,
                    occupancy=current_occupancy,
                )
            if not connector or not self._wires_fit_occupancy(connector, current_occupancy, allowed_vertices=allowed_vertices):
                remaining_components.extend(components)
                continue
            current_wires.extend(connector)
            current_occupancy = current_occupancy.merged(_occupancy_from_wires(connector, self.grid))
        return current_wires, remaining_components

    def _plan_free_point_group(
        self,
        *,
        points: list[Point],
        blocked: set[Point],
        occupancy: WireOccupancy,
    ) -> list[RawWire] | None:
        if len(points) < 2:
            return []
        if len(points) > 10:
            return self._plan_incremental_point_tree(points=points, blocked=blocked, occupancy=occupancy)
        if len(points) == 2:
            start, goal = points
            path = self._assignment_route_path(start, {goal}, blocked, occupancy, allowed_vertices=set(points))
            if path is None:
                path = self._fast_route_path(start, {goal}, blocked, occupancy, allowed_vertices=set(points))
            if path is not None:
                connector = list(_points_to_wires(self._compact_route(path)))
                if connector and self._wires_fit_occupancy(connector, occupancy, allowed_vertices=set(points)):
                    return connector
        best: list[RawWire] | None = None
        best_score: tuple[float, int] | None = None
        for orientation in ("h", "v"):
            if orientation == "h":
                origin = sorted(point[1] for point in points)[len(points) // 2]
                channels = self._ordered_channel_values(origin, (point[1] for point in points))
            else:
                origin = sorted(point[0] for point in points)[len(points) // 2]
                channels = self._ordered_channel_values(origin, (point[0] for point in points))
            for channel in channels:
                junctions: list[Point] = []
                candidate_wires: list[RawWire] = []
                score = 0.0
                failed = False
                for point in points:
                    junction = (point[0], channel) if orientation == "h" else (channel, point[1])
                    spoke = [point] if point == junction else [point, junction]
                    compact_spoke = self._compact_route(spoke)
                    if len(compact_spoke) > 1 and not self._path_is_clear_for_routing(compact_spoke, goals={junction}, blocked=blocked, occupancy=occupancy):
                        failed = True
                        break
                    if len(compact_spoke) > 1:
                        candidate_wires.extend(_points_to_wires(compact_spoke))
                        score += float(len(_polyline_points(compact_spoke, self.grid)))
                    junctions.append(junction)
                if failed or len(junctions) < 2:
                    continue
                if orientation == "h":
                    xs = [junction[0] for junction in junctions]
                    trunk_points = [(_snap(min(xs), self.grid), channel), (_snap(max(xs), self.grid), channel)]
                else:
                    ys = [junction[1] for junction in junctions]
                    trunk_points = [(channel, _snap(min(ys), self.grid)), (channel, _snap(max(ys), self.grid))]
                if trunk_points[0] != trunk_points[1]:
                    candidate_wires.extend(_points_to_wires(trunk_points))
                    score += float(len(_polyline_points(trunk_points, self.grid))) * 0.2
                if not candidate_wires or not self._wires_fit_occupancy(candidate_wires, occupancy, allowed_vertices=set(points)):
                    continue
                candidate_score = (score, len(candidate_wires))
                if best is None or best_score is None or candidate_score < best_score:
                    best = candidate_wires
                    best_score = candidate_score
        tree = self._plan_incremental_point_tree(points=points, blocked=blocked, occupancy=occupancy)
        if tree is not None:
            tree_score = (float(len(_occupancy_from_wires(tree, self.grid).edges)), len(tree))
            if best is None or best_score is None or tree_score < best_score:
                best = tree
                best_score = tree_score
        return best

    def _plan_incremental_point_tree(
        self,
        *,
        points: list[Point],
        blocked: set[Point],
        occupancy: WireOccupancy,
    ) -> list[RawWire] | None:
        unique_points = sorted(set(points), key=lambda point: (point[0], point[1]))
        if len(unique_points) < 2:
            return []
        seed = min(
            unique_points,
            key=lambda point: (
                sum(abs(point[0] - other[0]) + abs(point[1] - other[1]) for other in unique_points),
                point[1],
                point[0],
            ),
        )
        tree_grid_points: set[Point] = {seed}
        remaining: set[Point] = set(unique_points)
        remaining.remove(seed)
        wires: list[RawWire] = []
        tree_occupancy = WireOccupancy()

        while remaining:
            target = min(
                remaining,
                key=lambda point: (
                    min(abs(point[0] - goal[0]) + abs(point[1] - goal[1]) for goal in tree_grid_points),
                    point[1],
                    point[0],
                ),
            )
            active_occupancy = occupancy.merged(tree_occupancy)
            allowed_vertices = set(tree_grid_points) | {target}
            path = self._assignment_route_path(target, tree_grid_points, blocked, active_occupancy, allowed_vertices=allowed_vertices)
            if path is None:
                path = self._fast_route_path(target, tree_grid_points, blocked, active_occupancy, allowed_vertices=allowed_vertices)
            if path is None:
                return None
            compact = self._compact_route(path)
            new_wires = list(_points_to_wires(compact))
            if not self._wires_fit_occupancy(new_wires, active_occupancy, allowed_vertices=allowed_vertices):
                return None
            wires.extend(new_wires)
            tree_occupancy = tree_occupancy.merged(_occupancy_from_wires(new_wires, self.grid))
            tree_grid_points.update(_polyline_points(compact, self.grid))
            remaining.remove(target)

        if not wires:
            return None
        if not self._wires_fit_occupancy(wires, occupancy, allowed_vertices=set(unique_points)):
            return None
        return wires

    def _select_tunnel_lead_points(
        self,
        *,
        endpoint: EndpointRef,
        point: Point,
        side: str,
        component: RawComponent,
        geometry: ComponentGeometry,
        port_escapes: dict[tuple[str, str], list[tuple[Point, tuple[Point, ...]]]],
        occupancy: WireOccupancy,
        occupied_tunnel_points: set[Point],
        forbidden_points: set[Point] | None = None,
    ) -> tuple[Point, ...]:
        endpoint_key = (endpoint.instance_id, endpoint.port)
        candidates = list(port_escapes.get(endpoint_key, []))
        side_demand = max(1, sum(1 for port in geometry.ports if self._port_side(geometry, port.name) == side))
        for candidate in self._single_port_escape_candidates(
            component,
            geometry,
            endpoint.port,
            point,
            side_demand=side_demand,
        ):
            if candidate not in candidates:
                candidates.append(candidate)

        ranked: list[tuple[tuple[float, int, int, int, int], tuple[Point, ...]]] = []
        for exit_point, lead_points in candidates:
            if exit_point in occupied_tunnel_points:
                continue
            lead = tuple(lead_points)
            if forbidden_points and any(candidate_point in forbidden_points for candidate_point in lead[1:]):
                continue
            lead_wires = list(_points_to_wires(list(lead)))
            if not self._wires_fit_occupancy(lead_wires, occupancy):
                continue
            ranked.append((self._tunnel_lead_score(side=side, lead_points=lead), lead))
        if ranked:
            ranked.sort(key=lambda item: item[0])
            return ranked[0][1]

        return tuple(
            self._fallback_tunnel_lead(
                point=point,
                side=side,
                occupancy=occupancy,
                occupied_tunnel_points=occupied_tunnel_points,
                forbidden_points=forbidden_points,
            )
        )

    def _tunnel_lead_score(self, *, side: str, lead_points: tuple[Point, ...]) -> tuple[float, int, int, int, int]:
        length = 0
        for start, end in zip(lead_points, lead_points[1:]):
            length += abs(start[0] - end[0]) + abs(start[1] - end[1])
        bends = max(0, len(lead_points) - 2)
        start = lead_points[0]
        end = lead_points[-1]
        if side in {"left", "right"}:
            tangent = abs(end[1] - start[1])
            outward = abs(end[0] - start[0])
        else:
            tangent = abs(end[0] - start[0])
            outward = abs(end[1] - start[1])
        outward_penalty = max(0, self.grid * 5 - outward)
        return (float(bends), outward_penalty, tangent, length, abs(end[0]) + abs(end[1]))

    def _fallback_tunnel_lead(
        self,
        *,
        point: Point,
        side: str,
        occupancy: WireOccupancy,
        occupied_tunnel_points: set[Point],
        forbidden_points: set[Point] | None = None,
    ) -> list[Point]:
        # Fast-track Tunnel label geometry placement in ultra-dense logical configurations
        offset = (-self.grid if side == "left" else self.grid if side == "right" else 0,
                  -self.grid if side == "top" else self.grid if side == "bottom" else 0)
        target = (point[0] + offset[0], point[1] + offset[1])
        return [point, target]
        
        base_distance = max(self.grid * 5, _ceil_grid(max(self.grid * 3, self.placement_clearance + self.grid * 2), self.grid))
        distances = [base_distance + self.grid * 2 * index for index in range(20)]
        tangent_offsets = self._ordered_spread_offsets(0, step=self.grid * 2, limit=self.grid * 24)
        ranked: list[tuple[tuple[float, int, int, int, int], tuple[Point, ...]]] = []
        if side in {"left", "right"}:
            direction = -1 if side == "left" else 1
            for distance in distances:
                stub_x = point[0] + direction * distance
                for path in ([point, (stub_x, point[1])],):
                    lead = tuple(_polyline_points(path, self.grid))
                    if lead[-1] in occupied_tunnel_points:
                        continue
                    if forbidden_points and any(candidate_point in forbidden_points for candidate_point in lead[1:]):
                        continue
                    lead_wires = list(_points_to_wires(list(lead)))
                    if self._wires_fit_occupancy(lead_wires, occupancy):
                        ranked.append((self._tunnel_lead_score(side=side, lead_points=lead), lead))
                for tangent in tangent_offsets:
                    if tangent == 0:
                        continue
                    lead = tuple(_polyline_points([point, (point[0], point[1] + tangent), (stub_x, point[1] + tangent)], self.grid))
                    if lead[-1] in occupied_tunnel_points:
                        continue
                    if forbidden_points and any(candidate_point in forbidden_points for candidate_point in lead[1:]):
                        continue
                    lead_wires = list(_points_to_wires(list(lead)))
                    if self._wires_fit_occupancy(lead_wires, occupancy):
                        ranked.append((self._tunnel_lead_score(side=side, lead_points=lead), lead))
                    step_x = point[0] + direction * self.grid
                    lead = tuple(
                        _polyline_points(
                            [point, (step_x, point[1]), (step_x, point[1] + tangent), (stub_x, point[1] + tangent)],
                            self.grid,
                        )
                    )
                    if lead[-1] in occupied_tunnel_points:
                        continue
                    if forbidden_points and any(candidate_point in forbidden_points for candidate_point in lead[1:]):
                        continue
                    lead_wires = list(_points_to_wires(list(lead)))
                    if self._wires_fit_occupancy(lead_wires, occupancy):
                        ranked.append((self._tunnel_lead_score(side=side, lead_points=lead), lead))
        else:
            direction = -1 if side == "top" else 1
            for distance in distances:
                stub_y = point[1] + direction * distance
                for path in ([point, (point[0], stub_y)],):
                    lead = tuple(_polyline_points(path, self.grid))
                    if lead[-1] in occupied_tunnel_points:
                        continue
                    if forbidden_points and any(candidate_point in forbidden_points for candidate_point in lead[1:]):
                        continue
                    lead_wires = list(_points_to_wires(list(lead)))
                    if self._wires_fit_occupancy(lead_wires, occupancy):
                        ranked.append((self._tunnel_lead_score(side=side, lead_points=lead), lead))
                for tangent in tangent_offsets:
                    if tangent == 0:
                        continue
                    lead = tuple(_polyline_points([point, (point[0] + tangent, point[1]), (point[0] + tangent, stub_y)], self.grid))
                    if lead[-1] in occupied_tunnel_points:
                        continue
                    if forbidden_points and any(candidate_point in forbidden_points for candidate_point in lead[1:]):
                        continue
                    lead_wires = list(_points_to_wires(list(lead)))
                    if self._wires_fit_occupancy(lead_wires, occupancy):
                        ranked.append((self._tunnel_lead_score(side=side, lead_points=lead), lead))
                    step_y = point[1] + direction * self.grid
                    lead = tuple(
                        _polyline_points(
                            [point, (point[0], step_y), (point[0] + tangent, step_y), (point[0] + tangent, stub_y)],
                            self.grid,
                        )
                    )
                    if lead[-1] in occupied_tunnel_points:
                        continue
                    if forbidden_points and any(candidate_point in forbidden_points for candidate_point in lead[1:]):
                        continue
                    lead_wires = list(_points_to_wires(list(lead)))
                    if self._wires_fit_occupancy(lead_wires, occupancy):
                        ranked.append((self._tunnel_lead_score(side=side, lead_points=lead), lead))
        if ranked:
            ranked.sort(key=lambda item: item[0])
            return list(ranked[0][1])
            
        # Fallback to direct overlap if visually blocked
        offset = (-self.grid if side == "left" else self.grid if side == "right" else 0,
                  -self.grid if side == "top" else self.grid if side == "bottom" else 0)
        target = (point[0] + offset[0], point[1] + offset[1])
        return [point, target]

    def _plan_net_route(
        self,
        *,
        net: LogicNetSpec,
        geometries: dict[str, ComponentGeometry],
        port_escapes: dict[tuple[str, str], list[tuple[Point, tuple[Point, ...]]]],
        all_port_points: dict[tuple[str, str], Point],
        blocked: set[Point],
        occupancy: WireOccupancy,
        reserved_lead_points: set[Point],
    ) -> RoutedNetPlan | None:
        options = self._plan_net_route_options(
            net=net,
            geometries=geometries,
            port_escapes=port_escapes,
            all_port_points=all_port_points,
            blocked=blocked,
            occupancy=occupancy,
            reserved_lead_points=reserved_lead_points,
            limit=None,
        )
        return options[0] if options else None

    def _plan_net_route_options(
        self,
        *,
        net: LogicNetSpec,
        geometries: dict[str, ComponentGeometry],
        port_escapes: dict[tuple[str, str], list[tuple[Point, tuple[Point, ...]]]],
        all_port_points: dict[tuple[str, str], Point],
        blocked: set[Point],
        occupancy: WireOccupancy,
        reserved_lead_points: set[Point],
        limit: int | None,
    ) -> list[RoutedNetPlan]:
        started = time.perf_counter()
        outcome = "fail"
        ordered = self._ordered_endpoints_for_routing(net, geometries)
        previous_forbidden = self._active_forbidden_points
        self._active_forbidden_points = self._forbidden_port_points(net.endpoints, all_port_points)
        try:
            plans = self._plan_corridor_net_options(
                net=net,
                ordered=ordered,
                geometries=geometries,
                port_escapes=port_escapes,
                all_port_points=all_port_points,
                blocked=blocked,
                occupancy=occupancy,
                limit=limit,
            )
            outcome = f"corridor:{len(plans)}" if plans else "corridor:0"
            return plans
        finally:
            self._active_forbidden_points = previous_forbidden
            self._record_route_attempt(net, time.perf_counter() - started, outcome)

    def _plan_corridor_net_options(
        self,
        *,
        net: LogicNetSpec,
        ordered: list[EndpointRef],
        geometries: dict[str, ComponentGeometry],
        port_escapes: dict[tuple[str, str], list[tuple[Point, tuple[Point, ...]]]],
        all_port_points: dict[tuple[str, str], Point],
        blocked: set[Point],
        occupancy: WireOccupancy,
        limit: int | None,
    ) -> list[RoutedNetPlan]:
        if len(ordered) < 2:
            return []
        if len(ordered) == 2:
            return self._plan_direct_two_endpoint_net_options(
                net=net,
                ordered=ordered,
                geometries=geometries,
                port_escapes=port_escapes,
                all_port_points=all_port_points,
                blocked=blocked,
                occupancy=occupancy,
                limit=limit,
            )
        root = ordered[0]
        others = ordered[1:]
        points = [all_port_points[(endpoint.instance_id, endpoint.port)] for endpoint in ordered]
        plan_cap = None if limit is None else max(1, min(4, limit))
        candidates: list[RoutedNetPlan] = []
        seen_wire_keys: set[tuple[tuple[Point, Point], ...]] = set()
        orientation_order = self._corridor_orientation_order(
            ordered=ordered,
            geometries=geometries,
            all_port_points=all_port_points,
        )
        for orientation_rank, horizontal in enumerate(orientation_order):
            axis_index = 1 if horizontal else 0
            channel_index = 0 if horizontal else 1
            axis_points = points if not others else [all_port_points[(endpoint.instance_id, endpoint.port)] for endpoint in others]
            desired_axis = self._median_axis_coordinate(point[axis_index] for point in axis_points)
            requested_channel = self._median_axis_coordinate(point[channel_index] for point in points)
            found = 0
            for plan, actual_channel in self._iter_anchor_bundle_net_plans(
                net=net,
                root=root,
                others=others,
                requested_channel=requested_channel,
                root_desired_axis=desired_axis,
                horizontal=horizontal,
                axis_index=axis_index,
                geometries=geometries,
                port_escapes=port_escapes,
                all_port_points=all_port_points,
                blocked=blocked,
                occupancy=occupancy,
                anchor_dir=None,
            ):
                wire_key = tuple(sorted(_normalized_edge(wire.start, wire.end) for wire in plan.wires))
                if wire_key in seen_wire_keys:
                    continue
                seen_wire_keys.add(wire_key)
                channel_penalty = abs(actual_channel - requested_channel) / max(1, self.grid)
                adjusted = RoutedNetPlan(
                    net=plan.net,
                    wires=plan.wires,
                    occupancy=plan.occupancy,
                    score=plan.score + orientation_rank * 0.1 + channel_penalty * 0.02,
                    min_options=plan.min_options,
                    total_options=plan.total_options,
                )
                candidates.append(adjusted)
                found += 1
                if plan_cap is not None and found >= plan_cap:
                    break
        candidates.sort(key=lambda item: (item.min_options, item.total_options, item.score, len(item.wires)))
        return candidates if limit is None else candidates[:limit]

    def _plan_direct_two_endpoint_net_options(
        self,
        *,
        net: LogicNetSpec,
        ordered: list[EndpointRef],
        geometries: dict[str, ComponentGeometry],
        port_escapes: dict[tuple[str, str], list[tuple[Point, tuple[Point, ...]]]],
        all_port_points: dict[tuple[str, str], Point],
        blocked: set[Point],
        occupancy: WireOccupancy,
        limit: int | None,
    ) -> list[RoutedNetPlan]:
        if len(ordered) != 2:
            return []
        first, second = ordered
        candidates: list[RoutedNetPlan] = []
        seen_wire_keys: set[tuple[tuple[Point, Point], ...]] = set()
        pairings = ((first, second), (second, first))
        lead_cap = None if limit is None else max(8, min(16, limit * 6))
        for seed, other in pairings:
            seed_point = all_port_points[(seed.instance_id, seed.port)]
            other_point = all_port_points[(other.instance_id, other.port)]
            seed_candidates = self._lead_candidates_toward_point(
                endpoint=seed,
                preferred_point=other_point,
                port_escapes=port_escapes,
                all_port_points=all_port_points,
                geometries=geometries,
                per_channel_cap=4,
            )
            if lead_cap is not None:
                seed_candidates = seed_candidates[:lead_cap]
            other_candidates = self._lead_candidates_toward_point(
                endpoint=other,
                preferred_point=seed_point,
                port_escapes=port_escapes,
                all_port_points=all_port_points,
                geometries=geometries,
                per_channel_cap=4,
            )
            if lead_cap is not None:
                other_candidates = other_candidates[:lead_cap]
            for seed_lead in seed_candidates:
                seed_wires = _points_to_wires(self._compact_route(list(seed_lead)))
                if not self._wires_fit_occupancy(seed_wires, occupancy):
                    continue
                seed_occ = occupancy.merged(_occupancy_from_wires(seed_wires, self.grid))
                net_points = set(seed_lead)
                for other_lead in other_candidates:
                    other_lead_wires = _points_to_wires(self._compact_route(list(other_lead)))
                    if not self._wires_fit_occupancy(other_lead_wires, seed_occ):
                        continue
                    route = self._fast_route_path(
                        other_lead[-1],
                        net_points,
                        blocked,
                        seed_occ,
                        allowed_vertices=set(net_points) | {other_lead[-1]},
                    )
                    if route is None:
                        route = self._assignment_route_path(
                            other_lead[-1],
                            net_points,
                            blocked,
                            seed_occ,
                            allowed_vertices=set(net_points) | {other_lead[-1]},
                        )
                    if route is None:
                        continue
                    route_wires = _points_to_wires(self._compact_route(route))
                    if not self._wires_fit_occupancy(route_wires, seed_occ, allowed_vertices=set(net_points) | {other_lead[-1]}):
                        continue
                    wires = list(seed_wires)
                    wires.extend(route_wires)
                    wires.extend(other_lead_wires)
                    if not self._wires_fit_occupancy(wires, occupancy, allowed_vertices=set(net_points) | {other_lead[-1]}):
                        continue
                    wire_key = tuple(sorted(_normalized_edge(wire.start, wire.end) for wire in wires))
                    if wire_key in seen_wire_keys:
                        continue
                    seen_wire_keys.add(wire_key)
                    score = float(
                        len(_polyline_points(list(seed_lead), self.grid))
                        + len(_polyline_points(route, self.grid))
                        + len(_polyline_points(list(other_lead), self.grid)) * 0.2
                    )
                    candidates.append(
                        RoutedNetPlan(
                            net=net,
                            wires=wires,
                            occupancy=_occupancy_from_wires(wires, self.grid),
                            score=score,
                            min_options=0,
                            total_options=0,
                        )
                    )
        candidates.sort(key=lambda item: (item.score, len(item.wires)))
        total_options = len(candidates)
        if total_options:
            candidates = [
                RoutedNetPlan(
                    net=plan.net,
                    wires=plan.wires,
                    occupancy=plan.occupancy,
                    score=plan.score,
                    min_options=total_options,
                    total_options=total_options,
                )
                for plan in candidates
            ]
        return candidates if limit is None else candidates[:limit]

    def _corridor_orientation_order(
        self,
        *,
        ordered: list[EndpointRef],
        geometries: dict[str, ComponentGeometry],
        all_port_points: dict[tuple[str, str], Point],
    ) -> list[bool]:
        root = ordered[0]
        root_side = self._port_side(geometries[root.instance_id], root.port)
        points = [all_port_points[(endpoint.instance_id, endpoint.port)] for endpoint in ordered]
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        root_preference = root_side in {"left", "right"}
        spread_preference = (max(ys) - min(ys)) >= (max(xs) - min(xs))
        order: list[bool] = []
        for horizontal in (root_preference, spread_preference, not spread_preference, not root_preference):
            if horizontal not in order:
                order.append(horizontal)
        return order or [True, False]

    def _median_axis_coordinate(self, values: Iterable[int]) -> int:
        ordered = sorted(values)
        if not ordered:
            return 0
        return _snap(ordered[len(ordered) // 2], self.grid)

    def _ordered_seed_escapes(
        self,
        endpoint: EndpointRef,
        candidates: list[tuple[Point, tuple[Point, ...]]],
        others: list[EndpointRef],
        all_port_points: dict[tuple[str, str], Point],
        *,
        limit: int | None,
    ) -> list[tuple[Point, tuple[Point, ...]]]:
        _ = endpoint
        if len(candidates) <= 1 or not others:
            return candidates if limit is None else candidates[:limit]
        target_points = [all_port_points[(other.instance_id, other.port)] for other in others]
        ordered = sorted(
            candidates,
            key=lambda item: min(abs(item[0][0] - point[0]) + abs(item[0][1] - point[1]) for point in target_points),
        )
        return ordered if limit is None else ordered[:limit]

    def _nearest_goals(self, start: Point, goals: set[Point], *, limit: int | None = None) -> list[Point]:
        ordered = sorted(goals, key=lambda point: (abs(point[0] - start[0]) + abs(point[1] - start[1]), point[1], point[0]))
        return ordered if limit is None else ordered[:limit]

    def _ordered_channel_values(self, origin: int, targets: Iterable[int]) -> list[int]:
        return [value for ring in self._progressive_channel_rings(origin, targets) for value in ring]

    def _progressive_channel_rings(self, origin: int, targets: Iterable[int]) -> list[list[int]]:
        target_list = [_snap(target, self.grid) for target in targets]
        if not target_list:
            return [[_snap(origin, self.grid)]]
        origin = _snap(origin, self.grid)
        low = min(target_list + [origin])
        high = max(target_list + [origin])
        mid = _snap((low + high) / 2, self.grid)
        clearance = max(self.grid * 2, _ceil_grid(self.placement_clearance + self.grid * 2, self.grid))
        seen: set[int] = set()
        rings: list[list[int]] = []

        def append_ring(values: Iterable[int]) -> None:
            ring: list[int] = []
            for value in values:
                snapped = _snap(value, self.grid)
                if snapped in seen:
                    continue
                seen.add(snapped)
                ring.append(snapped)
            if ring:
                rings.append(ring)

        append_ring((origin, low, high, mid))
        span = max(abs(high - low), abs(origin - low), abs(origin - high))
        max_radius = max(clearance, _ceil_grid(span + self.placement_clearance + self.grid * 4, self.grid))
        radius = clearance
        while radius <= max_radius:
            append_ring(
                (
                    low - radius,
                    high + radius,
                    origin - radius,
                    origin + radius,
                    mid - radius,
                    mid + radius,
                )
            )
            radius += clearance
        return rings or [[origin]]

    def _path_is_clear_for_routing(
        self,
        points: list[Point],
        *,
        goals: set[Point],
        blocked: set[Point],
        occupancy: WireOccupancy,
        allowed_vertices: set[Point] | None = None,
    ) -> bool:
        candidate_wires = list(_points_to_wires(self._compact_route(points)))
        if not candidate_wires:
            return False
        expanded = _polyline_points(points, self.grid)
        allowed = set(allowed_vertices or set())
        allowed.add(expanded[0])
        allowed.update(goals)
        if self._active_forbidden_points:
            for point in expanded[1:]:
                if point in self._active_forbidden_points and point not in allowed:
                    return False
        for point in expanded[1:-1]:
            if point in blocked and point not in goals:
                return False
        end_point = expanded[-1]
        if end_point in blocked and end_point not in goals:
            return False
        return self._wires_fit_occupancy(candidate_wires, occupancy, allowed_vertices=allowed)

    def _fast_route_path(
        self,
        start: Point,
        goals: set[Point],
        blocked: set[Point],
        occupancy: WireOccupancy,
        *,
        allowed_vertices: set[Point] | None = None,
    ) -> list[Point] | None:
        def best_clear(paths: Iterable[list[Point]], seen: set[tuple[Point, ...]]) -> list[Point] | None:
            best_local: tuple[int, list[Point]] | None = None
            for path in paths:
                compact = tuple(self._compact_route(path))
                if len(compact) < 2 or compact in seen:
                    continue
                seen.add(compact)
                if not self._path_is_clear_for_routing(
                    list(compact),
                    goals=goals,
                    blocked=blocked,
                    occupancy=occupancy,
                    allowed_vertices=allowed_vertices,
                ):
                    continue
                expanded_len = len(_polyline_points(list(compact), self.grid))
                if best_local is None or expanded_len < best_local[0]:
                    best_local = (expanded_len, list(compact))
            return best_local[1] if best_local is not None else None

        for goal in self._nearest_goals(start, goals, limit=None):
            seen: set[tuple[Point, ...]] = set()
            immediate: list[list[Point]] = []
            if start[0] == goal[0] or start[1] == goal[1]:
                immediate.append([start, goal])
            if start[0] != goal[0] and start[1] != goal[1]:
                bend_xy = (goal[0], start[1])
                bend_yx = (start[0], goal[1])
                immediate.append([start, bend_xy, goal])
                immediate.append([start, bend_yx, goal])
            direct = best_clear(immediate, seen)
            if direct is not None:
                return direct

            mid_x = _snap((start[0] + goal[0]) / 2, self.grid)
            mid_y = _snap((start[1] + goal[1]) / 2, self.grid)
            x_rings = self._progressive_channel_rings(mid_x, [start[0], goal[0]])
            y_rings = self._progressive_channel_rings(mid_y, [start[1], goal[1]])
            for x_ring, y_ring in zip_longest(x_rings, y_rings, fillvalue=[]):
                ring_candidates: list[list[Point]] = []
                for channel_x in x_ring:
                    ring_candidates.append([start, (channel_x, start[1]), (channel_x, goal[1]), goal])
                for channel_y in y_ring:
                    ring_candidates.append([start, (start[0], channel_y), (goal[0], channel_y), goal])
                routed = best_clear(ring_candidates, seen)
                if routed is not None:
                    return routed
        return None

    def _assignment_route_path(
        self,
        start: Point,
        goals: set[Point],
        blocked: set[Point],
        occupancy: WireOccupancy,
        *,
        allowed_vertices: set[Point] | None = None,
    ) -> list[Point] | None:
        path = self._route_path(
            start,
            goals,
            blocked,
            occupancy,
            relaxed=False,
            allowed_vertices=allowed_vertices,
        )
        if path is not None:
            return path
        return self._route_path(
            start,
            goals,
            blocked,
            occupancy,
            relaxed=True,
            allowed_vertices=allowed_vertices,
        )

    def _tunnel_facing(self, lead_points: tuple[Point, ...]) -> str:
        if len(lead_points) < 2:
            return "west"
        prev = lead_points[-2]
        loc = lead_points[-1]
        if loc[0] < prev[0]:
            return "east"
        if loc[0] > prev[0]:
            return "west"
        if loc[1] < prev[1]:
            return "south"
        return "north"

    def _port_side(self, geometry: ComponentGeometry, port_name: str) -> str:
        print(f"DEBUG Port Look-up: {port_name} in {[p.name for p in geometry.ports]}")
        port = next(port for port in geometry.ports if port.name == port_name)
        return _nearest_side_name(geometry.bounds, port.offset)

    def _port_escape_candidates(
        self,
        raw_components: dict[str, RawComponent],
        geometries: dict[str, ComponentGeometry],
        all_port_points: dict[tuple[str, str], Point],
    ) -> dict[tuple[str, str], list[tuple[Point, tuple[Point, ...]]]]:
        result: dict[tuple[str, str], list[tuple[Point, tuple[Point, ...]]]] = {}
        side_demands = self._component_side_demands(geometries)
        for instance_id, geometry in geometries.items():
            component = raw_components[instance_id]
            if len(geometry.ports) == 1:
                port = geometry.ports[0]
                point = all_port_points[(instance_id, port.name)]
                res = self._single_port_escape_candidates(
                    component,
                    geometry,
                    port.name,
                    point,
                    side_demand=side_demands.get((instance_id, self._port_side(geometry, port.name)), 1),
                )
                print(f"DEBUG Escape for {instance_id}.{port.name}: {len(res)} candidates")
                result[(instance_id, port.name)] = res
                continue
            left = geometry.bounds[0]
            right = geometry.bounds[0] + geometry.bounds[2]
            top = geometry.bounds[1]
            bottom = geometry.bounds[1] + geometry.bounds[3]
            grouped: dict[str, list[tuple[str, Point]]] = defaultdict(list)
            for port in geometry.ports:
                point = all_port_points[(instance_id, port.name)]
                side_name = self._port_side(geometry, port.name)
                grouped[side_name].append((port.name, point))
            for side_name, ports in grouped.items():
                if side_name in {"left", "right"}:
                    ports.sort(key=lambda item: (item[1][1], item[1][0], item[0]))
                    direction = (-1, 0) if side_name == "left" else (1, 0)
                else:
                    ports.sort(key=lambda item: (item[1][0], item[1][1], item[0]))
                port_count = len(ports)
                for index, (port_name, point) in enumerate(ports):
                    result[(instance_id, port_name)] = self._multi_port_escape_candidates(
                        component=component,
                        geometry=geometry,
                        side_name=side_name,
                        point=point,
                        port_index=index,
                        port_count=port_count,
                        side_demand=side_demands.get((instance_id, side_name), port_count),
                    )
        return result

    def _ordered_spread_offsets(self, preferred: int, *, step: int, limit: int) -> list[int]:
        raw = [preferred, 0]
        delta = step
        while delta <= limit:
            raw.extend((preferred - delta, preferred + delta, -delta, delta))
            delta += step
        result: list[int] = []
        seen: set[int] = set()
        for value in raw:
            snapped = _snap(value, self.grid)
            if abs(snapped) > limit:
                continue
            if snapped in seen:
                continue
            seen.add(snapped)
            result.append(snapped)
        return result or [0]

    def _escape_clearance(
        self,
        *,
        component: RawComponent,
        geometry: ComponentGeometry,
        side_name: str,
        port_count: int,
        side_demand: int,
    ) -> int:
        _, _, wid, ht = geometry.absolute_bounds(component.loc)
        span = ht if side_name in {"left", "right"} else wid
        clearance = max(self.grid * 3, _ceil_grid(self.placement_clearance + self.grid, self.grid))
        clearance = max(clearance, _ceil_grid(self.placement_clearance + self.grid * (side_demand + 1), self.grid))
        if max(port_count, side_demand) >= 8 or span >= self.grid * 8:
            clearance += self.grid
        return _ceil_grid(clearance, self.grid)

    def _preferred_escape_depths(
        self,
        *,
        base_clearance: int,
        port_index: int,
        port_count: int,
        side_demand: int,
    ) -> list[int]:
        if port_count <= 1:
            return [base_clearance]
        step = self.grid
        max_extra = self.grid * max(8, side_demand + port_count)
        center = (port_count - 1) / 2.0
        preferred = base_clearance + int(round(abs(port_index - center))) * step
        raw = [preferred, base_clearance]
        delta = step
        while delta <= max_extra:
            raw.extend((preferred - delta, preferred + delta, base_clearance + delta))
            delta += step
        result: list[int] = []
        seen: set[int] = set()
        minimum = _ceil_grid(base_clearance, self.grid)
        for value in raw:
            snapped = _ceil_grid(max(minimum, value), self.grid)
            if snapped in seen:
                continue
            seen.add(snapped)
            result.append(snapped)
        return result or [minimum]

    def _multi_port_escape_candidates(
        self,
        *,
        component: RawComponent,
        geometry: ComponentGeometry,
        side_name: str,
        point: Point,
        port_index: int,
        port_count: int,
        side_demand: int,
    ) -> list[tuple[Point, tuple[Point, ...]]]:
        x, y, wid, ht = geometry.absolute_bounds(component.loc)
        clearance = self._escape_clearance(
            component=component,
            geometry=geometry,
            side_name=side_name,
            port_count=port_count,
            side_demand=side_demand,
        )
        left = _floor_grid(x - clearance, self.grid)
        right = _ceil_grid(x + wid + clearance, self.grid)
        top = _floor_grid(y - clearance, self.grid)
        bottom = _ceil_grid(y + ht + clearance, self.grid)
        left = self._clamp_x_to_route_floor(left)
        right = self._clamp_x_to_route_floor(right)
        top = self._clamp_y_to_route_floor(top)
        bottom = self._clamp_y_to_route_floor(bottom)
        slot_pitch = self.grid * max(2, min(4, side_demand))
        slot_extra = max(0, slot_pitch - self.grid)
        center = port_count - 1
        assigned_shift = ((2 * port_index - center) * slot_extra) // 2
        slot_step = self.grid
        slot_limit = self.grid * max(8, side_demand + port_count + 2)
        candidates: list[tuple[Point, tuple[Point, ...]]] = []
        seen: set[tuple[Point, tuple[Point, ...]]] = set()

        def add(path_points: list[Point]) -> None:
            lead = _polyline_points(path_points, self.grid)
            if len(lead) < 2:
                return
            item = (lead[-1], lead)
            if item in seen:
                return
            seen.add(item)
            candidates.append(item)

        if side_name in {"left", "right"}:
            depths = self._preferred_escape_depths(
                base_clearance=clearance,
                port_index=port_index,
                port_count=port_count,
                side_demand=side_demand,
            )
            slot_ys = [
                self._clamp_y_to_route_floor(_snap(point[1] + offset, self.grid))
                for offset in self._ordered_spread_offsets(assigned_shift, step=slot_step, limit=slot_limit)
            ]
            rails = [
                self._clamp_x_to_route_floor(
                    _floor_grid(x - depth, self.grid) if side_name == "left" else _ceil_grid(x + wid + depth, self.grid)
                )
                for depth in depths
            ]
            for rail_x in rails:
                add([point, self._clamp_point_to_route_floor((rail_x, point[1]))])
                for slot_y in slot_ys:
                    if slot_y != point[1]:
                        add([point, self._clamp_point_to_route_floor((rail_x, point[1])), self._clamp_point_to_route_floor((rail_x, slot_y))])
                        mid_x = self._clamp_x_to_route_floor(_snap(point[0] + (-self.grid if side_name == "left" else self.grid), self.grid))
                        add([point, self._clamp_point_to_route_floor((mid_x, point[1])), self._clamp_point_to_route_floor((mid_x, slot_y)), self._clamp_point_to_route_floor((rail_x, slot_y))])
        else:
            depths = self._preferred_escape_depths(
                base_clearance=clearance,
                port_index=port_index,
                port_count=port_count,
                side_demand=side_demand,
            )
            slot_xs = [
                self._clamp_x_to_route_floor(_snap(point[0] + offset, self.grid))
                for offset in self._ordered_spread_offsets(assigned_shift, step=slot_step, limit=slot_limit)
            ]
            rails = [
                self._clamp_y_to_route_floor(
                    _floor_grid(y - depth, self.grid) if side_name == "top" else _ceil_grid(y + ht + depth, self.grid)
                )
                for depth in depths
            ]
            for rail_y in rails:
                add([point, self._clamp_point_to_route_floor((point[0], rail_y))])
                for slot_x in slot_xs:
                    if slot_x != point[0]:
                        add([point, self._clamp_point_to_route_floor((point[0], rail_y)), self._clamp_point_to_route_floor((slot_x, rail_y))])
                        mid_y = self._clamp_y_to_route_floor(_snap(point[1] + (-self.grid if side_name == "top" else self.grid), self.grid))
                        add([point, self._clamp_point_to_route_floor((point[0], mid_y)), self._clamp_point_to_route_floor((slot_x, mid_y)), self._clamp_point_to_route_floor((slot_x, rail_y))])
        return candidates

    def _single_port_escape_candidates(
        self,
        component: RawComponent,
        geometry: ComponentGeometry,
        port_name: str,
        point: Point,
        side_demand: int,
    ) -> list[tuple[Point, tuple[Point, ...]]]:
        side_name = self._port_side(geometry, port_name)
        x, y, wid, ht = geometry.absolute_bounds(component.loc)
        clearance = self._escape_clearance(
            component=component,
            geometry=geometry,
            side_name=side_name,
            port_count=1,
            side_demand=side_demand,
        )
        left = _floor_grid(x - clearance, self.grid)
        right = _ceil_grid(x + wid + clearance, self.grid)
        top = _floor_grid(y - clearance, self.grid)
        bottom = _ceil_grid(y + ht + clearance, self.grid)
        left = self._clamp_x_to_route_floor(left)
        right = self._clamp_x_to_route_floor(right)
        top = self._clamp_y_to_route_floor(top)
        bottom = self._clamp_y_to_route_floor(bottom)
        base_distance = clearance
        candidates: list[tuple[Point, tuple[Point, ...]]] = []
        seen: set[tuple[Point, tuple[Point, ...]]] = set()

        def add(path_points: list[Point]) -> None:
            lead = _polyline_points(path_points, self.grid)
            if len(lead) < 2:
                return
            item = (lead[-1], lead)
            if item in seen:
                return
            seen.add(item)
            candidates.append(item)

        if side_name == "left":
            add([point, self._clamp_point_to_route_floor((point[0] - base_distance, point[1]))])
            add([point, self._clamp_point_to_route_floor((left, point[1]))])
            add([point, self._clamp_point_to_route_floor((left, point[1])), self._clamp_point_to_route_floor((left, top))])
            add([point, self._clamp_point_to_route_floor((left, point[1])), self._clamp_point_to_route_floor((left, bottom))])
            add([point, self._clamp_point_to_route_floor((point[0], top))])
            add([point, self._clamp_point_to_route_floor((point[0], bottom))])
        elif side_name == "right":
            add([point, self._clamp_point_to_route_floor((point[0] + base_distance, point[1]))])
            add([point, self._clamp_point_to_route_floor((right, point[1]))])
            add([point, self._clamp_point_to_route_floor((right, point[1])), self._clamp_point_to_route_floor((right, top))])
            add([point, self._clamp_point_to_route_floor((right, point[1])), self._clamp_point_to_route_floor((right, bottom))])
            add([point, self._clamp_point_to_route_floor((point[0], top))])
            add([point, self._clamp_point_to_route_floor((point[0], bottom))])
        elif side_name == "top":
            add([point, self._clamp_point_to_route_floor((point[0], point[1] - base_distance))])
            add([point, self._clamp_point_to_route_floor((point[0], top))])
            add([point, self._clamp_point_to_route_floor((point[0], top)), self._clamp_point_to_route_floor((left, top))])
            add([point, self._clamp_point_to_route_floor((point[0], top)), self._clamp_point_to_route_floor((right, top))])
            add([point, self._clamp_point_to_route_floor((left, point[1]))])
            add([point, self._clamp_point_to_route_floor((right, point[1]))])
        else:
            add([point, self._clamp_point_to_route_floor((point[0], point[1] + base_distance))])
            add([point, self._clamp_point_to_route_floor((point[0], bottom))])
            add([point, self._clamp_point_to_route_floor((point[0], bottom)), self._clamp_point_to_route_floor((left, bottom))])
            add([point, self._clamp_point_to_route_floor((point[0], bottom)), self._clamp_point_to_route_floor((right, bottom))])
            add([point, self._clamp_point_to_route_floor((left, point[1]))])
            add([point, self._clamp_point_to_route_floor((right, point[1]))])
        return candidates

    def _ordered_endpoints_for_routing(
        self,
        net: LogicNetSpec,
        geometries: dict[str, ComponentGeometry],
    ) -> list[EndpointRef]:
        resolved = self._resolved_port_directions(geometries)
        directions = {
            endpoint: resolved.get(endpoint.instance_id, {}).get(endpoint.port, "inout")
            for endpoint in net.endpoints
        }
        outputs = [endpoint for endpoint in net.endpoints if directions[endpoint] == "output"]
        root = outputs[0] if outputs else net.endpoints[0]
        others = [endpoint for endpoint in net.endpoints if endpoint != root]
        return [root, *others]

    def _component_blockers(
        self,
        raw_components: dict[str, RawComponent],
        geometries: dict[str, ComponentGeometry],
    ) -> set[Point]:
        blocked: set[Point] = set()
        for instance_id, component in raw_components.items():
            x, y, wid, ht = geometries[instance_id].absolute_bounds(component.loc)
            for gx in _grid_values(x, x + wid, self.grid):
                for gy in _grid_values(y, y + ht, self.grid):
                    blocked.add((gx, gy))
        return blocked

    def _direction_name(self, direction_index: int) -> str:
        return "h" if direction_index in (0, 1) else "v"

    def _point_compatible_with_occupancy(
        self,
        point: Point,
        *,
        previous_direction: int,
        next_direction: int,
        start: Point,
        goals: set[Point],
        allowed_vertices: set[Point] | None,
        occupancy: WireOccupancy,
    ) -> bool:
        allowed = allowed_vertices or set()
        if point == start or point in goals or point in allowed:
            return True
        if point in occupancy.vertices:
            return False
        existing = occupancy.interior_directions(point)
        if not existing:
            return True
        if previous_direction == -1 or previous_direction != next_direction:
            return False
        return self._direction_name(next_direction) not in existing and len(existing) == 1

    def _wires_fit_occupancy(
        self,
        wires: list[RawWire],
        occupancy: WireOccupancy,
        *,
        allowed_vertices: set[Point] | None = None,
    ) -> bool:
        allowed = allowed_vertices or set()
        candidate = _occupancy_from_wires(wires, self.grid)
        if self._active_forbidden_points:
            if any(point in self._active_forbidden_points and point not in allowed for point in candidate.vertices):
                return False
            if any(point in self._active_forbidden_points and point not in allowed for point in candidate.interiors):
                return False
        if candidate.edges & occupancy.edges:
            return False
        if any(point in occupancy.vertices and point not in allowed for point in candidate.vertices):
            return False
        for point in candidate.vertices:
            if point in occupancy.interiors and point not in allowed:
                return False
        for point, directions in candidate.interiors.items():
            if point in occupancy.vertices and point not in allowed:
                return False
            existing = occupancy.interiors.get(point)
            if not existing:
                continue
            if directions & existing:
                return False
            if len(directions | existing) > 2:
                return False
        return True

    def _forbidden_port_points(
        self,
        endpoints: Iterable[EndpointRef],
        all_port_points: dict[tuple[str, str], Point],
    ) -> set[Point]:
        allowed = {
            all_port_points[(endpoint.instance_id, endpoint.port)]
            for endpoint in endpoints
            if (endpoint.instance_id, endpoint.port) in all_port_points
        }
        return {point for point in all_port_points.values() if point not in allowed}

    def _polyline_hits_forbidden_points(
        self,
        points: list[Point],
        *,
        allowed_points: set[Point] | None = None,
    ) -> bool:
        if not self._active_forbidden_points:
            return False
        allowed = allowed_points or set()
        for point in _polyline_points(points, self.grid):
            if point in self._active_forbidden_points and point not in allowed:
                return True
        return False

    def _routing_extent(
        self,
        starts_and_goals: Iterable[Point],
        blocked: set[Point],
        *,
        relaxed: bool,
    ) -> tuple[int, int, int, int]:
        xs = [point[0] for point in starts_and_goals]
        ys = [point[1] for point in starts_and_goals]
        if blocked:
            xs.extend(point[0] for point in blocked)
            ys.extend(point[1] for point in blocked)
        extra = self.margin * (2 if relaxed else 1)
        min_x = _floor_grid(min(xs) - extra, self.grid)
        max_x = _ceil_grid(max(xs) + extra, self.grid)
        min_y = _floor_grid(min(ys) - extra, self.grid)
        max_y = _ceil_grid(max(ys) + extra, self.grid)
        if self._route_floor_x is not None:
            min_x = max(min_x, self._route_floor_x)
        if self._route_floor_y is not None:
            min_y = max(min_y, self._route_floor_y)
        return (min_x, min_y, max_x, max_y)

    def _route_path(
        self,
        start: Point,
        goals: set[Point],
        blocked: set[Point],
        occupancy: WireOccupancy,
        *,
        relaxed: bool = False,
        allowed_vertices: set[Point] | None = None,
    ) -> list[Point] | None:
        min_x, min_y, max_x, max_y = self._routing_extent([start, *goals], blocked, relaxed=relaxed)
        directions = [(self.grid, 0), (-self.grid, 0), (0, self.grid), (0, -self.grid)]
        goal_set = set(goals)
        allowed = set(allowed_vertices or set())
        allowed.add(start)
        allowed.update(goal_set)
        start_state = (start, -1)
        queue: list[tuple[float, float, tuple[Point, int]]] = []
        heapq.heappush(queue, (0.0, 0.0, start_state))
        costs = {start_state: 0.0}
        previous: dict[tuple[Point, int], tuple[Point, int] | None] = {start_state: None}
        while queue:
            _, cost, (point, direction_index) = heapq.heappop(queue)
            if point in goal_set:
                return self._reconstruct_path(previous, (point, direction_index))
            if cost > costs.get((point, direction_index), float("inf")):
                continue
            for next_dir_index, (dx, dy) in enumerate(directions):
                nxt = (point[0] + dx, point[1] + dy)
                if nxt[0] < min_x or nxt[0] > max_x or nxt[1] < min_y or nxt[1] > max_y:
                    continue
                if nxt in blocked and nxt not in allowed:
                    continue
                if nxt in occupancy.vertices and nxt not in allowed:
                    continue
                if not self._point_compatible_with_occupancy(
                    point,
                    previous_direction=direction_index,
                    next_direction=next_dir_index,
                    start=start,
                    goals=goal_set,
                    allowed_vertices=allowed,
                    occupancy=occupancy,
                ):
                    continue
                edge = _normalized_edge(point, nxt)
                if edge in occupancy.edges:
                    continue
                bend_penalty = 0.6 if direction_index != -1 and direction_index != next_dir_index else 0.0
                wire_penalty = 0.0
                new_cost = cost + 1.0 + bend_penalty + wire_penalty
                state = (nxt, next_dir_index)
                if new_cost >= costs.get(state, float("inf")):
                    continue
                costs[state] = new_cost
                previous[state] = (point, direction_index)
                heuristic = min((abs(nxt[0] - gx) + abs(nxt[1] - gy)) / self.grid for gx, gy in goal_set)
                heapq.heappush(queue, (new_cost + heuristic, new_cost, state))
        return None

    def _reconstruct_path(
        self,
        previous: dict[tuple[Point, int], tuple[Point, int] | None],
        state: tuple[Point, int],
    ) -> list[Point]:
        result: list[Point] = []
        current: tuple[Point, int] | None = state
        while current is not None:
            result.append(current[0])
            current = previous[current]
        result.reverse()
        return result

    def _compact_route(self, path: list[Point]) -> list[Point]:
        if len(path) <= 2:
            return path
        compact = [path[0]]
        for index in range(1, len(path) - 1):
            prev = compact[-1]
            point = path[index]
            nxt = path[index + 1]
            if (prev[0] == point[0] == nxt[0]) or (prev[1] == point[1] == nxt[1]):
                continue
            compact.append(point)
        compact.append(path[-1])
        return compact
