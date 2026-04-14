from __future__ import annotations

from .xml_io import load_project, save_project
from .model import RawCircuit, RawComponent, RawWire, RawAttribute, Point
from .logical import LogicalCircuit, LogicalInstance, LogicalNet, LogicalEndpoint, extract_logical_circuit
from .rebuild_support import (
    add_component,
    add_polyline,
    add_splitter,
    add_tunnel,
    add_tunnel_on_port,
    add_tunnel_to_port,
    add_wire,
    component_port_point as port_point,
    connect_points_routed,
    connect_ports_routed,
    find_component,
    get_attribute,
    set_attribute,
    set_attributes,
)
from .geometry import get_component_geometry, get_component_visual_bounds
from .high_level import ProjectFacade, CircuitEditor

# 别名，用于提高 LLM 亲和力
component = RawComponent
Instance = RawComponent
circuit = RawCircuit
wire = RawWire
load_project_facade = ProjectFacade.load

__all__ = [
    "load_project",
    "save_project",
    "RawCircuit",
    "RawComponent",
    "RawWire",
    "RawAttribute",
    "Point",
    "LogicalCircuit",
    "LogicalInstance",
    "LogicalNet",
    "LogicalEndpoint",
    "extract_logical_circuit",
    "add_component",
    "add_splitter",
    "add_wire",
    "add_polyline",
    "add_tunnel",
    "add_tunnel_to_port",
    "add_tunnel_on_port",
    "find_component",
    "get_attribute",
    "set_attribute",
    "set_attributes",
    "port_point",
    "connect_ports_routed",
    "connect_points_routed",
    "get_component_geometry",
    "get_component_visual_bounds",
    "ProjectFacade",
    "CircuitEditor",
]
