from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from .graph import build_wire_graph, expand_wire
from .logical import extract_logical_circuit
from .model import RawCircuit, RawProject


Point = tuple[int, int]


@dataclass(slots=True)
class WidthDeterminant:
    point: Point
    width: int
    instance: str
    port: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "point": self.point,
            "width": self.width,
            "instance": self.instance,
            "port": self.port,
        }


@dataclass(slots=True)
class WidthConflict:
    kind: str
    net_id: str | None
    determinants: list[WidthDeterminant] = field(default_factory=list)
    wire_indexes: list[int] = field(default_factory=list)
    points: set[Point] = field(default_factory=set)

    def widths(self) -> list[int]:
        return sorted({entry.width for entry in self.determinants})

    def signature(self) -> tuple[tuple[Point, int], ...]:
        items = {(entry.point, entry.width) for entry in self.determinants}
        return tuple(sorted(items))

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "net_id": self.net_id,
            "widths": self.widths(),
            "determinants": [entry.to_dict() for entry in self.determinants],
            "wire_indexes": sorted(self.wire_indexes),
            "points": sorted(self.points),
        }


@dataclass(slots=True)
class _PointWidthData:
    determinants: list[WidthDeterminant] = field(default_factory=list)
    primary_width: int | None = None


class _Bundle:
    __slots__ = ("name", "parent", "points", "assigned_pairs", "width", "invalid")

    def __init__(self, name: str, point: Point) -> None:
        self.name = name
        self.parent: _Bundle = self
        self.points: set[Point] = {point}
        self.assigned_pairs: set[tuple[Point, int]] = set()
        self.width: int | None = None
        self.invalid = False

    def find(self) -> _Bundle:
        node = self
        while node.parent is not node:
            node = node.parent
        root = node
        node = self
        while node.parent is not node:
            parent = node.parent
            node.parent = root
            node = parent
        return root

    def unite(self, other: _Bundle) -> None:
        left = self.find()
        right = other.find()
        if left is right:
            return
        left.parent = right

    def set_width(self, point: Point, width: int | None) -> None:
        if width is None:
            return
        pair = (point, width)
        if pair in self.assigned_pairs:
            return
        self.assigned_pairs.add(pair)
        if self.invalid:
            return
        if self.width is None:
            self.width = width
            return
        if self.width != width:
            self.invalid = True


class _BundleMap:
    def __init__(self) -> None:
        self.point_bundles: dict[Point, _Bundle] = {}
        self.bundles: list[_Bundle] = []
        self._counter = 0

    def get_bundle_at(self, point: Point) -> _Bundle | None:
        bundle = self.point_bundles.get(point)
        return None if bundle is None else bundle.find()

    def create_bundle_at(self, point: Point) -> _Bundle:
        bundle = self.point_bundles.get(point)
        if bundle is not None:
            return bundle.find()
        bundle = _Bundle(f"bundle_raw_{self._counter}", point)
        self._counter += 1
        self.point_bundles[point] = bundle
        self.bundles.append(bundle)
        return bundle

    def set_bundle_at(self, point: Point, bundle: _Bundle) -> None:
        self.point_bundles[point] = bundle
        bundle.points.add(point)

    def normalize(self) -> None:
        roots_seen: dict[_Bundle, None] = {}
        for point, bundle in list(self.point_bundles.items()):
            root = bundle.find()
            if root is not bundle:
                self.point_bundles[point] = root
            root.points.add(point)
            roots_seen[root] = None
        ordered_roots = sorted(roots_seen, key=lambda item: min(item.points))
        for index, bundle in enumerate(ordered_roots):
            bundle.name = f"bundle_{index}"
        self.bundles = ordered_roots


@dataclass(slots=True)
class _WidthAnalysis:
    all_determinants: list[WidthDeterminant]
    point_data: dict[Point, _PointWidthData]
    bundle_map: _BundleMap
    expanded_wires: list[set[Point]]
    invalid_wire_indexes: list[int]
    conflicts: list[WidthConflict]


def _parse_width(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    width_attr = getattr(value, "width", None)
    if isinstance(width_attr, int):
        return width_attr if width_attr > 0 else None
    text = str(value).strip()
    if not text or text.lower() in {"unknown", "nil", "none"}:
        return None
    try:
        width = int(text)
    except ValueError:
        return None
    return width if width > 0 else None


def _wire_indexes_touching_points(expanded_wires: list[set[Point]], points: set[Point]) -> list[int]:
    if not points:
        return []
    indexes: list[int] = []
    for index, expanded in enumerate(expanded_wires):
        if expanded & points:
            indexes.append(index)
    return indexes


def _merge_conflicts(conflicts: list[WidthConflict]) -> list[WidthConflict]:
    merged: dict[tuple[tuple[Point, int], ...], WidthConflict] = {}
    for conflict in conflicts:
        key = conflict.signature()
        if not key:
            continue
        existing = merged.get(key)
        if existing is None:
            merged[key] = conflict
            continue
        existing.points |= conflict.points
        existing.wire_indexes = sorted(set(existing.wire_indexes) | set(conflict.wire_indexes))
        if existing.net_id is None:
            existing.net_id = conflict.net_id
        if conflict.kind not in existing.kind.split("+"):
            existing.kind = f"{existing.kind}+{conflict.kind}"
    return sorted(merged.values(), key=lambda item: (item.net_id or "", item.signature()))


def _collect_point_widths(
    circuit: RawCircuit,
    *,
    project: RawProject | None,
) -> tuple[
    list[WidthDeterminant],
    dict[Point, _PointWidthData],
    list[tuple[str, Point]],
    list[tuple[str, dict[str, Point], dict[str, dict[str, Any]]]],
    set[Point],
]:
    logical = extract_logical_circuit(circuit, project=project)
    all_determinants: list[WidthDeterminant] = []
    point_data: dict[Point, _PointWidthData] = {}
    tunnels: list[tuple[str, Point]] = []
    splitters: list[tuple[str, dict[str, Point], dict[str, dict[str, Any]]]] = []
    split_points: set[Point] = set()

    for instance in logical.instances:
        split_points.update(instance.port_points.values())
        if instance.kind == "Tunnel":
            label = (instance.attrs.get("label", "") or "").strip()
            point = instance.port_points.get("io", instance.loc)
            if label:
                tunnels.append((label, point))
        if instance.kind == "Splitter":
            splitters.append((instance.id, dict(instance.port_points), dict(instance.port_info)))

        for port, point in instance.port_points.items():
            width = _parse_width(instance.port_info.get(port, {}).get("width"))
            if width is None:
                continue
            determinant = WidthDeterminant(point=point, width=width, instance=instance.id, port=port)
            all_determinants.append(determinant)
            data = point_data.setdefault(point, _PointWidthData())
            if data.primary_width is None:
                data.primary_width = width
            data.determinants.append(determinant)

    return all_determinants, point_data, tunnels, splitters, split_points


def _seed_bundles_from_graph(circuit: RawCircuit, split_points: set[Point], bundle_map: _BundleMap) -> None:
    # Saved .circ files often keep long wire segments unsplit, so endpoint-only
    # union would miss T-junctions where another wire lands on the segment body.
    graph = build_wire_graph(circuit, split_points=split_points)
    for net in graph.nets.values():
        points = sorted(net.points)
        if not points:
            continue
        bundle = bundle_map.create_bundle_at(points[0])
        for point in points[1:]:
            bundle_map.set_bundle_at(point, bundle)


def _connect_tunnels(tunnels: list[tuple[str, Point]], bundle_map: _BundleMap) -> None:
    groups: dict[str, list[Point]] = defaultdict(list)
    for label, point in tunnels:
        groups[label].append(point)
    for points in groups.values():
        found_bundle: _Bundle | None = None
        found_point: Point | None = None
        for point in points:
            found_bundle = bundle_map.get_bundle_at(point)
            if found_bundle is not None:
                found_point = point
                break
        if found_bundle is None:
            found_point = points[0]
            found_bundle = bundle_map.create_bundle_at(found_point)
        for point in points:
            if point == found_point:
                continue
            bundle = bundle_map.get_bundle_at(point)
            if bundle is None:
                bundle_map.set_bundle_at(point, found_bundle)
                continue
            bundle.unite(found_bundle)


def _apply_splitter_widths(splitters: list[tuple[str, dict[str, Point], dict[str, dict[str, Any]]]], bundle_map: _BundleMap, *, create_missing: bool) -> None:
    for _, port_points, port_info in splitters:
        for port, point in port_points.items():
            width = _parse_width(port_info.get(port, {}).get("width"))
            if width is None:
                continue
            bundle = bundle_map.create_bundle_at(point) if create_missing else bundle_map.get_bundle_at(point)
            if bundle is not None:
                bundle.set_width(point, width)


def _apply_point_widths(point_data: dict[Point, _PointWidthData], bundle_map: _BundleMap) -> None:
    for point, data in point_data.items():
        if data.primary_width is None:
            continue
        bundle = bundle_map.get_bundle_at(point)
        if bundle is not None:
            bundle.set_width(point, data.primary_width)


def _bundle_determinants(bundle: _Bundle, all_determinants: list[WidthDeterminant]) -> list[WidthDeterminant]:
    widths = {width for _, width in bundle.assigned_pairs}
    unique: dict[tuple[Point, int, str, str], WidthDeterminant] = {}
    for determinant in all_determinants:
        if determinant.point not in bundle.points:
            continue
        if determinant.width not in widths:
            continue
        unique[(determinant.point, determinant.width, determinant.instance, determinant.port)] = determinant
    return list(unique.values())


def _analyze_widths(circuit: RawCircuit, *, project: RawProject | None = None) -> _WidthAnalysis:
    all_determinants, point_data, tunnels, splitters, split_points = _collect_point_widths(circuit, project=project)
    expanded_wires = [set(expand_wire(wire)) for wire in circuit.wires]

    bundle_map = _BundleMap()
    _seed_bundles_from_graph(circuit, split_points, bundle_map)
    _connect_tunnels(tunnels, bundle_map)
    bundle_map.normalize()
    # Logisim applies splitter end widths before and after point-derived widths.
    _apply_splitter_widths(splitters, bundle_map, create_missing=True)
    _apply_point_widths(point_data, bundle_map)
    _apply_splitter_widths(splitters, bundle_map, create_missing=False)

    invalid_wire_indexes: list[int] = []
    for index, wire in enumerate(circuit.wires):
        bundle = bundle_map.get_bundle_at(wire.start)
        if bundle is not None and bundle.invalid:
            invalid_wire_indexes.append(index)

    conflicts: list[WidthConflict] = []

    for point, data in point_data.items():
        widths = {entry.width for entry in data.determinants}
        if len(widths) <= 1:
            continue
        unique = {
            (entry.point, entry.width, entry.instance, entry.port): entry
            for entry in data.determinants
        }
        conflict_points = {point}
        conflicts.append(
            WidthConflict(
                kind="point",
                net_id=None,
                determinants=list(unique.values()),
                wire_indexes=_wire_indexes_touching_points(expanded_wires, conflict_points),
                points=conflict_points,
            )
        )

    for bundle in bundle_map.bundles:
        if not bundle.invalid:
            continue
        determinants = _bundle_determinants(bundle, all_determinants)
        if not determinants:
            continue
        wire_indexes = [
            index
            for index, wire in enumerate(circuit.wires)
            if bundle_map.get_bundle_at(wire.start) is bundle
        ]
        conflicts.append(
            WidthConflict(
                kind="bundle",
                net_id=bundle.name,
                determinants=determinants,
                wire_indexes=wire_indexes,
                points=set(bundle.points),
            )
        )

    return _WidthAnalysis(
        all_determinants=all_determinants,
        point_data=point_data,
        bundle_map=bundle_map,
        expanded_wires=expanded_wires,
        invalid_wire_indexes=sorted(invalid_wire_indexes),
        conflicts=_merge_conflicts(conflicts),
    )


def find_width_conflicts(circuit: RawCircuit, *, project: RawProject | None = None) -> list[WidthConflict]:
    return _analyze_widths(circuit, project=project).conflicts


def find_invalid_wire_indexes(circuit: RawCircuit, *, project: RawProject | None = None) -> list[int]:
    return _analyze_widths(circuit, project=project).invalid_wire_indexes


def has_width_conflicts(circuit: RawCircuit, *, project: RawProject | None = None) -> bool:
    return bool(find_width_conflicts(circuit, project=project))
