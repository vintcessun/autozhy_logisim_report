from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from .geometry import get_component_geometry
from .graph import build_wire_graph, infer_component_attachment_points
from .model import RawCircuit, RawComponent, RawProject


@dataclass(slots=True)
class LogicalEndpoint:
    instance: str
    port: str

    def to_dict(self) -> dict[str, str]:
        return {"instance": self.instance, "port": self.port}


@dataclass(slots=True)
class LogicalInstance:
    id: str
    kind: str
    attrs: dict[str, str]
    loc: tuple[int, int]
    port_points: dict[str, tuple[int, int]] = field(default_factory=dict)
    port_info: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "attrs": dict(self.attrs),
            "loc": self.loc,
            "port_points": dict(self.port_points),
            "port_info": dict(self.port_info),
        }


@dataclass(slots=True)
class LogicalNet:
    id: str
    endpoints: list[LogicalEndpoint] = field(default_factory=list)
    points: set[tuple[int, int]] = field(default_factory=set)
    tunnel_labels: set[str] = field(default_factory=set)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "endpoints": [endpoint.to_dict() for endpoint in self.endpoints],
            "points": sorted(self.points),
            "tunnel_labels": sorted(self.tunnel_labels),
        }


@dataclass(slots=True)
class LogicalCircuit:
    name: str
    instances: list[LogicalInstance]
    nets: list[LogicalNet]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "instances": [instance.to_dict() for instance in self.instances],
            "nets": [net.to_dict() for net in self.nets],
        }


def _instance_id(component: RawComponent, index: int) -> str:
    x, y = component.loc
    return f"{component.name}_{index}_{x}_{y}"


def _subcircuit_ports(component: RawComponent, project: RawProject) -> list[tuple[str, tuple[int, int], dict[str, Any]]]:
    if component.lib is not None or not project.has_circuit(component.name):
        return []
    target = project.circuit(component.name)
    facing = component.get("facing", "east") or "east"
    result: list[tuple[str, tuple[int, int], dict[str, Any]]] = []
    for port in target.port_offsets(facing=facing):
        absolute = (component.loc[0] + port.offset[0], component.loc[1] + port.offset[1])
        result.append(
            (
                port.name,
                absolute,
                {
                    "direction": port.direction,
                    "width": port.width,
                    "label": port.label,
                    "pin_loc": port.pin_loc,
                    "target_circuit": component.name,
                },
            )
        )
    return result


def _component_ports(component: RawComponent, project: RawProject | None = None) -> list[tuple[str, tuple[int, int], dict[str, Any]]]:
    if project is not None:
        ports = _subcircuit_ports(component, project)
        if ports:
            return ports
    geometry = get_component_geometry(component, project=project)
    result: list[tuple[str, tuple[int, int], dict[str, Any]]] = []
    for port in geometry.ports:
        absolute = (component.loc[0] + port.offset[0], component.loc[1] + port.offset[1])
        result.append(
            (
                port.name,
                absolute,
                {
                    "direction": port.direction,
                    "width": port.width,
                },
            )
        )
    return result


def extract_logical_circuit(
    circuit: RawCircuit,
    radius: int = 60,
    project: RawProject | None = None,
) -> LogicalCircuit:
    split_points = {
        point
        for component in circuit.components
        for _, point, _ in _component_ports(component, project)
    }
    graph = build_wire_graph(circuit, split_points=split_points)
    instances: list[LogicalInstance] = []
    nets: dict[str, LogicalNet] = {net_id: LogicalNet(id=net_id, points=set(net.points)) for net_id, net in graph.nets.items()}
    tunnel_groups: defaultdict[str, list[str]] = defaultdict(list)

    for index, component in enumerate(circuit.components):
        instance = LogicalInstance(
            id=_instance_id(component, index),
            kind=component.name,
            attrs=component.attr_map(),
            loc=component.loc,
        )

        resolved_ports = _component_ports(component, project)
        if resolved_ports:
            port_entries = resolved_ports
        else:
            attach_points = infer_component_attachment_points(component, graph, radius=radius)
            port_entries = [(("io" if len(attach_points) == 1 else f"p{port_index}"), point, {}) for port_index, point in enumerate(attach_points)]

        for port_name, point, port_meta in port_entries:
            instance.port_points[port_name] = point
            if port_meta:
                instance.port_info[port_name] = port_meta
            net_id = graph.net_at(point)
            if net_id is None:
                continue
            nets.setdefault(net_id, LogicalNet(id=net_id)).endpoints.append(LogicalEndpoint(instance=instance.id, port=port_name))
            if component.name == "Tunnel":
                label = component.get("label", "") or ""
                if label:
                    nets[net_id].tunnel_labels.add(label)
                    tunnel_groups[label].append(net_id)
        instances.append(instance)

    merged_nets: dict[str, LogicalNet] = dict(nets)
    for label, members in tunnel_groups.items():
        unique_members = [member for member in dict.fromkeys(members) if member in merged_nets]
        if len(unique_members) < 2:
            continue
        keeper = unique_members[0]
        if keeper not in merged_nets:
            continue
        keep_net = merged_nets[keeper]
        for other in unique_members[1:]:
            if other == keeper or other not in merged_nets:
                continue
            keep_net.endpoints.extend(merged_nets[other].endpoints)
            keep_net.points |= merged_nets[other].points
            keep_net.tunnel_labels |= merged_nets[other].tunnel_labels
            del merged_nets[other]

    return LogicalCircuit(name=circuit.name, instances=instances, nets=sorted(merged_nets.values(), key=lambda item: item.id))
