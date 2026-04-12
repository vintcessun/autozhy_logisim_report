from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable
from copy import deepcopy
from itertools import count

from .geometry import get_component_geometry
from .logic_builder import LogicCircuitBuilder, _occupancy_from_wires, _points_to_wires
from .layout import (
    attachment_facing_for_port,
    component_bounds,
    component_extents,
    default_splitter_pitch as module_default_splitter_pitch,
    place_attached_component,
    route_circuit_path,
)
from .model import RawAttribute, RawCircuit, RawComponent, RawProject, RawWire

_LABEL_COUNTER = count()
_ATTACHMENTS: defaultdict[int, list[dict[str, object]]] = defaultdict(list)
_BUS_TUNNELS: defaultdict[int, list[dict[str, object]]] = defaultdict(list)
_TUNNEL_SLOT_STATE: defaultdict[tuple[object, ...], set[int]] = defaultdict(set)

_OPPOSITE_FACING = {
    "east": "west",
    "west": "east",
    "north": "south",
    "south": "north",
}

SUBCIRCUIT_INSTANCE_ATTRS = {
    "facing": "east",
    "label": "",
    "labelloc": "north",
    "labelfont": "Dialog plain 12",
    "labelcolor": "#000000",
}

def unique_label(prefix: str) -> str:
    _ = prefix
    return f"n{next(_LABEL_COUNTER):x}"


def connect_signal_fanout(
    builder: LogicCircuitBuilder,
    signal_fanout: dict[str, list[str]],
    *,
    forced_signals: set[str] | None = None,
) -> None:
    forced = forced_signals or set()
    for signal, endpoints in signal_fanout.items():
        if len(endpoints) < 2:
            continue
        builder.connect(*endpoints, label=signal, force_tunnel=signal in forced)


def detunnelize_circuit(
    circuit: RawCircuit,
    *,
    keep_labels: set[str] | None = None,
    project: RawProject | None = None,
    passes: int = 2,
) -> None:
    preserved = set(keep_labels or set())
    max_passes = max(1, passes)
    for _ in range(max_passes):
        tunnels = [component for component in circuit.components if component.name == "Tunnel"]
        if not tunnels:
            return
        builder = LogicCircuitBuilder(f"{circuit.name}__detunnel", project=project, allow_tunnel_fallback=True)
        blocked_components: dict[str, RawComponent] = {}
        blocked_geometries = {}
        for index, component in enumerate(circuit.components):
            if component.name in {"Tunnel", "Text"}:
                continue
            try:
                geometry = get_component_geometry(component, project=project)
            except Exception:
                continue
            key = f"c{index:x}"
            blocked_components[key] = component
            blocked_geometries[key] = geometry
        blocked = builder._component_blockers(blocked_components, blocked_geometries) if blocked_components else set()
        wires, remaining_tunnels = builder._detunnelize_tunnel_labels(
            wires=list(circuit.wires),
            tunnel_components=tunnels,
            blocked=blocked,
            preserve_labels=preserved,
        )
        changed = len(remaining_tunnels) != len(tunnels) or len(wires) != len(circuit.wires)
        circuit.components = [component for component in circuit.components if component.name != "Tunnel"] + remaining_tunnels
        circuit.wires = wires
        if not changed:
            break
    _detunnelize_remaining_groups(circuit, preserved=preserved, project=project)


def detunnelize_selected_tunnels(
    circuit: RawCircuit,
    *,
    remove_labels: set[str] | None = None,
    keep_labels: set[str] | None = None,
    remove_predicate: Callable[[RawComponent], bool] | None = None,
    keep_predicate: Callable[[RawComponent], bool] | None = None,
    project: RawProject | None = None,
    passes: int = 2,
    check_widths: bool = True,
) -> dict[str, list[str]]:
    """Replace explicitly selected Tunnel components with real wires.

    Existing semantic tunnels should usually be kept.  If a kept tunnel shares
    a label with selected tunnels, the label group is wired first and the kept
    tunnel is restored at its original location as a visual/net-name marker.
    """

    labels_to_remove = set(remove_labels or set())
    labels_to_keep = set(keep_labels or set())
    if not labels_to_remove and remove_predicate is None:
        raise ValueError("detunnelize_selected_tunnels requires remove_labels or remove_predicate")

    report: dict[str, list[str]] = {
        "removed": [],
        "kept": [],
        "skipped": [],
    }

    def label_of(component: RawComponent) -> str:
        return component.get("label", "") or ""

    def should_keep(component: RawComponent) -> bool:
        label = label_of(component)
        return label in labels_to_keep or (keep_predicate is not None and keep_predicate(component))

    def should_remove(component: RawComponent) -> bool:
        if should_keep(component):
            return False
        label = label_of(component)
        return label in labels_to_remove or (remove_predicate is not None and remove_predicate(component))

    def tunnel_components(subject: RawCircuit) -> list[RawComponent]:
        return [component for component in subject.components if component.name == "Tunnel"]

    grouped: defaultdict[str, list[RawComponent]] = defaultdict(list)
    for tunnel in tunnel_components(circuit):
        grouped[label_of(tunnel)].append(tunnel)

    for label in sorted(grouped):
        components = grouped[label]
        selected = [component for component in components if should_remove(component)]
        if not selected:
            report["kept"].append(label)
            continue
        if len(components) < 2:
            report["skipped"].append(f"{label}: singleton")
            continue
        widths = {_tunnel_width(component) for component in components}
        if len(widths) != 1:
            report["skipped"].append(f"{label}: mixed widths {sorted(widths)}")
            continue

        selected_locations = {tuple(component.loc) for component in selected}
        restored = [deepcopy(component) for component in components if not should_remove(component)]
        restored_locations = {tuple(component.loc) for component in restored}
        all_labels = {label_of(component) for component in tunnel_components(circuit)}
        trial = deepcopy(circuit)
        try:
            detunnelize_circuit(
                trial,
                keep_labels=all_labels - {label},
                project=project,
                passes=passes,
            )
        except Exception as exc:
            report["skipped"].append(f"{label}: {exc}")
            continue

        remaining_selected = [
            component
            for component in tunnel_components(trial)
            if label_of(component) == label and tuple(component.loc) in selected_locations
        ]
        if remaining_selected:
            report["skipped"].append(f"{label}: unchanged")
            continue

        trial.components = [
            component
            for component in trial.components
            if not (
                component.name == "Tunnel"
                and label_of(component) == label
                and tuple(component.loc) in restored_locations
            )
        ]
        trial.components.extend(restored)

        if check_widths:
            from .diagnostics import find_width_conflicts

            conflicts = find_width_conflicts(trial, project=project)
            if conflicts:
                report["skipped"].append(f"{label}: width conflicts {len(conflicts)}")
                continue

        circuit.components = trial.components
        circuit.wires = trial.wires
        report["removed"].append(label)

    return report


def _wire_adjacency(circuit: RawCircuit) -> dict[tuple[int, int], set[tuple[int, int]]]:
    adjacency: dict[tuple[int, int], set[tuple[int, int]]] = defaultdict(set)
    for wire in circuit.wires:
        adjacency[wire.start].add(wire.end)
        adjacency[wire.end].add(wire.start)
    return adjacency


def _tunnel_anchor_point(
    tunnel: RawComponent,
    *,
    adjacency: dict[tuple[int, int], set[tuple[int, int]]],
    blocked: set[tuple[int, int]],
    grid: int,
) -> tuple[int, int] | None:
    loc = tuple(tunnel.loc)
    neighbors = sorted(
        adjacency.get(loc, set()),
        key=lambda point: (abs(point[0] - loc[0]) + abs(point[1] - loc[1]), point[1], point[0]),
    )
    if not neighbors:
        return None
    neighbor = neighbors[0]
    dx = loc[0] - neighbor[0]
    dy = loc[1] - neighbor[1]
    step_x = 0 if dx == 0 else (grid if dx > 0 else -grid)
    step_y = 0 if dy == 0 else (grid if dy > 0 else -grid)
    if step_x == 0 and step_y == 0:
        return None
    for distance in range(grid * 4, grid * 16 + 1, grid * 2):
        candidate = (loc[0] + (distance // grid) * step_x, loc[1] + (distance // grid) * step_y)
        if candidate not in blocked:
            return candidate
    return (loc[0] + 12 * step_x, loc[1] + 12 * step_y)


def _detunnelize_remaining_groups(
    circuit: RawCircuit,
    *,
    preserved: set[str],
    project: RawProject | None = None,
) -> None:
    tunnels = [component for component in circuit.components if component.name == "Tunnel"]
    if not tunnels:
        return
    builder = LogicCircuitBuilder(f"{circuit.name}__detunnel2", project=project, allow_tunnel_fallback=True)
    blocked_components: dict[str, RawComponent] = {}
    blocked_geometries = {}
    for index, component in enumerate(circuit.components):
        if component.name in {"Tunnel", "Text"}:
            continue
        try:
            geometry = get_component_geometry(component, project=project)
        except Exception:
            continue
        key = f"c{index:x}"
        blocked_components[key] = component
        blocked_geometries[key] = geometry
    blocked = builder._component_blockers(blocked_components, blocked_geometries) if blocked_components else set()
    adjacency = _wire_adjacency(circuit)
    current_wires = list(circuit.wires)
    current_occupancy = _occupancy_from_wires(current_wires, builder.grid)
    component_boxes = [component_bounds(component) for component in circuit.components if component.name not in {"Tunnel", "Text"}]
    if component_boxes:
        min_x = min(box[0] for box in component_boxes)
        min_y = min(box[1] for box in component_boxes)
        max_x = max(box[0] + box[2] for box in component_boxes)
        max_y = max(box[1] + box[3] for box in component_boxes)
    else:
        min_x = min_y = -200
        max_x = max_y = 200
    grouped: defaultdict[str, list[RawComponent]] = defaultdict(list)
    for tunnel in tunnels:
        grouped[tunnel.get("label", "") or ""].append(tunnel)

    remaining_components: list[RawComponent] = []
    fallback_index = 0
    for label, components in sorted(grouped.items()):
        if label in preserved or len(components) < 2:
            remaining_components.extend(components)
            continue
        anchor_points: list[tuple[int, int]] = []
        spoke_wires: list[RawWire] = []
        failed = False
        for tunnel in components:
            anchor = _tunnel_anchor_point(tunnel, adjacency=adjacency, blocked=blocked, grid=builder.grid)
            loc = tuple(tunnel.loc)
            if anchor is None:
                anchor_points.append(loc)
                continue
            path = [loc, anchor]
            clear_with_blockers = builder._path_is_clear_for_routing(
                path,
                goals={anchor},
                blocked=blocked,
                occupancy=current_occupancy,
                allowed_vertices={loc, anchor},
            )
            clear_relaxed = clear_with_blockers or builder._path_is_clear_for_routing(
                path,
                goals={anchor},
                blocked=set(),
                occupancy=current_occupancy,
                allowed_vertices={loc, anchor},
            )
            if not clear_relaxed:
                anchor_points.append(loc)
                continue
            new_wires = list(_points_to_wires(path))
            if not builder._wires_fit_occupancy(new_wires, current_occupancy, allowed_vertices={loc, anchor}):
                anchor_points.append(loc)
                continue
            anchor_points.append(anchor)
            spoke_wires.extend(new_wires)
        if failed:
            remaining_components.extend(components)
            continue
        active_occupancy = current_occupancy.merged(_occupancy_from_wires(spoke_wires, builder.grid))
        connector = builder._plan_free_point_group(points=anchor_points, blocked=blocked, occupancy=active_occupancy)
        connector_allowed = set(anchor_points)
        fallback_connector = False
        if connector is None:
            connector = builder._plan_free_point_group(points=anchor_points, blocked=set(), occupancy=active_occupancy)
        if connector is None or not builder._wires_fit_occupancy(connector, active_occupancy, allowed_vertices=set(anchor_points)):
            gap = builder.grid * (6 + fallback_index * 3)
            fallback_index += 1
            connector = None
            for channel_y in (min_y - gap, max_y + gap):
                fallback_wires = []
                channel_points: list[tuple[int, int]] = []
                for anchor in anchor_points:
                    channel_point = (anchor[0], channel_y)
                    candidate = list(_points_to_wires([anchor, channel_point]))
                    if not builder._wires_fit_occupancy(
                        candidate,
                        current_occupancy,
                        allowed_vertices={anchor, channel_point},
                    ):
                        fallback_wires = []
                        break
                    fallback_wires.extend(candidate)
                    channel_points.append(channel_point)
                if len(channel_points) < 2:
                    continue
                trunk = list(_points_to_wires([(min(point[0] for point in channel_points), channel_y), (max(point[0] for point in channel_points), channel_y)]))
                active_outer = current_occupancy.merged(_occupancy_from_wires(fallback_wires, builder.grid))
                if not builder._wires_fit_occupancy(trunk, active_outer, allowed_vertices=set(channel_points)):
                    continue
                connector = [*fallback_wires, *trunk]
                connector_allowed = set(anchor_points) | set(channel_points)
                fallback_connector = True
                break
        if connector is None:
            remaining_components.extend(components)
            continue
        if not fallback_connector and not builder._wires_fit_occupancy(connector, active_occupancy, allowed_vertices=connector_allowed):
            remaining_components.extend(components)
            continue
        added = [*spoke_wires, *connector]
        current_wires.extend(added)
        current_occupancy = current_occupancy.merged(_occupancy_from_wires(added, builder.grid))
    circuit.components = [component for component in circuit.components if component.name != "Tunnel"] + remaining_components
    circuit.wires = current_wires


def snap10(value: int) -> int:
    return round(value / 10) * 10


def clone_circuit(circuit: RawCircuit, *, name: str | None = None) -> RawCircuit:
    copied = deepcopy(circuit)
    if name is not None:
        copied.name = name
        if copied.get("circuit") is not None:
            copied.set("circuit", name)
    return copied


def circuit_shell(base: RawCircuit, *, name: str | None = None, keep_names: set[str] | None = None) -> RawCircuit:
    keep = {"Pin", "Text"} if keep_names is None else keep_names
    circuit = clone_circuit(base, name=name)
    circuit.components = [deepcopy(comp) for comp in base.components if comp.name in keep]
    circuit.wires = []
    circuit.appearances = []
    circuit.item_order = []
    return circuit


def preserve_base_appearance(base: RawCircuit, circuit: RawCircuit) -> None:
    circuit.appearances = deepcopy(base.appearances)
    circuit.item_order = [item for item in base.item_order if item[0] in {"attr", "appear", "other"}]
    _rebind_appearance_ports(base, circuit)


def _pin_signature(component: RawComponent) -> tuple[str, str, str]:
    return (
        component.get("facing", "east") or "east",
        component.get("output", "false") or "false",
        component.name,
    )


def _pin_label(component: RawComponent) -> str:
    return (component.get("label", "") or "").strip()


def _match_pin_components(base_components: list[RawComponent], current_components: list[RawComponent]) -> list[tuple[RawComponent, RawComponent]]:
    remaining_base = list(base_components)
    remaining_current = list(current_components)
    matches: list[tuple[RawComponent, RawComponent]] = []

    def pop_pair(base_component: RawComponent, current_component: RawComponent) -> None:
        remaining_base.remove(base_component)
        remaining_current.remove(current_component)
        matches.append((base_component, current_component))

    label_pairs: list[tuple[RawComponent, RawComponent]] = []
    base_by_label: defaultdict[str, list[RawComponent]] = defaultdict(list)
    current_by_label: defaultdict[str, list[RawComponent]] = defaultdict(list)
    for component in remaining_base:
        label = _pin_label(component)
        if label:
            base_by_label[label].append(component)
    for component in remaining_current:
        label = _pin_label(component)
        if label:
            current_by_label[label].append(component)
    for label, base_labeled in base_by_label.items():
        current_labeled = current_by_label.get(label, [])
        if len(base_labeled) != 1 or len(current_labeled) != 1:
            continue
        label_pairs.append((base_labeled[0], current_labeled[0]))
    for base_component, current_component in label_pairs:
        if base_component in remaining_base and current_component in remaining_current:
            pop_pair(base_component, current_component)

    width_pairs: list[tuple[RawComponent, RawComponent]] = []
    base_by_width: defaultdict[str, list[RawComponent]] = defaultdict(list)
    current_by_width: defaultdict[str, list[RawComponent]] = defaultdict(list)
    for component in remaining_base:
        width = component.get("width")
        if width:
            base_by_width[width].append(component)
    for component in remaining_current:
        width = component.get("width")
        if width:
            current_by_width[width].append(component)
    for width, base_width_group in base_by_width.items():
        current_width_group = current_by_width.get(width, [])
        if len(base_width_group) != 1 or len(current_width_group) != 1:
            continue
        width_pairs.append((base_width_group[0], current_width_group[0]))
    for base_component, current_component in width_pairs:
        if base_component in remaining_base and current_component in remaining_current:
            pop_pair(base_component, current_component)

    remaining_base.sort(key=lambda component: (component.loc[0], component.loc[1]))
    remaining_current.sort(key=lambda component: (component.loc[0], component.loc[1]))
    for base_component, current_component in zip(remaining_base, remaining_current):
        matches.append((base_component, current_component))
    return matches


def _rebind_appearance_ports(base: RawCircuit, circuit: RawCircuit) -> None:
    base_groups: defaultdict[tuple[str, str, str], list[RawComponent]] = defaultdict(list)
    current_groups: defaultdict[tuple[str, str, str], list[RawComponent]] = defaultdict(list)
    for component in base.pin_components():
        base_groups[_pin_signature(component)].append(component)
    for component in circuit.pin_components():
        current_groups[_pin_signature(component)].append(component)

    location_map: dict[tuple[int, int], tuple[int, int]] = {}
    for signature, base_components in base_groups.items():
        current_components = current_groups.get(signature, [])
        if len(base_components) != len(current_components):
            continue
        for base_component, current_component in _match_pin_components(base_components, current_components):
            location_map[tuple(base_component.loc)] = tuple(current_component.loc)

    for appearance in circuit.appearances:
        for shape in appearance.shapes:
            if shape.tag != "circ-port":
                continue
            pin_text = shape.attrs.get("pin")
            if not pin_text:
                continue
            raw = pin_text.strip()
            if raw.startswith("(") and raw.endswith(")"):
                raw = raw[1:-1]
            try:
                old_loc = tuple(int(part.strip()) for part in raw.split(",", 1))
            except Exception:
                continue
            new_loc = location_map.get(old_loc)
            if new_loc is None:
                continue
            shape.attrs["pin"] = f"{new_loc[0]},{new_loc[1]}"


def translate_circuit(circuit: RawCircuit, *, dx: int, dy: int) -> None:
    if dx == 0 and dy == 0:
        return
    for component in circuit.components:
        component.loc = (component.loc[0] + dx, component.loc[1] + dy)
    for wire in circuit.wires:
        wire.start = (wire.start[0] + dx, wire.start[1] + dy)
        wire.end = (wire.end[0] + dx, wire.end[1] + dy)


def align_circuit_pins_to_base(base: RawCircuit, circuit: RawCircuit) -> tuple[int, int] | None:
    base_ports = {port.name: port.pin_loc for port in base.port_offsets()}
    current_ports = {port.name: port.pin_loc for port in circuit.port_offsets()}
    shared = [name for name in base_ports if name in current_ports]
    if not shared:
        return None
    deltas = Counter(
        (
            base_ports[name][0] - current_ports[name][0],
            base_ports[name][1] - current_ports[name][1],
        )
        for name in shared
    )
    (dx, dy), count = deltas.most_common(1)[0]
    required = len(shared) if len(shared) <= 3 else max(2, (2 * len(shared) + 2) // 3)
    if count < required:
        return None
    translate_circuit(circuit, dx=dx, dy=dy)
    return (dx, dy)


def normalize_circuit_to_padding(
    circuit: RawCircuit,
    *,
    project: RawProject | None = None,
    grid: int = 10,
    padding: int = 20,
) -> tuple[int, int]:
    points: list[tuple[int, int]] = []
    for component in circuit.components:
        x, y, wid, ht = component_bounds(component, project=project)
        points.append((x, y))
        points.append((x + wid, y + ht))
        points.append(tuple(component.loc))
    for wire in circuit.wires:
        points.append(tuple(wire.start))
        points.append(tuple(wire.end))
    if not points:
        return (0, 0)
    min_x = min(point[0] for point in points)
    min_y = min(point[1] for point in points)
    shift_x = max(0, ((padding - min_x + grid - 1) // grid) * grid)
    shift_y = max(0, ((padding - min_y + grid - 1) // grid) * grid)
    if shift_x == 0 and shift_y == 0:
        return (0, 0)
    translate_circuit(circuit, dx=shift_x, dy=shift_y)
    return (shift_x, shift_y)


def normalize_project_root_circuits_to_padding(
    project: RawProject,
    *,
    grid: int = 10,
    padding: int = 20,
) -> dict[str, tuple[int, int]]:
    circuit_names = {circuit.name for circuit in project.circuits}
    referenced: set[str] = set()
    for circuit in project.circuits:
        for component in circuit.components:
            if component.lib is None and component.name in circuit_names:
                referenced.add(component.name)
    shifts: dict[str, tuple[int, int]] = {}
    for circuit in project.circuits:
        if circuit.name in referenced:
            continue
        dx, dy = normalize_circuit_to_padding(circuit, project=project, grid=grid, padding=padding)
        if dx or dy:
            shifts[circuit.name] = (dx, dy)
    return shifts


def merge_logic_circuit(base: RawCircuit, built: RawCircuit, *, name: str, keep_names: set[str] | None = None) -> RawCircuit:
    circuit = circuit_shell(base, name=name, keep_names={"Text"} if keep_names is None else keep_names)
    circuit.components.extend(deepcopy(comp) for comp in built.components)
    circuit.wires.extend(deepcopy(wire) for wire in built.wires)
    return circuit


def get_attr(comp: RawComponent, name: str, default: str | None = None) -> str | None:
    for attr in comp.attrs:
        if attr.name == name:
            return attr.value
    return default


def attrs_dict(comp: RawComponent) -> dict[str, str]:
    return {attr.name: attr.value for attr in comp.attrs}


def set_attr(comp: RawComponent, name: str, value: str, *, as_text: bool | None = None) -> None:
    comp.set(name, value, as_text=as_text)


def find_component(circuit: RawCircuit, *, name: str | None = None, loc: tuple[int, int] | None = None) -> RawComponent:
    for comp in circuit.components:
        if name is not None and comp.name != name:
            continue
        if loc is not None and comp.loc != loc:
            continue
        return comp
    raise KeyError((name, loc))


def find_tunnel(circuit: RawCircuit, label: str, loc: tuple[int, int] | None = None) -> RawComponent:
    for comp in circuit.components:
        if comp.name != "Tunnel":
            continue
        if get_attr(comp, "label") != label:
            continue
        if loc is not None and comp.loc != loc:
            continue
        return comp
    raise KeyError((label, loc))


def replace_text_exact(circuit: RawCircuit, old: str, new: str) -> None:
    for comp in circuit.components:
        if comp.name != "Text":
            continue
        if get_attr(comp, "text") == old:
            set_attr(comp, "text", new, as_text=False)


def update_text_contains(circuit: RawCircuit, old: str, new: str) -> None:
    for comp in circuit.components:
        if comp.name != "Text":
            continue
        text = get_attr(comp, "text", "")
        if text and old in text:
            set_attr(comp, "text", text.replace(old, new), as_text=False)


def add_component(circuit: RawCircuit, name: str, loc: tuple[int, int], attrs: dict[str, str], *, lib: str) -> RawComponent:
    comp = RawComponent(
        name=name,
        loc=loc,
        lib=lib,
        attrs=[RawAttribute(name=key, value=value) for key, value in attrs.items()],
    )
    circuit.components.append(comp)
    return comp


def component_template(name: str, attrs: dict[str, str], *, lib: str | None = None) -> RawComponent:
    return RawComponent(
        name=name,
        loc=(0, 0),
        lib=lib,
        attrs=[RawAttribute(name=key, value=value) for key, value in attrs.items()],
    )


def _component_extents(component: RawComponent, *, project: RawProject | None = None) -> tuple[int, int, int, int]:
    return component_extents(component, project=project)


def component_port_point(component: RawComponent, port_name: str, *, project: RawProject | None = None) -> tuple[int, int]:
    geometry = get_component_geometry(component, project=project)
    port = geometry.port(port_name)
    return (component.loc[0] + port.offset[0], component.loc[1] + port.offset[1])


def _nearest_side_name(bounds: tuple[int, int, int, int], offset: tuple[int, int]) -> str:
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


def component_port_lead(
    component: RawComponent,
    port_name: str,
    *,
    distance: int = 20,
    project: RawProject | None = None,
) -> tuple[tuple[int, int], tuple[int, int]]:
    geometry = get_component_geometry(component, project=project)
    port = geometry.port(port_name)
    port_point = (component.loc[0] + port.offset[0], component.loc[1] + port.offset[1])
    port_side = _nearest_side_name(geometry.bounds, port.offset)
    same_side_ports = 0
    for other in geometry.ports:
        other_side = _nearest_side_name(geometry.bounds, other.offset)
        if other_side == port_side:
            same_side_ports += 1
    lead_distance = distance
    if same_side_ports >= 9:
        lead_distance = max(lead_distance, 40)
    elif same_side_ports >= 4:
        lead_distance = max(lead_distance, 30)
    side = {
        "left": (-lead_distance, 0),
        "right": (lead_distance, 0),
        "top": (0, -lead_distance),
        "bottom": (0, lead_distance),
    }[port_side]
    return port_point, (port_point[0] + side[0], port_point[1] + side[1])


def _lead_facing(port_point: tuple[int, int], lead_point: tuple[int, int]) -> str:
    if lead_point[0] < port_point[0]:
        return "east"
    if lead_point[0] > port_point[0]:
        return "west"
    if lead_point[1] < port_point[1]:
        return "south"
    return "north"


def _tunnel_attrs(label: str, width: int, *, facing: str) -> dict[str, str]:
    return {
        "facing": facing,
        "width": str(width),
        "label": label,
        "labelfont": "Dialog plain 12",
    }


def _preferred_tunnel_distance(
    component: RawComponent,
    port_name: str,
    *,
    project: RawProject | None = None,
) -> int:
    port_point, lead_point = component_port_lead(component, port_name, project=project)
    return max(30, abs(port_point[0] - lead_point[0]) + abs(port_point[1] - lead_point[1]))


def _tunnel_snap10(value: int) -> int:
    return ((value + 9) // 10) * 10


def _find_port_at_point(
    circuit: RawCircuit,
    loc: tuple[int, int],
    *,
    project: RawProject | None = None,
) -> tuple[RawComponent, str] | None:
    for component in reversed(circuit.components):
        if component.name in {"Text", "Tunnel"}:
            continue
        try:
            geometry = get_component_geometry(component, project=project)
        except Exception:
            continue
        for port in geometry.ports:
            point = (component.loc[0] + port.offset[0], component.loc[1] + port.offset[1])
            if point == loc:
                return component, port.name
    return None


def _virtual_pin_at(loc: tuple[int, int], *, tunnel_facing: str) -> RawComponent:
    pin = component_template(
        "Pin",
        {
            "facing": _OPPOSITE_FACING.get(tunnel_facing, "west"),
            "output": "false",
            "width": "1",
            "tristate": "false",
            "pull": "none",
            "label": "",
            "labelloc": "north",
            "labelfont": "Dialog plain 12",
            "labelcolor": "#000000",
        },
        lib="0",
    )
    pin.loc = loc
    return pin


def _is_center_port(
    component: RawComponent,
    port_name: str,
    *,
    project: RawProject | None = None,
) -> bool:
    try:
        geometry = get_component_geometry(component, project=project)
    except Exception:
        return False
    for port in geometry.ports:
        if port.name == port_name:
            return port.offset == (0, 0)
    return False


def _component_port_width(
    component: RawComponent,
    port_name: str,
    *,
    project: RawProject | None = None,
) -> int:
    geometry = get_component_geometry(component, project=project)
    port = geometry.port(port_name)
    try:
        return int(port.width or 1)
    except Exception:
        return 1


def _tunnel_slot_step(
    label: str,
    width: int,
    *,
    facing: str,
    project: RawProject | None = None,
) -> int:
    tunnel = component_template("Tunnel", _tunnel_attrs(label, width, facing=facing), lib="0")
    left, right, top, bottom = component_extents(tunnel, project=project)
    span = top + bottom if facing in {"east", "west"} else left + right
    return max(20, _tunnel_snap10(span + 10))


def _slot_candidates(step: int, *, limit: int = 240) -> tuple[int, ...]:
    values = [0]
    current = step
    while current <= limit:
        values.extend((current, -current))
        current += step
    return tuple(values)


def _reserve_tunnel_slot(key: tuple[object, ...], step: int) -> int:
    used = _TUNNEL_SLOT_STATE[key]
    for offset in _slot_candidates(step):
        if offset in used:
            continue
        used.add(offset)
        return offset
    offset = step * (len(used) + 1)
    used.add(offset)
    return offset


def _preferred_slot_offsets(preferred: int, step: int, *, limit: int = 240) -> tuple[int, ...]:
    seen: set[int] = set()
    values: list[int] = []
    for base in _slot_candidates(step, limit=limit):
        candidate = preferred + base
        if candidate in seen or candidate < -limit or candidate > limit:
            continue
        seen.add(candidate)
        values.append(candidate)
    return tuple(values)


def _wire_points(start: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int]]:
    if start[0] != end[0] and start[1] != end[1]:
        raise ValueError(f"non-orthogonal wire segment: {start} -> {end}")
    if start[0] == end[0]:
        step = 10 if end[1] >= start[1] else -10
        return [(start[0], y) for y in range(start[1], end[1] + step, step)]
    step = 10 if end[0] >= start[0] else -10
    return [(x, start[1]) for x in range(start[0], end[0] + step, step)]


def _path_points(path: list[tuple[int, int]]) -> list[tuple[int, int]]:
    points: list[tuple[int, int]] = []
    for index in range(len(path) - 1):
        segment = _wire_points(path[index], path[index + 1])
        if index:
            segment = segment[1:]
        points.extend(segment)
    return points


def _occupied_attachment_points(
    circuit: RawCircuit,
    *,
    project: RawProject | None = None,
) -> set[tuple[int, int]]:
    points: set[tuple[int, int]] = set()
    for wire in circuit.wires:
        points.update(_wire_points(tuple(wire.start), tuple(wire.end)))
    for component in circuit.components:
        if component.name == "Text":
            continue
        try:
            geometry = get_component_geometry(component, project=project)
        except Exception:
            continue
        for port in geometry.ports:
            points.add((component.loc[0] + port.offset[0], component.loc[1] + port.offset[1]))
    return points


def _center_port_tunnel(
    circuit: RawCircuit,
    component: RawComponent,
    port_name: str,
    label: str,
    width: int,
    *,
    facing: str,
    project: RawProject | None = None,
) -> RawComponent:
    port_x, port_y = component_port_point(component, port_name, project=project)
    left, top, comp_width, comp_height = component_bounds(component, project=project)
    right = left + comp_width
    bottom = top + comp_height
    attrs = _tunnel_attrs(label, width, facing=facing)
    tunnel_template = component_template("Tunnel", attrs, lib="0")
    t_left, t_right, t_top, t_bottom = component_extents(tunnel_template, project=project)
    slot_step = _tunnel_slot_step(label, width, facing=facing, project=project)
    preferred_offset = _reserve_tunnel_slot((id(circuit), id(component), port_name, facing), slot_step)
    clearance = 20
    stub = 10
    others = [other for other in circuit.components if other.name != "Text"]

    if port_x == left:
        place_side = "west"
    elif port_x == right:
        place_side = "east"
    elif port_y == top:
        place_side = "north"
    elif port_y == bottom:
        place_side = "south"
    else:
        place_side = _OPPOSITE_FACING.get(facing, facing)

    def overlaps(loc: tuple[int, int]) -> bool:
        candidate = component_template("Tunnel", attrs, lib="0")
        candidate.loc = loc
        candidate_bounds = component_bounds(candidate, project=project)
        for other in others:
            other_bounds = component_bounds(other, project=project)
            if max(candidate_bounds[0], other_bounds[0]) < min(candidate_bounds[0] + candidate_bounds[2], other_bounds[0] + other_bounds[2]) and max(candidate_bounds[1], other_bounds[1]) < min(candidate_bounds[1] + candidate_bounds[3], other_bounds[1] + other_bounds[3]):
                return True
        return False

    chosen: tuple[tuple[int, int], int] | None = None
    for tangent_offset in _preferred_slot_offsets(preferred_offset, slot_step):
        if place_side == "east":
            loc = (right + clearance + t_left, port_y + tangent_offset)
        elif place_side == "west":
            loc = (left - clearance - t_right, port_y + tangent_offset)
        elif place_side == "north":
            loc = (port_x + tangent_offset, top - clearance - t_bottom)
        else:
            loc = (port_x + tangent_offset, bottom + clearance + t_top)
        loc = (_tunnel_snap10(loc[0]), _tunnel_snap10(loc[1]))
        if not overlaps(loc):
            chosen = (loc, tangent_offset)
            break

    if chosen is None:
        tangent_offset = preferred_offset
        if place_side == "east":
            loc = (right + clearance + t_left, port_y + tangent_offset)
        elif place_side == "west":
            loc = (left - clearance - t_right, port_y + tangent_offset)
        elif place_side == "north":
            loc = (port_x + tangent_offset, top - clearance - t_bottom)
        else:
            loc = (port_x + tangent_offset, bottom + clearance + t_top)
        loc = (_tunnel_snap10(loc[0]), _tunnel_snap10(loc[1]))
        chosen = (loc, tangent_offset)

    loc, tangent_offset = chosen
    tunnel = add_component(circuit, "Tunnel", loc, attrs, lib="0")

    if place_side in {"east", "west"}:
        exit_x = _tunnel_snap10(right + stub if place_side == "east" else left - stub)
        bend = (exit_x, _tunnel_snap10(port_y + tangent_offset))
        path = [(port_x, port_y), (exit_x, port_y), bend, loc]
    else:
        exit_y = _tunnel_snap10(top - stub if place_side == "north" else bottom + stub)
        bend = (_tunnel_snap10(port_x + tangent_offset), exit_y)
        path = [(port_x, port_y), (port_x, exit_y), bend, loc]
    add_polyline(circuit, path)
    return tunnel


def _hang_tunnel_on_port(
    circuit: RawCircuit,
    component: RawComponent,
    port_name: str,
    label: str,
    width: int,
    *,
    facing: str,
    preferred_offset: int = 0,
    project: RawProject | None = None,
) -> RawComponent:
    port_point, far_point = component_port_lead(component, port_name, distance=20, project=project)
    dx = far_point[0] - port_point[0]
    dy = far_point[1] - port_point[1]
    if dx != 0:
        normal = (1 if dx > 0 else -1, 0)
        tangent = (0, 1)
    elif dy != 0:
        normal = (0, 1 if dy > 0 else -1)
        tangent = (1, 0)
    else:
        normal = {
            "west": (1, 0),
            "east": (-1, 0),
            "north": (0, 1),
            "south": (0, -1),
        }.get(facing, (1, 0))
        tangent = (0, 1) if normal[0] else (1, 0)

    occupied = _occupied_attachment_points(circuit, project=project)
    preferred = _tunnel_snap10(preferred_offset)
    offset_candidates = [0]
    if preferred != 0:
        offset_candidates.append(preferred)
    for step in range(10, 90, 10):
        offset_candidates.extend((step, -step))
    seen: set[int] = set()
    offset_candidates = [value for value in offset_candidates if not (value in seen or seen.add(value))]

    for lead_distance in (10, 20, 30, 40):
        lead_point = (
            port_point[0] + normal[0] * lead_distance,
            port_point[1] + normal[1] * lead_distance,
        )
        for offset in offset_candidates:
            loc = (
                lead_point[0] + tangent[0] * offset,
                lead_point[1] + tangent[1] * offset,
            )
            loc = (_tunnel_snap10(loc[0]), _tunnel_snap10(loc[1]))
            path = [port_point, lead_point] if loc == lead_point else [port_point, lead_point, loc]
            if any(point != port_point and point in occupied for point in _path_points(path)):
                continue
            tunnel = add_component(circuit, "Tunnel", loc, _tunnel_attrs(label, width, facing=facing), lib="0")
            add_polyline(circuit, path)
            return tunnel

    lead_point = (
        port_point[0] + normal[0] * 10,
        port_point[1] + normal[1] * 10,
    )
    loc = (
        _tunnel_snap10(lead_point[0] + tangent[0] * preferred),
        _tunnel_snap10(lead_point[1] + tangent[1] * preferred),
    )
    path = [port_point, lead_point] if loc == lead_point else [port_point, lead_point, loc]
    tunnel = add_component(circuit, "Tunnel", loc, _tunnel_attrs(label, width, facing=facing), lib="0")
    add_polyline(circuit, path)
    return tunnel


def add_tunnel_to_port(
    circuit: RawCircuit,
    component: RawComponent,
    port_name: str,
    label: str,
    width: int,
    *,
    project: RawProject | None = None,
    facing: str | None = None,
) -> tuple[int, int]:
    resolved_facing = attachment_facing_for_port(component, port_name, project=project)
    if _is_center_port(component, port_name, project=project):
        tunnel = _center_port_tunnel(
            circuit,
            component,
            port_name,
            label,
            width,
            facing=facing or resolved_facing,
            project=project,
        )
        return tunnel.loc
    preferred_offset = _reserve_tunnel_slot(
        (id(circuit), id(component), resolved_facing),
        _tunnel_slot_step(label, width, facing=resolved_facing, project=project),
    )
    tunnel = _hang_tunnel_on_port(
        circuit,
        component,
        port_name,
        label,
        width,
        facing=resolved_facing,
        preferred_offset=preferred_offset,
        project=project,
    )
    return tunnel.loc


def add_tunnel_on_port(
    circuit: RawCircuit,
    component: RawComponent,
    port_name: str,
    label: str,
    *,
    facing: str | None = None,
    project: RawProject | None = None,
) -> tuple[int, int]:
    width = _component_port_width(component, port_name, project=project)
    return add_tunnel_to_port(
        circuit,
        component,
        port_name,
        label,
        width,
        project=project,
        facing=facing,
    )


def add_constant_to_port(
    circuit: RawCircuit,
    component: RawComponent,
    port_name: str,
    *,
    width: int,
    value: int | str,
    project: RawProject | None = None,
) -> tuple[int, int]:
    port_point, lead_point = component_port_lead(component, port_name, project=project)
    add_constant(circuit, lead_point, width=width, value=value, facing=_lead_facing(port_point, lead_point))
    add_wire(circuit, lead_point, port_point)
    return lead_point


def add_wire(circuit: RawCircuit, start: tuple[int, int], end: tuple[int, int]) -> RawWire:
    wire = RawWire(start=start, end=end)
    circuit.wires.append(wire)
    return wire


def add_polyline(circuit: RawCircuit, points: list[tuple[int, int]] | tuple[tuple[int, int], ...]) -> list[RawWire]:
    compact: list[tuple[int, int]] = []
    for point in points:
        if compact and point == compact[-1]:
            continue
        compact.append(point)
    orthogonal: list[tuple[int, int]] = []
    for point in compact:
        if not orthogonal:
            orthogonal.append(point)
            continue
        start = orthogonal[-1]
        if start[0] != point[0] and start[1] != point[1]:
            bend = (point[0], start[1])
            if bend != start and bend != point:
                orthogonal.append(bend)
        if point != orthogonal[-1]:
            orthogonal.append(point)
    added: list[RawWire] = []
    for start, end in zip(orthogonal, orthogonal[1:], strict=False):
        wire = RawWire(start=start, end=end)
        circuit.wires.append(wire)
        added.append(wire)
    return added


def _port_guard_points(
    circuit: RawCircuit,
    *,
    project: RawProject | None = None,
    exclude: set[tuple[int, int]] | None = None,
    grid: int = 10,
    steps: int = 4,
) -> set[tuple[int, int]]:
    excluded = set() if exclude is None else set(exclude)
    guarded: set[tuple[int, int]] = set()
    side_vectors = {
        "left": (-grid, 0),
        "right": (grid, 0),
        "top": (0, -grid),
        "bottom": (0, grid),
    }
    for component in circuit.components:
        if component.name in {"Text", "Tunnel"}:
            continue
        try:
            geometry = get_component_geometry(component, project=project)
        except Exception:
            continue
        for port in geometry.ports:
            point = (component.loc[0] + port.offset[0], component.loc[1] + port.offset[1])
            if point in excluded:
                continue
            guarded.add(point)
            dx, dy = side_vectors[_nearest_side_name(geometry.bounds, port.offset)]
            for step in range(1, steps + 1):
                guarded.add((point[0] + dx * step, point[1] + dy * step))
    return guarded


def _route_between_points(
    circuit: RawCircuit,
    start: tuple[int, int],
    end: tuple[int, int],
    *,
    project: RawProject | None = None,
    extra_blocked: set[tuple[int, int]] | None = None,
    extra_allowed: set[tuple[int, int]] | None = None,
    margin: int = 160,
    component_padding: int = 10,
    avoid_wires: bool = False,
) -> tuple[tuple[int, int], ...]:
    path = route_circuit_path(
        circuit,
        start,
        end,
        project=project,
        margin=margin,
        component_padding=component_padding,
        avoid_wires=avoid_wires,
        extra_blocked=extra_blocked,
        extra_allowed={start, end, *(extra_allowed or set())},
    )
    if path is None:
        raise RuntimeError(f"failed to route wire: {start} -> {end}")
    return path


def connect_points_routed(
    circuit: RawCircuit,
    start: tuple[int, int],
    end: tuple[int, int],
    *,
    project: RawProject | None = None,
    via: list[tuple[int, int]] | tuple[tuple[int, int], ...] = (),
    margin: int = 160,
    component_padding: int = 10,
    avoid_wires: bool = False,
    protect_ports: bool = True,
) -> list[RawWire]:
    waypoints = [tuple(start), *[tuple(point) for point in via], tuple(end)]
    allowed = set(waypoints)
    blocked = _port_guard_points(circuit, project=project, exclude=allowed) if protect_ports else set()
    routed = [waypoints[0]]
    for target in waypoints[1:]:
        path = _route_between_points(
            circuit,
            routed[-1],
            target,
            project=project,
            extra_blocked=blocked,
            extra_allowed=allowed,
            margin=margin,
            component_padding=component_padding,
            avoid_wires=avoid_wires,
        )
        routed.extend(path[1:])
    return add_polyline(circuit, routed)


def _port_lead_candidates(
    component: RawComponent,
    port_name: str,
    *,
    target: tuple[int, int],
    distance: int,
    project: RawProject | None = None,
) -> tuple[tuple[int, int], list[tuple[int, int]]]:
    port_point = component_port_point(component, port_name, project=project)
    default_point, default_lead = component_port_lead(component, port_name, distance=distance, project=project)
    candidates = [default_lead]
    try:
        geometry = get_component_geometry(component, project=project)
        port = geometry.port(port_name)
    except Exception:
        return default_point, candidates

    # Some Logisim components, notably Negator, expose their useful terminal as
    # a single center port.  For those, "nearest side" is ambiguous; choose an
    # escape direction based on the connection target and keep the other
    # directions as fallbacks.
    if len(geometry.ports) == 1 and port.offset == (0, 0):
        dx = target[0] - port_point[0]
        dy = target[1] - port_point[1]
        preferred: list[tuple[int, int]] = []
        if abs(dx) >= abs(dy) and dx != 0:
            preferred.append((1 if dx > 0 else -1, 0))
        if dy != 0:
            preferred.append((0, 1 if dy > 0 else -1))
        for direction in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            if direction not in preferred:
                preferred.append(direction)
        for direction in preferred:
            lead = (port_point[0] + direction[0] * distance, port_point[1] + direction[1] * distance)
            if lead not in candidates:
                candidates.append(lead)

    return port_point, candidates


def connect_ports_routed(
    circuit: RawCircuit,
    src_component: RawComponent,
    src_port: str,
    dst_component: RawComponent,
    dst_port: str,
    *,
    project: RawProject | None = None,
    lead_distance: int = 30,
    margin: int = 160,
    component_padding: int = 10,
    avoid_wires: bool = False,
) -> list[RawWire]:
    src_point = component_port_point(src_component, src_port, project=project)
    dst_point = component_port_point(dst_component, dst_port, project=project)
    src_point, src_leads = _port_lead_candidates(
        src_component,
        src_port,
        target=dst_point,
        distance=lead_distance,
        project=project,
    )
    dst_point, dst_leads = _port_lead_candidates(
        dst_component,
        dst_port,
        target=src_point,
        distance=lead_distance,
        project=project,
    )
    blocked = _port_guard_points(circuit, project=project, exclude={src_point, dst_point})
    wire_points = _circuit_wire_points(circuit)
    last_failure: tuple[tuple[int, int], tuple[int, int]] | None = None
    avoid_options = [avoid_wires]
    if avoid_wires:
        avoid_options.append(False)
    for avoid_existing_wires in avoid_options:
        for padding in sorted({component_padding, max(0, component_padding // 2), 0}, reverse=True):
            for src_lead in src_leads:
                for dst_lead in dst_leads:
                    if src_lead != src_point and src_lead in wire_points:
                        last_failure = (src_lead, dst_lead)
                        continue
                    if dst_lead != dst_point and dst_lead in wire_points:
                        last_failure = (src_lead, dst_lead)
                        continue
                    allowed = {src_point, src_lead, dst_point, dst_lead}
                    path = route_circuit_path(
                        circuit,
                        src_lead,
                        dst_lead,
                        project=project,
                        margin=margin,
                        component_padding=padding,
                        avoid_wires=avoid_existing_wires,
                        extra_blocked=blocked,
                        extra_allowed=allowed,
                    )
                    if path is None:
                        last_failure = (src_lead, dst_lead)
                        continue
                    return add_polyline(circuit, [src_point, src_lead, *path[1:-1], dst_lead, dst_point])
    failed_start, failed_end = last_failure or (src_leads[0], dst_leads[0])
    raise RuntimeError(f"failed to route wire: {failed_start} -> {failed_end}")


def _port_point(component: RawComponent, port_name: str, *, project: RawProject | None = None) -> tuple[int, int]:
    geometry = get_component_geometry(component, project=project)
    port = geometry.port(port_name)
    return (component.loc[0] + port.offset[0], component.loc[1] + port.offset[1])


def _tunnel_width(component: RawComponent) -> int:
    raw = component.get("width", "1") or "1"
    try:
        return int(raw)
    except Exception:
        return 1


def _wire_segment_points(wire: RawWire, *, grid: int = 10) -> set[tuple[int, int]]:
    points: set[tuple[int, int]] = set()
    if wire.start[0] == wire.end[0]:
        x = wire.start[0]
        y0, y1 = sorted((wire.start[1], wire.end[1]))
        for y in range(y0, y1 + grid, grid):
            points.add((x, y))
        return points
    if wire.start[1] == wire.end[1]:
        y = wire.start[1]
        x0, x1 = sorted((wire.start[0], wire.end[0]))
        for x in range(x0, x1 + grid, grid):
            points.add((x, y))
        return points
    return {tuple(wire.start), tuple(wire.end)}


def _circuit_wire_points(circuit: RawCircuit, *, grid: int = 10) -> set[tuple[int, int]]:
    points: set[tuple[int, int]] = set()
    for wire in circuit.wires:
        points.update(_wire_segment_points(wire, grid=grid))
    return points


def _connected_wire_points(circuit: RawCircuit, origin: tuple[int, int], *, grid: int = 10) -> set[tuple[int, int]]:
    endpoint_wires: defaultdict[tuple[int, int], list[RawWire]] = defaultdict(list)
    for wire in circuit.wires:
        endpoint_wires[tuple(wire.start)].append(wire)
        endpoint_wires[tuple(wire.end)].append(wire)
    visited_points = {origin}
    visited_wires: set[int] = set()
    allowed = {origin}
    queue = [origin]
    while queue:
        point = queue.pop()
        for wire in endpoint_wires.get(point, []):
            wire_id = id(wire)
            if wire_id in visited_wires:
                continue
            visited_wires.add(wire_id)
            allowed.update(_wire_segment_points(wire, grid=grid))
            other = tuple(wire.end) if tuple(wire.start) == point else tuple(wire.start)
            if other not in visited_points:
                visited_points.add(other)
                queue.append(other)
    return allowed


def _find_reusable_tunnel_connection(
    circuit: RawCircuit,
    anchor: RawComponent,
    port_name: str,
    *,
    label: str,
    width: int,
    project: RawProject | None = None,
    component_padding: int = 12,
    max_path_length: int = 1200,
) -> tuple[RawComponent, tuple[tuple[int, int], ...]] | None:
    start = _port_point(anchor, port_name, project=project)
    candidates: list[tuple[int, int, RawComponent, tuple[tuple[int, int], ...]]] = []
    for component in circuit.components:
        if component.name != "Tunnel":
            continue
        if (component.get("label", "") or "") != label:
            continue
        if _tunnel_width(component) != width:
            continue
        shared_points = _connected_wire_points(circuit, tuple(component.loc))
        near_points = sorted(
            shared_points,
            key=lambda point: (
                abs(point[0] - start[0]) + abs(point[1] - start[1]),
                abs(point[0] - component.loc[0]) + abs(point[1] - component.loc[1]),
                point[1],
                point[0],
            ),
        )
        for goal in near_points[:96]:
            path = route_circuit_path(
                circuit,
                start,
                goal,
                project=project,
                component_padding=component_padding,
                grid=10,
                margin=400,
                extra_allowed=shared_points,
                wire_junction_allowed={start, *shared_points},
            )
            if path is None:
                continue
            path_length = sum(abs(a[0] - b[0]) + abs(a[1] - b[1]) for a, b in zip(path, path[1:], strict=False))
            if path_length > max_path_length:
                continue
            distance = abs(goal[0] - start[0]) + abs(goal[1] - start[1])
            candidates.append((path_length, distance, component, path))
            break
    if not candidates:
        return None
    _, _, tunnel, path = min(candidates, key=lambda item: (item[0], item[1], item[2].loc[1], item[2].loc[0]))
    return tunnel, path


def _remember_attachment(
    circuit: RawCircuit,
    attached: RawComponent,
    anchor: RawComponent,
    port_name: str,
    wires: list[RawWire],
    *,
    attached_port_name: str | None,
    follow_port_facing: bool,
) -> None:
    _ATTACHMENTS[id(circuit)].append(
        {
            "attached": attached,
            "anchor": anchor,
            "port_name": port_name,
            "attached_port_name": attached_port_name,
            "follow_port_facing": follow_port_facing,
            "wires": wires,
        }
    )


def _remember_bus_tunnel(
    circuit: RawCircuit,
    tunnel: RawComponent,
    *,
    start_x: int,
    end_x: int,
    y: int,
    offset: int,
    bus_width: int,
    wire: RawWire,
) -> None:
    _BUS_TUNNELS[id(circuit)].append(
        {
            "tunnel": tunnel,
            "start_x": start_x,
            "end_x": end_x,
            "y": y,
            "offset": offset,
            "bus_width": bus_width,
            "wire": wire,
        }
    )


def reflow_attached_components(circuit: RawCircuit, *, project: RawProject | None = None) -> None:
    attachments = _ATTACHMENTS.get(id(circuit), [])
    for entry in attachments:
        attached = entry["attached"]
        anchor = entry["anchor"]
        port_name = entry["port_name"]
        attached_port_name = entry.get("attached_port_name")
        follow_port_facing = bool(entry.get("follow_port_facing", True))
        wires = entry["wires"]
        if not isinstance(attached, RawComponent) or not isinstance(anchor, RawComponent) or not isinstance(port_name, str):
            continue
        for wire in list(wires):
            if wire in circuit.wires:
                circuit.wires.remove(wire)
        if follow_port_facing and attached.get("facing") is not None:
            attached.set("facing", attachment_facing_for_port(anchor, port_name, project=project))
        placement = place_attached_component(
            circuit,
            anchor,
            port_name,
            attached,
            attached_port_name=attached_port_name if isinstance(attached_port_name, str) else None,
            project=project,
        )
        attached.loc = placement.loc
        if follow_port_facing and attached.get("facing") is not None:
            attached.set("facing", placement.facing)
        entry["wires"] = add_polyline(circuit, placement.path)


def reflow_bus_tunnels(circuit: RawCircuit) -> None:
    def overlap_count(candidate: RawComponent, ignored: RawComponent) -> int:
        candidate_bounds = component_bounds(candidate)
        total = 0
        for other in circuit.components:
            if other is ignored or other.name == "Text":
                continue
            other_bounds = component_bounds(other)
            left = max(candidate_bounds[0], other_bounds[0])
            top = max(candidate_bounds[1], other_bounds[1])
            right = min(candidate_bounds[0] + candidate_bounds[2], other_bounds[0] + other_bounds[2])
            bottom = min(candidate_bounds[1] + candidate_bounds[3], other_bounds[1] + other_bounds[3])
            if left < right and top < bottom:
                total += 1
        return total

    for entry in _BUS_TUNNELS.get(id(circuit), []):
        tunnel = entry["tunnel"]
        if not isinstance(tunnel, RawComponent):
            continue
        wire = entry["wire"]
        if isinstance(wire, RawWire) and wire in circuit.wires:
            circuit.wires.remove(wire)
        start_x = int(entry["start_x"])
        end_x = int(entry["end_x"])
        y = int(entry["y"])
        offset = int(entry["offset"])
        bus_width = int(entry["bus_width"])
        label = tunnel.get("label", "") or ""
        left_tunnel = RawComponent(
            name="Tunnel",
            loc=(start_x - offset, y),
            lib="0",
            attrs=[
                RawAttribute(name="facing", value="east"),
                RawAttribute(name="width", value=str(bus_width)),
                RawAttribute(name="label", value=label),
                RawAttribute(name="labelfont", value="Dialog plain 12"),
            ],
        )
        right_tunnel = RawComponent(
            name="Tunnel",
            loc=(end_x + offset, y),
            lib="0",
            attrs=[
                RawAttribute(name="facing", value="west"),
                RawAttribute(name="width", value=str(bus_width)),
                RawAttribute(name="label", value=label),
                RawAttribute(name="labelfont", value="Dialog plain 12"),
            ],
        )
        left_score = overlap_count(left_tunnel, tunnel)
        right_score = overlap_count(right_tunnel, tunnel)
        chosen = left_tunnel if left_score < right_score else right_tunnel
        tunnel.loc = chosen.loc
        tunnel.set("facing", chosen.get("facing", "west"))
        if tunnel.get("facing") == "east":
            entry["wire"] = add_wire(circuit, tunnel.loc, (end_x, y))
        else:
            entry["wire"] = add_wire(circuit, (start_x, y), tunnel.loc)


def attach_component_to_port(
    circuit: RawCircuit,
    anchor: RawComponent,
    port_name: str,
    name: str,
    attrs: dict[str, str],
    *,
    lib: str | None,
    project: RawProject | None = None,
    distance: int = 20,
    component_padding: int = 10,
    tangent_limit: int = 240,
    distance_limit: int = 240,
    preferred_tangent_offset: int | None = None,
    follow_port_facing: bool = True,
    attached_port_name: str | None = None,
) -> RawComponent:
    attached = component_template(name, attrs, lib=lib)
    if follow_port_facing and attached.get("facing") is not None:
        attached.set("facing", attachment_facing_for_port(anchor, port_name, project=project))
    try:
        placement = place_attached_component(
            circuit,
            anchor,
            port_name,
            attached,
            attached_port_name=attached_port_name,
            project=project,
            distance=distance,
            component_padding=component_padding,
            tangent_limit=tangent_limit,
            distance_limit=distance_limit,
            preferred_tangent_offset=preferred_tangent_offset,
        )
    except RuntimeError:
        if name != "Tunnel":
            raise
        label = attached.get("label", "") or ""
        reusable = _find_reusable_tunnel_connection(
            circuit,
            anchor,
            port_name,
            label=label,
            width=_tunnel_width(attached),
            project=project,
            component_padding=component_padding,
        )
        if reusable is None:
            raise
        tunnel, path = reusable
        add_polyline(circuit, list(path))
        return tunnel
    attached.loc = placement.loc
    if follow_port_facing and attached.get("facing") is not None:
        attached.set("facing", placement.facing)
    circuit.components.append(attached)
    wires = add_polyline(circuit, list(placement.path))
    _remember_attachment(
        circuit,
        attached,
        anchor,
        port_name,
        wires,
        attached_port_name=attached_port_name,
        follow_port_facing=follow_port_facing,
    )
    return attached


def attach_subcircuit_to_port(
    circuit: RawCircuit,
    anchor: RawComponent,
    port_name: str,
    name: str,
    *,
    attached_port_name: str | None = None,
    project: RawProject | None = None,
    distance: int = 20,
    component_padding: int = 10,
    tangent_limit: int = 240,
    distance_limit: int = 240,
) -> RawComponent:
    return attach_component_to_port(
        circuit,
        anchor,
        port_name,
        name,
        dict(SUBCIRCUIT_INSTANCE_ATTRS),
        lib=None,
        attached_port_name=attached_port_name,
        project=project,
        distance=distance,
        component_padding=component_padding,
        tangent_limit=tangent_limit,
        distance_limit=distance_limit,
    )


def attach_bit_extender_to_port(
    circuit: RawCircuit,
    anchor: RawComponent,
    port_name: str,
    *,
    in_width: int,
    out_width: int,
    extend_type: str = "zero",
    project: RawProject | None = None,
    distance: int = 20,
    component_padding: int = 10,
    tangent_limit: int = 240,
    distance_limit: int = 240,
    attached_port_name: str = "in",
) -> RawComponent:
    return attach_component_to_port(
        circuit,
        anchor,
        port_name,
        "Bit Extender",
        {"in_width": str(in_width), "out_width": str(out_width), "type": extend_type},
        lib="0",
        attached_port_name=attached_port_name,
        project=project,
        distance=distance,
        component_padding=component_padding,
        tangent_limit=tangent_limit,
        distance_limit=distance_limit,
    )


def attach_not_gate_to_port(
    circuit: RawCircuit,
    anchor: RawComponent,
    port_name: str,
    *,
    project: RawProject | None = None,
    distance: int = 20,
    component_padding: int = 10,
    tangent_limit: int = 240,
    distance_limit: int = 240,
    attached_port_name: str = "in",
) -> RawComponent:
    return attach_component_to_port(
        circuit,
        anchor,
        port_name,
        "NOT Gate",
        {
            "facing": "east",
            "width": "1",
            "size": "20",
            "out": "01",
            "label": "",
            "labelfont": "Dialog plain 12",
            "labelcolor": "#000000",
        },
        lib="1",
        attached_port_name=attached_port_name,
        project=project,
        distance=distance,
        component_padding=component_padding,
        tangent_limit=tangent_limit,
        distance_limit=distance_limit,
    )


def _scan_offsets(step: int, limit: int) -> list[int]:
    offsets = [0]
    for distance in range(step, limit + step, step):
        offsets.extend((distance, -distance))
    return offsets


def _bounds_overlap_local(first: tuple[int, int, int, int], second: tuple[int, int, int, int]) -> bool:
    first_right = first[0] + first[2]
    second_right = second[0] + second[2]
    first_bottom = first[1] + first[3]
    second_bottom = second[1] + second[3]
    return max(first[0], second[0]) < min(first_right, second_right) and max(first[1], second[1]) < min(first_bottom, second_bottom)


def place_component_near_component(
    circuit: RawCircuit,
    anchor: RawComponent,
    attached: RawComponent,
    *,
    side: str,
    project: RawProject | None = None,
    gap: int = 40,
    align: str = "center",
    component_padding: int = 10,
    scan_step: int = 10,
    scan_limit: int = 600,
    ignore_names: set[str] | None = None,
) -> tuple[int, int]:
    ignored = {"Text"} if ignore_names is None else set(ignore_names)
    anchor_bounds = component_bounds(anchor, project=project, padding=component_padding)
    left_extent, right_extent, top_extent, bottom_extent = component_extents(attached, project=project, padding=component_padding)
    others = [component for component in circuit.components if component is not anchor and component.name not in ignored]

    def loc_for(extra_gap: int, offset: int) -> tuple[int, int]:
        actual_gap = gap + extra_gap
        if side == "right":
            loc_x = snap10(anchor_bounds[0] + anchor_bounds[2] + actual_gap + left_extent)
            if align == "start":
                loc_y = snap10(anchor_bounds[1] + top_extent + offset)
            elif align == "end":
                loc_y = snap10(anchor_bounds[1] + anchor_bounds[3] - bottom_extent + offset)
            else:
                loc_y = snap10(anchor_bounds[1] + anchor_bounds[3] // 2 - (bottom_extent - top_extent) // 2 + offset)
            return (loc_x, loc_y)
        if side == "left":
            loc_x = snap10(anchor_bounds[0] - actual_gap - right_extent)
            if align == "start":
                loc_y = snap10(anchor_bounds[1] + top_extent + offset)
            elif align == "end":
                loc_y = snap10(anchor_bounds[1] + anchor_bounds[3] - bottom_extent + offset)
            else:
                loc_y = snap10(anchor_bounds[1] + anchor_bounds[3] // 2 - (bottom_extent - top_extent) // 2 + offset)
            return (loc_x, loc_y)
        if side == "bottom":
            loc_y = snap10(anchor_bounds[1] + anchor_bounds[3] + actual_gap + top_extent)
            if align == "start":
                loc_x = snap10(anchor_bounds[0] + left_extent + offset)
            elif align == "end":
                loc_x = snap10(anchor_bounds[0] + anchor_bounds[2] - right_extent + offset)
            else:
                loc_x = snap10(anchor_bounds[0] + anchor_bounds[2] // 2 - (right_extent - left_extent) // 2 + offset)
            return (loc_x, loc_y)
        if side == "top":
            loc_y = snap10(anchor_bounds[1] - actual_gap - bottom_extent)
            if align == "start":
                loc_x = snap10(anchor_bounds[0] + left_extent + offset)
            elif align == "end":
                loc_x = snap10(anchor_bounds[0] + anchor_bounds[2] - right_extent + offset)
            else:
                loc_x = snap10(anchor_bounds[0] + anchor_bounds[2] // 2 - (right_extent - left_extent) // 2 + offset)
            return (loc_x, loc_y)
        raise ValueError(f"unsupported side {side!r}")

    for extra_gap in range(0, scan_limit + scan_step, scan_step):
        for offset in _scan_offsets(scan_step, scan_limit):
            candidate = deepcopy(attached)
            candidate.loc = loc_for(extra_gap, offset)
            candidate_bounds = component_bounds(candidate, project=project, padding=component_padding)
            if any(
                _bounds_overlap_local(candidate_bounds, component_bounds(other, project=project, padding=component_padding))
                for other in others
            ):
                continue
            return candidate.loc
    return loc_for(scan_limit, 0)


def add_component_near_component(
    circuit: RawCircuit,
    anchor: RawComponent,
    name: str,
    attrs: dict[str, str],
    *,
    lib: str | None,
    side: str,
    project: RawProject | None = None,
    gap: int = 40,
    align: str = "center",
    component_padding: int = 10,
    scan_step: int = 10,
    scan_limit: int = 600,
    ignore_names: set[str] | None = None,
) -> RawComponent:
    attached = component_template(name, attrs, lib=lib)
    attached.loc = place_component_near_component(
        circuit,
        anchor,
        attached,
        side=side,
        project=project,
        gap=gap,
        align=align,
        component_padding=component_padding,
        scan_step=scan_step,
        scan_limit=scan_limit,
        ignore_names=ignore_names,
    )
    circuit.components.append(attached)
    return attached


def add_subcircuit_near_component(
    circuit: RawCircuit,
    anchor: RawComponent,
    name: str,
    *,
    side: str,
    project: RawProject | None = None,
    gap: int = 40,
    align: str = "center",
    component_padding: int = 10,
    scan_step: int = 10,
    scan_limit: int = 600,
    ignore_names: set[str] | None = None,
) -> RawComponent:
    return add_component_near_component(
        circuit,
        anchor,
        name,
        dict(SUBCIRCUIT_INSTANCE_ATTRS),
        lib=None,
        side=side,
        project=project,
        gap=gap,
        align=align,
        component_padding=component_padding,
        scan_step=scan_step,
        scan_limit=scan_limit,
        ignore_names=ignore_names,
    )


def add_tunnel(
    circuit: RawCircuit,
    loc: tuple[int, int],
    label: str,
    width: int,
    *,
    facing: str | None = None,
    project: RawProject | None = None,
) -> RawComponent:
    facing_hint = facing or "east"
    matched = _find_port_at_point(circuit, loc, project=project)
    if matched is not None:
        anchor, port_name = matched
        resolved_facing = attachment_facing_for_port(anchor, port_name, project=project)
        if _is_center_port(anchor, port_name, project=project):
            return _center_port_tunnel(
                circuit,
                anchor,
                port_name,
                label,
                width,
                facing=facing or resolved_facing,
                project=project,
            )
        slot_key = (id(circuit), id(anchor), resolved_facing)
    else:
        anchor = _virtual_pin_at(loc, tunnel_facing=facing_hint)
        port_name = "io"
        resolved_facing = facing_hint
        if _is_center_port(anchor, port_name, project=project):
            return _center_port_tunnel(
                circuit,
                anchor,
                port_name,
                label,
                width,
                facing=resolved_facing,
                project=project,
            )
        slot_key = (id(circuit), loc, resolved_facing)
    preferred_offset = _reserve_tunnel_slot(
        slot_key,
        _tunnel_slot_step(label, width, facing=resolved_facing, project=project),
    )
    if matched is not None:
        return _hang_tunnel_on_port(
            circuit,
            anchor,
            port_name,
            label,
            width,
            facing=resolved_facing,
            preferred_offset=preferred_offset,
            project=project,
        )
    return attach_component_to_port(
        circuit,
        anchor,
        port_name,
        "Tunnel",
        _tunnel_attrs(label, width, facing=resolved_facing),
        lib="0",
        project=project,
        distance=_preferred_tunnel_distance(anchor, port_name, project=project),
        component_padding=12,
        tangent_limit=max(180, abs(preferred_offset) + 40),
        distance_limit=160,
        preferred_tangent_offset=preferred_offset,
        attached_port_name="io",
    )


def add_constant(circuit: RawCircuit, loc: tuple[int, int], *, width: int, value: int | str, facing: str = "east") -> None:
    rendered = value if isinstance(value, str) else hex(value)
    add_component(circuit, "Constant", loc, {"facing": facing, "width": str(width), "value": rendered}, lib="0")


def add_bit_extender(circuit: RawCircuit, loc: tuple[int, int], *, in_width: int, out_width: int, extend_type: str = "zero") -> RawComponent:
    return add_component(
        circuit,
        "Bit Extender",
        loc,
        {"in_width": str(in_width), "out_width": str(out_width), "type": extend_type},
        lib="0",
    )


def add_logic_gate(
    circuit: RawCircuit,
    gate_name: str,
    loc: tuple[int, int],
    *,
    width: int,
    inputs: int = 2,
    size: int = 30,
    negate_inputs: tuple[bool, ...] | None = None,
) -> RawComponent:
    attrs = {
        "facing": "east",
        "width": str(width),
        "size": str(size),
        "inputs": str(inputs),
        "out": "01",
        "label": "",
        "labelfont": "Dialog plain 12",
        "labelcolor": "#000000",
    }
    if gate_name == "XOR Gate":
        attrs["xor"] = "odd"
    for idx in range(inputs):
        negated = negate_inputs is not None and idx < len(negate_inputs) and negate_inputs[idx]
        attrs[f"negate{idx}"] = "true" if negated else "false"
    return add_component(circuit, gate_name, loc, attrs, lib="1")


def add_not_gate(circuit: RawCircuit, loc: tuple[int, int]) -> RawComponent:
    return add_component(
        circuit,
        "NOT Gate",
        loc,
        {
            "facing": "east",
            "width": "1",
            "size": "20",
            "out": "01",
            "label": "",
            "labelfont": "Dialog plain 12",
            "labelcolor": "#000000",
        },
        lib="1",
    )


def add_multiplexer(
    circuit: RawCircuit,
    loc: tuple[int, int],
    *,
    width: int,
    select_bits: int,
    facing: str = "east",
    select_loc: str = "bl",
    enable: bool = False,
) -> RawComponent:
    return add_component(
        circuit,
        "Multiplexer",
        loc,
        {
            "facing": facing,
            "selloc": select_loc,
            "select": str(select_bits),
            "width": str(width),
            "disabled": "Z",
            "enable": "true" if enable else "false",
        },
        lib="2",
    )


def add_decoder_component(
    circuit: RawCircuit,
    loc: tuple[int, int],
    *,
    select_bits: int,
    facing: str = "south",
    select_loc: str = "bl",
    enable: bool = False,
) -> RawComponent:
    return add_component(
        circuit,
        "Decoder",
        loc,
        {
            "facing": facing,
            "selloc": select_loc,
            "select": str(select_bits),
            "tristate": "false",
            "disabled": "Z",
            "enable": "true" if enable else "false",
        },
        lib="2",
    )


def add_subtractor_component(circuit: RawCircuit, loc: tuple[int, int], *, width: int) -> RawComponent:
    return add_component(circuit, "Subtractor", loc, {"width": str(width)}, lib="3")


def add_multi_input_gate_from_tunnels(
    circuit: RawCircuit,
    gate_name: str,
    loc: tuple[int, int],
    input_labels: list[str],
    out_label: str,
    *,
    width: int = 1,
    size: int = 50,
    negate_inputs: tuple[bool, ...] | None = None,
    project: RawProject | None = None,
) -> RawComponent:
    gate = add_logic_gate(
        circuit,
        gate_name,
        loc,
        width=width,
        inputs=len(input_labels),
        size=size,
        negate_inputs=negate_inputs,
    )
    for index, label in enumerate(input_labels):
        add_tunnel_to_port(circuit, gate, f"in{index}", label, width, project=project)
    add_tunnel_to_port(circuit, gate, "out", out_label, width, project=project)
    return gate


def wire_pin_to_led(circuit: RawCircuit, pin_loc: tuple[int, int], led_loc: tuple[int, int]) -> None:
    add_wire(circuit, pin_loc, (led_loc[0], pin_loc[1]))
    add_wire(circuit, (led_loc[0], pin_loc[1]), led_loc)


def add_binary_gate_from_tunnels(
    circuit: RawCircuit,
    gate_name: str,
    loc: tuple[int, int],
    in_a: str,
    in_b: str,
    out_label: str,
    *,
    width: int = 1,
    negate_inputs: tuple[bool, bool] = (False, False),
    project: RawProject | None = None,
) -> None:
    _ = project
    add_logic_gate(circuit, gate_name, loc, width=width, inputs=2, size=30, negate_inputs=negate_inputs)
    in_x = loc[0] - 30
    for y_offset, label in [(-10, in_a), (10, in_b)]:
        point = (in_x, loc[1] + y_offset)
        tunnel = (in_x - 20, point[1])
        add_tunnel(circuit, tunnel, label, width, facing="east")
        add_wire(circuit, tunnel, point)
    out_tunnel = (loc[0] + 20, loc[1])
    add_tunnel(circuit, out_tunnel, out_label, width, facing="west")
    add_wire(circuit, loc, out_tunnel)


def add_not_from_tunnel(circuit: RawCircuit, loc: tuple[int, int], in_label: str, out_label: str) -> None:
    add_not_gate(circuit, loc)
    in_point = (loc[0] - 20, loc[1])
    in_tunnel = (loc[0] - 40, loc[1])
    add_tunnel(circuit, in_tunnel, in_label, 1, facing="east")
    add_wire(circuit, in_tunnel, in_point)
    out_tunnel = (loc[0] + 20, loc[1])
    add_tunnel(circuit, out_tunnel, out_label, 1, facing="west")
    add_wire(circuit, loc, out_tunnel)


def _component_total_span(component: RawComponent, *, project: RawProject | None = None) -> tuple[int, int]:
    left, right, top, bottom = _component_extents(component, project=project)
    return (left + right, top + bottom)


def _default_gate_step(gate_name: str, *, width: int = 1, inputs: int = 2, size: int = 30) -> int:
    sample = component_template(
        gate_name,
        {
            "facing": "east",
            "width": str(width),
            "size": str(size),
            "inputs": str(inputs),
            "out": "01",
            **({"xor": "odd"} if gate_name == "XOR Gate" else {}),
            **{f"negate{index}": "false" for index in range(inputs)},
        },
        lib="1",
    )
    span_x, _ = _component_total_span(sample)
    return max(90, span_x + 50)


def chain_reduce(
    circuit: RawCircuit,
    gate_name: str,
    input_labels: list[str],
    output_label: str,
    *,
    start_x: int,
    y: int,
    width: int = 1,
    step: int | None = None,
) -> None:
    actual_step = step if step is not None else _default_gate_step(gate_name, width=width)
    acc = input_labels[0]
    for idx, label in enumerate(input_labels[1:], start=1):
        out = output_label if idx == len(input_labels) - 1 else unique_label(gate_name.lower())
        add_binary_gate_from_tunnels(circuit, gate_name, (start_x + (idx - 1) * actual_step, y), acc, label, out, width=width)
        acc = out


def add_compare_const_eq(
    circuit: RawCircuit,
    loc: tuple[int, int],
    *,
    bus_label: str,
    width: int,
    const_value: int,
    out_label: str,
) -> None:
    add_component(circuit, "Comparator", loc, {"width": str(width)}, lib="3")
    a_point = (loc[0] - 40, loc[1] - 10)
    a_tunnel = (a_point[0] - 20, a_point[1])
    add_tunnel(circuit, a_tunnel, bus_label, width, facing="east")
    add_wire(circuit, a_tunnel, a_point)
    b_point = (loc[0] - 40, loc[1] + 10)
    add_constant(circuit, (b_point[0] - 20, b_point[1]), width=width, value=const_value, facing="east")
    add_wire(circuit, (b_point[0] - 20, b_point[1]), b_point)
    eq_tunnel = (loc[0] + 20, loc[1])
    add_tunnel(circuit, eq_tunnel, out_label, 1, facing="west")
    add_wire(circuit, loc, eq_tunnel)


def connect_pin_to_tunnel(
    circuit: RawCircuit,
    *,
    pin_loc: tuple[int, int],
    tunnel_loc: tuple[int, int],
    label: str,
    width: int,
    facing: str,
) -> None:
    dx = tunnel_loc[0] - pin_loc[0]
    dy = tunnel_loc[1] - pin_loc[1]
    if abs(dx) >= abs(dy) and dx != 0:
        axis = (1 if dx > 0 else -1, 0)
        tangent = (0, 1)
        preferred_distance = max(30, abs(dx))
        preferred_tangent = snap10(dy)
    elif dy != 0:
        axis = (0, 1 if dy > 0 else -1)
        tangent = (1, 0)
        preferred_distance = max(30, abs(dy))
        preferred_tangent = snap10(dx)
    else:
        axis = {
            "east": (-1, 0),
            "west": (1, 0),
            "north": (0, 1),
            "south": (0, -1),
        }.get(facing, (1, 0))
        tangent = (0, 1) if axis[0] else (1, 0)
        preferred_distance = 30
        preferred_tangent = 0

    tunnel_attrs = _tunnel_attrs(label, width, facing=facing)
    existing = [component for component in circuit.components if component.name != "Text"]

    def candidate_loc(distance: int, tangent_offset: int) -> tuple[int, int]:
        return (
            pin_loc[0] + axis[0] * distance + tangent[0] * tangent_offset,
            pin_loc[1] + axis[1] * distance + tangent[1] * tangent_offset,
        )

    def tangent_offsets(limit: int = 120) -> list[int]:
        preferred = snap10(preferred_tangent)
        offsets = [preferred]
        for delta in range(10, limit + 10, 10):
            plus = preferred + delta
            minus = preferred - delta
            if plus <= limit:
                offsets.append(plus)
            if minus >= -limit:
                offsets.append(minus)
        return offsets

    chosen = candidate_loc(preferred_distance, preferred_tangent)
    for extra_distance in range(0, 181, 10):
        distance = preferred_distance + extra_distance
        for tangent_offset in tangent_offsets():
            loc = candidate_loc(distance, tangent_offset)
            candidate = component_template("Tunnel", tunnel_attrs, lib="0")
            candidate.loc = loc
            candidate_bounds = component_bounds(candidate, padding=12)
            if any(
                _bounds_overlap_local(candidate_bounds, component_bounds(other, padding=12))
                for other in existing
            ):
                continue
            chosen = loc
            extra_distance = 999999
            break
        else:
            continue
        break

    path = [pin_loc]
    if pin_loc[0] != chosen[0] and pin_loc[1] != chosen[1]:
        if axis[0]:
            path.append((pin_loc[0] + axis[0] * max(30, abs(chosen[0] - pin_loc[0])), pin_loc[1]))
        else:
            path.append((pin_loc[0], pin_loc[1] + axis[1] * max(30, abs(chosen[1] - pin_loc[1]))))
    path.append(chosen)
    add_tunnel(circuit, chosen, label, width, facing=facing)
    add_polyline(circuit, path)


def single_bit_splitter_attrs(bus_width: int, bit_index: int, facing: str) -> dict[str, str]:
    attrs = {"facing": facing, "fanout": "1", "incoming": str(bus_width), "appear": "center"}
    for idx in range(bus_width):
        attrs[f"bit{idx}"] = "0" if idx == bit_index else "none"
    return attrs


def ordered_splitter_attrs(
    incoming: int,
    output_bits: list[int],
    *,
    facing: str = "south",
    appear: str = "center",
) -> dict[str, str]:
    attrs = {"facing": facing, "fanout": str(len(output_bits)), "incoming": str(incoming), "appear": appear}
    for bit in range(incoming):
        attrs[f"bit{bit}"] = "none"
    for output_index, bit_index in enumerate(output_bits):
        attrs[f"bit{bit_index}"] = str(output_index)
    return attrs


def logic_gate_attrs(
    gate_name: str,
    *,
    width: int = 1,
    inputs: int = 2,
    size: int = 30,
    negate_inputs: tuple[bool, ...] | None = None,
) -> dict[str, str]:
    attrs = {
        "facing": "east",
        "width": str(width),
        "size": str(size),
        "inputs": str(inputs),
        "out": "01",
        "label": "",
        "labelloc": "north",
        "labelfont": "Dialog plain 12",
        "labelcolor": "#000000",
    }
    if gate_name == "XOR Gate":
        attrs["xor"] = "odd"
    for index in range(inputs):
        negated = negate_inputs is not None and index < len(negate_inputs) and negate_inputs[index]
        attrs[f"negate{index}"] = "true" if negated else "false"
    return attrs


def multiplexer_attrs(
    *,
    width: int,
    select_bits: int,
    facing: str = "east",
    select_loc: str = "bl",
    enable: bool = False,
) -> dict[str, str]:
    return {
        "facing": facing,
        "selloc": select_loc,
        "select": str(select_bits),
        "width": str(width),
        "disabled": "Z",
        "enable": "true" if enable else "false",
    }


def decoder_attrs(
    *,
    select_bits: int,
    facing: str = "south",
    select_loc: str = "bl",
    enable: bool = False,
) -> dict[str, str]:
    return {
        "facing": facing,
        "selloc": select_loc,
        "select": str(select_bits),
        "tristate": "false",
        "disabled": "Z",
        "enable": "true" if enable else "false",
    }


def append_built_circuit(target: RawCircuit, built: RawCircuit) -> RawCircuit:
    target.components.extend(deepcopy(comp) for comp in built.components)
    target.wires.extend(deepcopy(wire) for wire in built.wires)
    return target


def add_extract_cluster(
    circuit: RawCircuit,
    *,
    bus_label: str,
    bus_width: int,
    start_x: int,
    y: int,
    bit_specs: list[tuple[int, str]],
    pitch: int | None = None,
) -> None:
    actual_pitch = pitch if pitch is not None else module_default_splitter_pitch(bus_width)
    bus_tunnel = (start_x - max(50, actual_pitch), y)
    add_tunnel(circuit, bus_tunnel, bus_label, bus_width, facing="east")
    add_wire(circuit, bus_tunnel, (start_x + actual_pitch * (len(bit_specs) - 1), y))
    for idx, (bit_index, label) in enumerate(bit_specs):
        x = start_x + actual_pitch * idx
        facing = "north" if idx % 2 == 0 else "south"
        splitter = add_component(circuit, "Splitter", (x, y), single_bit_splitter_attrs(bus_width, bit_index, facing), lib="0")
        port_point = component_port_point(splitter, "out0")
        if port_point[1] < y:
            tunnel_facing = "south"
        elif port_point[1] > y:
            tunnel_facing = "north"
        elif port_point[0] < x:
            tunnel_facing = "east"
        else:
            tunnel_facing = "west"
        add_tunnel(circuit, port_point, label, 1, facing=tunnel_facing)


def add_assemble_cluster(
    circuit: RawCircuit,
    *,
    bus_label: str,
    bus_width: int,
    start_x: int,
    y: int,
    bit_specs: list[tuple[int, str]],
    pitch: int | None = None,
) -> None:
    actual_pitch = pitch if pitch is not None else module_default_splitter_pitch(bus_width)
    end_x = start_x + actual_pitch * (len(bit_specs) - 1)
    bus_tunnel = (end_x + max(50, actual_pitch), y)
    add_tunnel(circuit, bus_tunnel, bus_label, bus_width, facing="west")
    add_wire(circuit, (start_x, y), bus_tunnel)
    for idx, (bit_index, label) in enumerate(bit_specs):
        x = start_x + actual_pitch * idx
        facing = "north" if idx % 2 == 0 else "south"
        splitter = add_component(circuit, "Splitter", (x, y), single_bit_splitter_attrs(bus_width, bit_index, facing), lib="0")
        port_point = component_port_point(splitter, "out0")
        if port_point[1] < y:
            tunnel_facing = "south"
        elif port_point[1] > y:
            tunnel_facing = "north"
        elif port_point[0] < x:
            tunnel_facing = "east"
        else:
            tunnel_facing = "west"
        add_tunnel(circuit, port_point, label, 1, facing=tunnel_facing)


def add_subcircuit_instance(circuit: RawCircuit, name: str, loc: tuple[int, int]) -> RawComponent:
    return add_component(circuit, name, loc, dict(SUBCIRCUIT_INSTANCE_ATTRS), lib=None)


def add_constant_source(
    circuit: RawCircuit,
    loc: tuple[int, int],
    *,
    label: str,
    width: int = 1,
    value: int | str = 0,
) -> RawComponent:
    constant = add_component(
        circuit,
        "Constant",
        loc,
        {"facing": "east", "width": str(width), "value": value if isinstance(value, str) else hex(value)},
        lib="0",
    )
    add_tunnel_to_port(circuit, constant, "io", label, width)
    return constant


def extract_bus_labels(
    circuit: RawCircuit,
    *,
    bus_label: str,
    bus_width: int,
    bits: list[int],
    prefix: str,
    start_x: int,
    y: int,
    pitch: int = 20,
) -> dict[int, str]:
    labels: dict[int, str] = {}
    specs: list[tuple[int, str]] = []
    for bit in bits:
        label = unique_label(prefix)
        labels[bit] = label
        specs.append((bit, label))
    add_extract_cluster(circuit, bus_label=bus_label, bus_width=bus_width, start_x=start_x, y=y, bit_specs=specs, pitch=pitch)
    return labels


def assemble_bus_from_labels(
    circuit: RawCircuit,
    *,
    bus_label: str,
    bus_width: int,
    mapping: dict[int, str],
    start_x: int,
    y: int,
    pitch: int = 20,
    zero_origin: tuple[int, int] | None = None,
) -> None:
    zero_x, zero_y = zero_origin if zero_origin is not None else (start_x - 120, y + 60)
    zero_index = 0
    specs: list[tuple[int, str]] = []
    for bit in range(bus_width - 1, -1, -1):
        label = mapping.get(bit)
        if label is None:
            label = unique_label("ZERO")
            add_constant_source(circuit, (zero_x, zero_y + zero_index * 30), label=label)
            zero_index += 1
        specs.append((bit, label))
    add_assemble_cluster(circuit, bus_label=bus_label, bus_width=bus_width, start_x=start_x, y=y, bit_specs=specs, pitch=pitch)


def add_two_way_splitter(
    circuit: RawCircuit,
    loc: tuple[int, int],
    *,
    incoming: int,
    primary_bits: list[int],
    secondary_bits: list[int],
    facing: str,
    appear: str = "left",
) -> RawComponent:
    attrs = {"facing": facing, "fanout": "2", "incoming": str(incoming), "appear": appear}
    for bit in range(incoming):
        attrs[f"bit{bit}"] = "none"
    splitter = add_component(circuit, "Splitter", loc, attrs, lib="0")
    set_splitter_two_way(splitter, incoming, primary_bits, secondary_bits)
    return splitter


def add_reduction_tree_from_tunnels(
    circuit: RawCircuit,
    gate_name: str,
    input_labels: list[str],
    output_label: str,
    *,
    start_x: int,
    start_y: int,
    x_step: int = 80,
    y_step: int = 60,
    width: int = 1,
) -> None:
    if not input_labels:
        raise ValueError("reduction tree requires at least one input label")
    if len(input_labels) == 1:
        add_tunnel(circuit, (start_x, start_y), input_labels[0], width, facing="east")
        add_tunnel(circuit, (start_x + 40, start_y), output_label, width, facing="west")
        add_wire(circuit, (start_x, start_y), (start_x + 40, start_y))
        return

    items = [(label, start_y + index * y_step) for index, label in enumerate(input_labels)]
    x = start_x
    while len(items) > 1:
        next_items: list[tuple[str, int]] = []
        for index in range(0, len(items), 2):
            if index == len(items) - 1:
                next_items.append(items[index])
                continue
            left_label, left_y = items[index]
            right_label, right_y = items[index + 1]
            out_label = output_label if len(items) == 2 and index == 0 else unique_label(gate_name.lower().replace(" ", "_"))
            gate_y = snap10((left_y + right_y) // 2)
            add_binary_gate_from_tunnels(circuit, gate_name, (x, gate_y), left_label, right_label, out_label, width=width)
            next_items.append((out_label, gate_y))
        items = next_items
        x += x_step


def remove_components_at_locs(
    circuit: RawCircuit,
    locs: set[tuple[int, int]],
    *,
    remove_wires: bool = True,
) -> None:
    circuit.components = [comp for comp in circuit.components if comp.loc not in locs]
    if remove_wires:
        circuit.wires = [wire for wire in circuit.wires if wire.start not in locs and wire.end not in locs]


def splitter_selected_bits(comp: RawComponent) -> list[int]:
    bits: list[int] = []
    for attr in comp.attrs:
        if attr.name.startswith("bit") and attr.value == "0":
            bits.append(int(attr.name[3:]))
    return sorted(bits)


def set_splitter_single_bit(comp: RawComponent, incoming: int, bit_index: int) -> None:
    set_attr(comp, "incoming", str(incoming))
    set_attr(comp, "fanout", "1")
    comp.attrs = [attr for attr in comp.attrs if not attr.name.startswith("bit")]
    for index in range(incoming):
        set_attr(comp, f"bit{index}", "0" if index == bit_index else "none")


def set_splitter_two_way(comp: RawComponent, incoming: int, low_bits: list[int], high_bits: list[int]) -> None:
    low = set(low_bits)
    high = set(high_bits)
    set_attr(comp, "incoming", str(incoming))
    set_attr(comp, "fanout", "2")
    comp.attrs = [attr for attr in comp.attrs if not attr.name.startswith("bit")]
    for index in range(incoming):
        if index in low:
            value = "0"
        elif index in high:
            value = "1"
        else:
            value = "none"
        set_attr(comp, f"bit{index}", value)


def set_splitter_extract(comp: RawComponent, incoming: int, selected: list[int]) -> None:
    keep = set(selected)
    set_attr(comp, "incoming", str(incoming))
    set_attr(comp, "fanout", "1")
    comp.attrs = [attr for attr in comp.attrs if not attr.name.startswith("bit")]
    for index in range(incoming):
        set_attr(comp, f"bit{index}", "0" if index in keep else "none")


def add_named_bus_rebuilder(
    circuit: RawCircuit,
    *,
    bus_label: str,
    bit_labels_msb_to_lsb: list[str],
    splitter_loc: tuple[int, int],
) -> RawComponent:
    splitter = add_component(
        circuit,
        "Splitter",
        splitter_loc,
        {
            "facing": "north",
            "fanout": str(len(bit_labels_msb_to_lsb)),
            "incoming": str(len(bit_labels_msb_to_lsb)),
            "appear": "center",
            **{f"bit{index}": str(len(bit_labels_msb_to_lsb) - 1 - index) for index in range(len(bit_labels_msb_to_lsb))},
        },
        lib="0",
    )
    add_tunnel_to_port(circuit, splitter, "combined", bus_label, len(bit_labels_msb_to_lsb))
    for index, label in enumerate(bit_labels_msb_to_lsb):
        add_tunnel_to_port(circuit, splitter, f"out{index}", label, 1)
    return splitter


def add_bus_rebuilder_from_input_bus(
    circuit: RawCircuit,
    *,
    source_bus_label: str,
    source_labels_msb_to_lsb: list[str],
    source_splitter_loc: tuple[int, int],
    target_bus_label: str,
    target_labels_msb_to_lsb: list[str],
    target_splitter_loc: tuple[int, int],
    constant_origin: tuple[int, int],
) -> dict[str, str]:
    source_splitter = add_component(
        circuit,
        "Splitter",
        source_splitter_loc,
        {
            "facing": "north",
            "fanout": str(len(source_labels_msb_to_lsb)),
            "incoming": str(len(source_labels_msb_to_lsb)),
            "appear": "center",
            **{f"bit{index}": str(len(source_labels_msb_to_lsb) - 1 - index) for index in range(len(source_labels_msb_to_lsb))},
        },
        lib="0",
    )
    add_tunnel_to_port(circuit, source_splitter, "combined", source_bus_label, len(source_labels_msb_to_lsb))
    temp_labels: dict[str, str] = {}
    for index, label in enumerate(source_labels_msb_to_lsb):
        temp = unique_label(label)
        temp_labels[label] = temp
        add_tunnel_to_port(circuit, source_splitter, f"out{index}", temp, 1)

    target_splitter = add_component(
        circuit,
        "Splitter",
        target_splitter_loc,
        {
            "facing": "north",
            "fanout": str(len(target_labels_msb_to_lsb)),
            "incoming": str(len(target_labels_msb_to_lsb)),
            "appear": "center",
            **{f"bit{index}": str(len(target_labels_msb_to_lsb) - 1 - index) for index in range(len(target_labels_msb_to_lsb))},
        },
        lib="0",
    )
    add_tunnel_to_port(circuit, target_splitter, "combined", target_bus_label, len(target_labels_msb_to_lsb))
    for index, label in enumerate(target_labels_msb_to_lsb):
        if label in temp_labels:
            add_tunnel_to_port(circuit, target_splitter, f"out{index}", temp_labels[label], 1)
        else:
            temp = unique_label(f"zero_{label}")
            add_tunnel_to_port(circuit, target_splitter, f"out{index}", temp, 1)
            const_x = constant_origin[0]
            const_y = constant_origin[1] + index * 30
            add_constant(circuit, (const_x, const_y), width=1, value=0, facing="east")
            add_tunnel(circuit, (const_x + 40, const_y), temp, 1, facing="west")
            add_wire(circuit, (const_x, const_y), (const_x + 40, const_y))
    return temp_labels


def rename_tunnel_labels(circuit: RawCircuit, old: str, new: str) -> None:
    for comp in circuit.components:
        if comp.name == "Tunnel" and get_attr(comp, "label") == old:
            set_attr(comp, "label", new)
