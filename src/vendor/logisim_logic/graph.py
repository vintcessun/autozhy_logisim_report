from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable

from .model import RawCircuit, RawComponent, RawWire


Point = tuple[int, int]
Node = tuple[str, Point]


def _step(a: int, b: int) -> int:
    if a == b:
        return 0
    return 10 if b > a else -10


def expand_wire(wire: RawWire) -> list[Point]:
    x1, y1 = wire.start
    x2, y2 = wire.end
    if x1 != x2 and y1 != y2:
        raise ValueError(f"Logisim wire must be orthogonal: {wire.start} -> {wire.end}")
    dx = _step(x1, x2)
    dy = _step(y1, y2)
    points: list[Point] = [(x1, y1)]
    x, y = x1, y1
    while (x, y) != (x2, y2):
        x += dx
        y += dy
        points.append((x, y))
    return points


class UnionFind:
    def __init__(self) -> None:
        self.parent: dict[Point, Point] = {}

    def add(self, item: Point) -> None:
        self.parent.setdefault(item, item)

    def find(self, item: Point) -> Point:
        self.add(item)
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while item != root:
            parent = self.parent[item]
            self.parent[item] = root
            item = parent
        return root

    def union(self, a: Point, b: Point) -> None:
        ra = self.find(a)
        rb = self.find(b)
        if ra != rb:
            self.parent[rb] = ra


@dataclass(slots=True)
class Net:
    id: str
    points: set[Point] = field(default_factory=set)

    def to_dict(self) -> dict[str, object]:
        return {"id": self.id, "points": sorted(self.points)}


@dataclass(slots=True)
class WireGraph:
    nets: dict[str, Net]
    point_to_net: dict[Point, str]
    point_to_nets: dict[Point, tuple[str, ...]]
    expanded_points: set[Point]

    def net_at(self, point: Point) -> str | None:
        nets = self.point_to_nets.get(point, ())
        if len(nets) != 1:
            return None
        return nets[0]

    def nets_at(self, point: Point) -> list[str]:
        return list(self.point_to_nets.get(point, ()))

    def nearby_points(self, point: Point, radius: int = 60) -> list[Point]:
        x, y = point
        result = [
            other
            for other in self.expanded_points
            if abs(other[0] - x) + abs(other[1] - y) <= radius
        ]
        result.sort(key=lambda p: (abs(p[0] - x) + abs(p[1] - y), p[1], p[0]))
        return result

    def nearby_nets(self, point: Point, radius: int = 60) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for other in self.nearby_points(point, radius=radius):
            for net_id in self.point_to_nets.get(other, ()):
                if net_id in seen:
                    continue
                seen.add(net_id)
                result.append(net_id)
        return result


def build_wire_graph(circuit: RawCircuit, split_points: Iterable[Point] = ()) -> WireGraph:
    uf = UnionFind()
    expanded: set[Point] = set()
    explicit_splits = set(split_points)
    nodes_at_point: defaultdict[Point, set[Node]] = defaultdict(set)
    endpoint_counts: defaultdict[Point, int] = defaultdict(int)
    for wire in circuit.wires:
        points = expand_wire(wire)
        expanded.update(points)
        if len(points) >= 1:
            endpoint_counts[points[0]] += 1
            endpoint_counts[points[-1]] += 1
        orientation = "h" if wire.start[1] == wire.end[1] else "v"
        for point in points:
            node = (orientation, point)
            uf.add(node)
            nodes_at_point[point].add(node)
        for left, right in zip(points, points[1:]):
            uf.union((orientation, left), (orientation, right))

    # Ensure all split points (component ports) result in a node/net
    for point in explicit_splits:
        if point not in nodes_at_point:
            expanded.add(point)
            node = ("h", point) # Default orientation
            uf.add(node)
            nodes_at_point[point].add(node)

    for point, nodes in nodes_at_point.items():
        by_orientation: defaultdict[str, list[Node]] = defaultdict(list)
        for node in nodes:
            by_orientation[node[0]].append(node)
        for oriented_nodes in by_orientation.values():
            head = oriented_nodes[0]
            for other in oriented_nodes[1:]:
                uf.union(head, other)
        if point in explicit_splits or endpoint_counts[point] > 0:
            merged_nodes = [group[0] for group in by_orientation.values()]
            if len(merged_nodes) > 1:
                head = merged_nodes[0]
                for other in merged_nodes[1:]:
                    uf.union(head, other)

    grouped: dict[Node, set[Point]] = defaultdict(set)
    for point, nodes in nodes_at_point.items():
        for node in nodes:
            grouped[uf.find(node)].add(point)

    nets: dict[str, Net] = {}
    point_to_net: dict[Point, str] = {}
    point_to_nets_raw: defaultdict[Point, list[str]] = defaultdict(list)
    for index, points in enumerate(grouped.values()):
        net_id = f"net_{index}"
        net = Net(id=net_id, points=points)
        nets[net_id] = net
        for point in points:
            point_to_nets_raw[point].append(net_id)

    point_to_nets = {point: tuple(sorted(net_ids)) for point, net_ids in point_to_nets_raw.items()}
    for point, net_ids in point_to_nets.items():
        if len(net_ids) == 1:
            point_to_net[point] = net_ids[0]

    return WireGraph(nets=nets, point_to_net=point_to_net, point_to_nets=point_to_nets, expanded_points=expanded)


def single_port_components() -> set[str]:
    return {
        "Button",
        "Clock",
        "Constant",
        "DipSwitch",
        "Ground",
        "LED",
        "Pin",
        "Power",
        "Probe",
        "Pull Resistor",
        "Random",
        "Switch",
        "Text",
        "Tunnel",
    }


def infer_component_attachment_points(component: RawComponent, graph: WireGraph, radius: int = 60) -> list[Point]:
    if component.name == "Text":
        return []
    if component.name in single_port_components():
        return [component.loc]
    points = graph.nearby_points(component.loc, radius=radius)
    unique: list[Point] = []
    seen: set[Point] = set()
    for point in points:
        if point in seen:
            continue
        seen.add(point)
        unique.append(point)
    return unique
