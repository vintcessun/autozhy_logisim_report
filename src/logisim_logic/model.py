from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Iterable, Iterator
import xml.etree.ElementTree as ET

from .java_types import Direction, EAST, NORTH, SOUTH, WEST, Location, format_attribute_value, parse_attribute_value


Point = tuple[int, int]
Bounds = tuple[int, int, int, int]


def point_to_location(point: Point) -> Location:
    return Location(point[0], point[1])


@dataclass(slots=True)
class XmlFragment:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    text: str = ""
    children: list["XmlFragment"] = field(default_factory=list)

    @classmethod
    def from_element(cls, elem: ET.Element) -> "XmlFragment":
        return cls(
            tag=elem.tag,
            attrs=dict(elem.attrib),
            text=elem.text or "",
            children=[cls.from_element(child) for child in list(elem)],
        )

    def to_element(self) -> ET.Element:
        elem = ET.Element(self.tag, dict(self.attrs))
        elem.text = self.text
        for child in self.children:
            elem.append(child.to_element())
        return elem

    def to_dict(self) -> dict[str, Any]:
        return {
            "tag": self.tag,
            "attrs": dict(self.attrs),
            "text": self.text,
            "children": [child.to_dict() for child in self.children],
        }


def _attr_map(attrs: list["RawAttribute"]) -> dict[str, str]:
    return {attr.name: attr.value for attr in attrs}


def _get_attr(attrs: list["RawAttribute"], name: str, default: str | None = None) -> str | None:
    for attr in attrs:
        if attr.name == name:
            return attr.value
    return default


def _set_attr(attrs: list["RawAttribute"], name: str, value: Any, *, as_text: bool | None = None) -> None:
    rendered = format_attribute_value(name, value)
    for attr in attrs:
        if attr.name == name:
            attr.value = rendered
            if as_text is not None:
                attr.as_text = as_text
            return
    attrs.append(RawAttribute(name=name, value=rendered, as_text=bool(as_text)))


def _delete_attr(attrs: list["RawAttribute"], name: str) -> list["RawAttribute"]:
    return [attr for attr in attrs if attr.name != name]


@dataclass(slots=True)
class RawAttribute:
    name: str
    value: str
    as_text: bool = False
    extra_attrs: dict[str, str] = field(default_factory=dict)

    def parsed(self) -> Any:
        return parse_attribute_value(self.name, self.value)

    def to_dict(self) -> dict[str, Any]:
        parsed = self.parsed()
        return {
            "name": self.name,
            "value": self.value,
            "parsed": str(parsed) if parsed != self.value else parsed,
            "as_text": self.as_text,
            "extra_attrs": dict(self.extra_attrs),
        }


@dataclass(slots=True)
class RawTool:
    name: str
    lib: str | None = None
    attrs: list[RawAttribute] = field(default_factory=list)
    extra_attrs: dict[str, str] = field(default_factory=dict)
    other_children: list[XmlFragment] = field(default_factory=list)

    def attr_map(self) -> dict[str, str]:
        return _attr_map(self.attrs)

    def get(self, name: str, default: str | None = None) -> str | None:
        return _get_attr(self.attrs, name, default)

    def get_typed(self, name: str, default: Any = None) -> Any:
        value = self.get(name)
        if value is None:
            return default
        return parse_attribute_value(name, value)

    def set(self, name: str, value: Any, *, as_text: bool | None = None) -> None:
        _set_attr(self.attrs, name, value, as_text=as_text)

    def delete(self, name: str) -> None:
        self.attrs = _delete_attr(self.attrs, name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "lib": self.lib,
            "attrs": [attr.to_dict() for attr in self.attrs],
            "extra_attrs": dict(self.extra_attrs),
            "other_children": [child.to_dict() for child in self.other_children],
        }


@dataclass(slots=True)
class RawLibrary:
    name: str
    desc: str
    tools: list[RawTool] = field(default_factory=list)
    extra_attrs: dict[str, str] = field(default_factory=dict)
    other_children: list[XmlFragment] = field(default_factory=list)

    def tool(self, name: str) -> RawTool:
        for tool in self.tools:
            if tool.name == name:
                return tool
        raise KeyError(name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "desc": self.desc,
            "tools": [tool.to_dict() for tool in self.tools],
            "extra_attrs": dict(self.extra_attrs),
            "other_children": [child.to_dict() for child in self.other_children],
        }


@dataclass(slots=True)
class RawOptions:
    attrs: list[RawAttribute] = field(default_factory=list)
    extra_attrs: dict[str, str] = field(default_factory=dict)
    other_children: list[XmlFragment] = field(default_factory=list)

    def attr_map(self) -> dict[str, str]:
        return _attr_map(self.attrs)

    def get(self, name: str, default: str | None = None) -> str | None:
        return _get_attr(self.attrs, name, default)

    def set(self, name: str, value: Any, *, as_text: bool | None = None) -> None:
        _set_attr(self.attrs, name, value, as_text=as_text)

    def delete(self, name: str) -> None:
        self.attrs = _delete_attr(self.attrs, name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "attrs": [attr.to_dict() for attr in self.attrs],
            "extra_attrs": dict(self.extra_attrs),
            "other_children": [child.to_dict() for child in self.other_children],
        }


@dataclass(slots=True)
class RawMappings:
    tools: list[RawTool] = field(default_factory=list)
    extra_attrs: dict[str, str] = field(default_factory=dict)
    other_children: list[XmlFragment] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tools": [tool.to_dict() for tool in self.tools],
            "extra_attrs": dict(self.extra_attrs),
            "other_children": [child.to_dict() for child in self.other_children],
        }


@dataclass(slots=True)
class RawToolbarItem:
    kind: str
    tool: RawTool | None = None
    fragment: XmlFragment | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "tool": None if self.tool is None else self.tool.to_dict(),
            "fragment": None if self.fragment is None else self.fragment.to_dict(),
        }


@dataclass(slots=True)
class RawToolbar:
    items: list[RawToolbarItem] = field(default_factory=list)
    extra_attrs: dict[str, str] = field(default_factory=dict)
    other_children: list[XmlFragment] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [item.to_dict() for item in self.items],
            "extra_attrs": dict(self.extra_attrs),
            "other_children": [child.to_dict() for child in self.other_children],
        }


@dataclass(slots=True)
class RawMain:
    name: str
    extra_attrs: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class RawMessage:
    value: str
    extra_attrs: dict[str, str] = field(default_factory=dict)
    text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"value": self.value, "extra_attrs": dict(self.extra_attrs), "text": self.text}


@dataclass(slots=True)
class RawAppearance:
    shapes: list[XmlFragment] = field(default_factory=list)
    extra_attrs: dict[str, str] = field(default_factory=dict)
    other_children: list[XmlFragment] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "shapes": [shape.to_dict() for shape in self.shapes],
            "extra_attrs": dict(self.extra_attrs),
            "other_children": [child.to_dict() for child in self.other_children],
        }


@dataclass(slots=True)
class RawWire:
    start: Point
    end: Point
    extra_attrs: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"start": self.start, "end": self.end, "extra_attrs": dict(self.extra_attrs)}


@dataclass(slots=True)
class RawComponent:
    name: str
    loc: Point
    lib: str | None = None
    attrs: list[RawAttribute] = field(default_factory=list)
    extra_attrs: dict[str, str] = field(default_factory=dict)
    other_children: list[XmlFragment] = field(default_factory=list)

    @property
    def type(self) -> str:
        return self.name

    @property
    def type_name(self) -> str:
        return self.name

    @property
    def label(self) -> str:
        for attr in self.attrs:
            if attr.name == "label":
                return attr.value or ""
        return ""

    @property
    def x(self) -> int:
        return self.loc.x

    @property
    def y(self) -> int:
        return self.loc.y

    @label.setter
    def label(self, value: str):
        for attr in self.attrs:
            if attr.name == "label":
                attr.value = value
                return
        from .model import RawAttribute
        self.attrs.append(RawAttribute(name="label", value=value))

    def attr_map(self) -> dict[str, str]:
        return _attr_map(self.attrs)

    def get(self, name: str, default: str | None = None) -> str | None:
        return _get_attr(self.attrs, name, default)

    def get_typed(self, name: str, default: Any = None) -> Any:
        value = self.get(name)
        if value is None:
            return default
        return parse_attribute_value(name, value)

    def set(self, name: str, value: Any, *, as_text: bool | None = None) -> None:
        _set_attr(self.attrs, name, value, as_text=as_text)

    def set_attribute(self, name: str, value: Any, *, as_text: bool | None = None) -> None:
        return self.set(name, value, as_text=as_text)

    def get_attribute(self, name: str, default: str | None = None) -> str | None:
        return self.get(name, default=default)

    def delete(self, name: str) -> None:
        self.attrs = _delete_attr(self.attrs, name)

    @property
    def location(self) -> Location:
        return point_to_location(self.loc)

    def get_location(self) -> Location:
        return self.location

    def get_bounds(self, project: RawProject | None = None) -> Bounds:
        from .layout import component_bounds
        return component_bounds(self, project=project)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "loc": self.loc,
            "lib": self.lib,
            "attrs": [attr.to_dict() for attr in self.attrs],
            "extra_attrs": dict(self.extra_attrs),
            "other_children": [child.to_dict() for child in self.other_children],
        }


@dataclass(slots=True)
class CircuitPort:
    name: str
    offset: Point
    pin_loc: Point
    direction: str
    width: str | None
    label: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "offset": self.offset,
            "pin_loc": self.pin_loc,
            "direction": self.direction,
            "width": self.width,
            "label": self.label,
        }


def shape_center(shape: XmlFragment) -> Location:
    x = float(shape.attrs.get("x", "0"))
    y = float(shape.attrs.get("y", "0"))
    width = float(shape.attrs.get("width", "0"))
    height = float(shape.attrs.get("height", "0"))
    return Location(round(x + width / 2.0), round(y + height / 2.0))


def rotate_bounds(bounds: Bounds, from_dir: Direction, to_dir: Direction, xc: int = 0, yc: int = 0) -> Bounds:
    x, y, wid, ht = bounds
    degrees = to_dir.degrees - from_dir.degrees
    while degrees >= 360:
        degrees -= 360
    while degrees < 0:
        degrees += 360
    dx = x - xc
    dy = y - yc
    if degrees == 90:
        return (xc + dy, yc - dx - wid, ht, wid)
    if degrees == 180:
        return (xc - dx - wid, yc - dy - ht, wid, ht)
    if degrees == 270:
        return (xc - dy - ht, yc + dx, ht, wid)
    return bounds


def _combine_bounds(first: Bounds | None, second: Bounds) -> Bounds:
    if first is None:
        return second
    min_x = min(first[0], second[0])
    min_y = min(first[1], second[1])
    max_x = max(first[0] + first[2], second[0] + second[2])
    max_y = max(first[1] + first[3], second[1] + second[3])
    return (min_x, min_y, max_x - min_x, max_y - min_y)


def _path_shape_bounds(shape: XmlFragment) -> Bounds | None:
    numbers = [float(value) for value in re.findall(r"-?\d+(?:\.\d+)?", shape.attrs.get("d", ""))]
    if len(numbers) < 2:
        return None
    xs = numbers[0::2]
    ys = numbers[1::2]
    min_x = round(min(xs))
    max_x = round(max(xs))
    min_y = round(min(ys))
    max_y = round(max(ys))
    return (min_x, min_y, max(1, max_x - min_x), max(1, max_y - min_y))


def _text_shape_bounds(shape: XmlFragment) -> Bounds:
    text = shape.text or ""
    lines = text.splitlines() or [""]
    try:
        font_size = max(1, round(float(shape.attrs.get("font-size", "12"))))
    except ValueError:
        font_size = 12
    width = max(1, round(max(len(line) for line in lines) * font_size * 0.6))
    height = max(1, round(len(lines) * font_size * 1.2))
    x = round(float(shape.attrs.get("x", "0")))
    y = round(float(shape.attrs.get("y", "0")))
    anchor = (shape.attrs.get("text-anchor") or "start").lower()
    if anchor == "middle":
        left = x - width // 2
    elif anchor == "end":
        left = x - width
    else:
        left = x
    top = y - font_size
    return (left, top, width, height)


def shape_bounds(shape: XmlFragment) -> Bounds | None:
    if shape.tag in {"rect", "circ-port"}:
        x = round(float(shape.attrs.get("x", "0")))
        y = round(float(shape.attrs.get("y", "0")))
        width = max(1, round(float(shape.attrs.get("width", "0"))))
        height = max(1, round(float(shape.attrs.get("height", "0"))))
        return (x, y, width, height)
    if shape.tag in {"circ-anchor", "circ-origin"}:
        loc = shape_center(shape)
        return (loc.x, loc.y, 1, 1)
    if shape.tag == "path":
        return _path_shape_bounds(shape)
    if shape.tag == "text":
        return _text_shape_bounds(shape)
    return None


def _compute_default_dimension(max_this: int, max_others: int) -> int:
    if max_this < 3:
        return 30
    if max_others == 0:
        return 10 * max_this
    return 10 * max_this + 10


def _compute_default_offset(num_facing: int, num_opposite: int, max_others: int) -> int:
    max_this = max(num_facing, num_opposite)
    if max_this in {0, 1}:
        max_offs = 15 if max_others == 0 else 10
    elif max_this == 2:
        max_offs = 10
    else:
        max_offs = 5 if max_others == 0 else 10
    return max_offs + 10 * ((max_this - num_facing) // 2)


def _place_default_ports(pins: Iterable[RawComponent], x: int, y: int, dx: int, dy: int) -> list[tuple[Location, RawComponent]]:
    placed: list[tuple[Location, RawComponent]] = []
    px, py = x, y
    for pin in pins:
        placed.append((Location(px, py), pin))
        px += dx
        py += dy
    return placed


@dataclass(slots=True)
class RawCircuit:
    name: str
    attrs: list[RawAttribute] = field(default_factory=list)
    other_children: list[XmlFragment] = field(default_factory=list)
    extra_attrs: dict[str, str] = field(default_factory=dict)
    components: list[RawComponent] = field(default_factory=list)
    wires: list[RawWire] = field(default_factory=list)
    appearances: list[RawAppearance] = field(default_factory=list)
    item_order: list[tuple[str, int]] = field(default_factory=list)

    def attr_map(self) -> dict[str, str]:
        return _attr_map(self.attrs)

    def get(self, name: str, default: str | None = None) -> str | None:
        return _get_attr(self.attrs, name, default)

    def set(self, name: str, value: Any, *, as_text: bool | None = None) -> None:
        _set_attr(self.attrs, name, value, as_text=as_text)

    def delete(self, name: str) -> None:
        self.attrs = _delete_attr(self.attrs, name)

    def find_components(self, *, name: str | None = None, lib: str | None = None) -> list[RawComponent]:
        return [
            component
            for component in self.components
            if (name is None or component.name == name) and (lib is None or component.lib == lib)
        ]

    def get_components(self, *, name: str | None = None, lib: str | None = None) -> list[RawComponent]:
        return self.find_components(name=name, lib=lib)

    def pin_components(self) -> list[RawComponent]:
        return [component for component in self.components if component.name == "Pin"]

    def iter_appearance_shapes(self) -> Iterator[XmlFragment]:
        for appearance in self.appearances:
            yield from appearance.shapes
            yield from appearance.other_children

    def resolved_item_order(self) -> list[tuple[str, int]]:
        groups = {"attr": self.attrs, "appear": self.appearances, "wire": self.wires, "comp": self.components, "other": self.other_children}
        return _resolve_item_order(groups, self.item_order, ("attr", "appear", "wire", "comp", "other"))

    def explicit_port_offsets(self, facing: str | Direction = EAST) -> list[CircuitPort]:
        facing_dir = Direction.parse(facing) if isinstance(facing, str) else facing
        pin_by_location = {component.location: component for component in self.pin_components()}
        anchor, default_facing = self._anchor_location_and_facing()
        found: list[tuple[Location, RawComponent]] = []
        for shape in self.iter_appearance_shapes():
            if shape.tag != "circ-port" or "pin" not in shape.attrs:
                continue
            try:
                pin_location = Location.parse(shape.attrs["pin"])
            except Exception:
                continue
            pin_component = pin_by_location.get(pin_location)
            if pin_component is None:
                continue
            found.append((shape_center(shape), pin_component))
        return self._ports_from_locations(found, anchor, default_facing, facing_dir)

    def default_port_offsets(self, facing: str | Direction = EAST) -> list[CircuitPort]:
        facing_dir = Direction.parse(facing) if isinstance(facing, str) else facing
        edges: dict[Direction, list[RawComponent]] = {NORTH: [], SOUTH: [], EAST: [], WEST: []}
        for pin in self.pin_components():
            pin_facing = pin.get_typed("facing", EAST)
            if not isinstance(pin_facing, Direction):
                pin_facing = Direction.parse(str(pin_facing))
            edges[pin_facing.reverse()].append(pin)
        for edge_dir, pins in edges.items():
            pins.sort(key=(lambda item: (item.loc[0], item.loc[1])) if edge_dir in {NORTH, SOUTH} else (lambda item: (item.loc[1], item.loc[0])))
        north, south, east, west = edges[NORTH], edges[SOUTH], edges[EAST], edges[WEST]
        max_vert = max(len(north), len(south))
        max_horz = max(len(east), len(west))
        offs_north = _compute_default_offset(len(north), len(south), max_horz)
        offs_south = _compute_default_offset(len(south), len(north), max_horz)
        offs_east = _compute_default_offset(len(east), len(west), max_vert)
        offs_west = _compute_default_offset(len(west), len(east), max_vert)
        width = _compute_default_dimension(max_vert, max_horz)
        height = _compute_default_dimension(max_horz, max_vert)
        if east:
            ax, ay = width, offs_east
        elif north:
            ax, ay = offs_north, 0
        elif west:
            ax, ay = 0, offs_west
        elif south:
            ax, ay = offs_south, height
        else:
            ax, ay = 0, 0
        rx = 50 + (9 - (ax + 9) % 10)
        ry = 50 + (9 - (ay + 9) % 10)
        anchor = Location(rx + ax, ry + ay)
        located = []
        located.extend(_place_default_ports(west, rx, ry + offs_west, 0, 10))
        located.extend(_place_default_ports(east, rx + width, ry + offs_east, 0, 10))
        located.extend(_place_default_ports(north, rx + offs_north, ry, 10, 0))
        located.extend(_place_default_ports(south, rx + offs_south, ry + height, 10, 0))
        return self._ports_from_locations(located, anchor, EAST, facing_dir)

    def port_offsets(self, facing: str | Direction = EAST) -> list[CircuitPort]:
        explicit = self.explicit_port_offsets(facing=facing)
        return explicit if explicit else self.default_port_offsets(facing=facing)

    def explicit_appearance_offset_bounds(self, facing: str | Direction = EAST) -> Bounds | None:
        facing_dir = Direction.parse(facing) if isinstance(facing, str) else facing
        bounds: Bounds | None = None
        anchor: Location | None = None
        default_facing = EAST
        for shape in self.iter_appearance_shapes():
            if shape.tag in {"circ-anchor", "circ-origin"}:
                anchor = shape_center(shape)
                try:
                    default_facing = Direction.parse(shape.attrs.get("facing", "east"))
                except Exception:
                    default_facing = EAST
            shape_bds = shape_bounds(shape)
            if shape_bds is not None:
                bounds = _combine_bounds(bounds, shape_bds)
        if bounds is None:
            return None
        if anchor is not None:
            bounds = (bounds[0] - anchor.x, bounds[1] - anchor.y, bounds[2], bounds[3])
        if facing_dir != default_facing:
            bounds = rotate_bounds(bounds, default_facing, facing_dir)
        return bounds

    def default_appearance_offset_bounds(self, facing: str | Direction = EAST) -> Bounds:
        facing_dir = Direction.parse(facing) if isinstance(facing, str) else facing
        edges: dict[Direction, list[RawComponent]] = {NORTH: [], SOUTH: [], EAST: [], WEST: []}
        for pin in self.pin_components():
            pin_facing = pin.get_typed("facing", EAST)
            if not isinstance(pin_facing, Direction):
                pin_facing = Direction.parse(str(pin_facing))
            edges[pin_facing.reverse()].append(pin)
        for edge_dir, pins in edges.items():
            pins.sort(key=(lambda item: (item.loc[0], item.loc[1])) if edge_dir in {NORTH, SOUTH} else (lambda item: (item.loc[1], item.loc[0])))
        north, south, east, west = edges[NORTH], edges[SOUTH], edges[EAST], edges[WEST]
        max_vert = max(len(north), len(south))
        max_horz = max(len(east), len(west))
        offs_north = _compute_default_offset(len(north), len(south), max_horz)
        offs_south = _compute_default_offset(len(south), len(north), max_horz)
        offs_east = _compute_default_offset(len(east), len(west), max_vert)
        offs_west = _compute_default_offset(len(west), len(east), max_vert)
        width = _compute_default_dimension(max_vert, max_horz)
        height = _compute_default_dimension(max_horz, max_vert)
        if east:
            ax, ay = width, offs_east
        elif north:
            ax, ay = offs_north, 0
        elif west:
            ax, ay = 0, offs_west
        elif south:
            ax, ay = offs_south, height
        else:
            ax, ay = 0, 0
        rx = 50 + (9 - (ax + 9) % 10)
        ry = 50 + (9 - (ay + 9) % 10)
        anchor = Location(rx + ax, ry + ay)
        bounds: Bounds | None = (rx, ry, width, height)
        placed = []
        placed.extend(_place_default_ports(west, rx, ry + offs_west, 0, 10))
        placed.extend(_place_default_ports(east, rx + width, ry + offs_east, 0, 10))
        placed.extend(_place_default_ports(north, rx + offs_north, ry, 10, 0))
        placed.extend(_place_default_ports(south, rx + offs_south, ry + height, 10, 0))
        for port_loc, pin in placed:
            radius = 5 if pin.get_typed("output", False) else 4
            bounds = _combine_bounds(bounds, (port_loc.x - radius, port_loc.y - radius, 2 * radius, 2 * radius))
        bounds = _combine_bounds(bounds, (anchor.x, anchor.y, 1, 1))
        bounds = (bounds[0] - anchor.x, bounds[1] - anchor.y, bounds[2], bounds[3])
        if facing_dir != EAST:
            bounds = rotate_bounds(bounds, EAST, facing_dir)
        return bounds

    def appearance_offset_bounds(self, facing: str | Direction = EAST) -> Bounds:
        explicit = self.explicit_appearance_offset_bounds(facing=facing)
        return explicit if explicit is not None else self.default_appearance_offset_bounds(facing=facing)

    def _anchor_location_and_facing(self) -> tuple[Location, Direction]:
        for shape in self.iter_appearance_shapes():
            if shape.tag not in {"circ-anchor", "circ-origin"}:
                continue
            facing = shape.attrs.get("facing", "east")
            try:
                return shape_center(shape), Direction.parse(facing)
            except Exception:
                return shape_center(shape), EAST
        return Location(100, 100), EAST

    def _ports_from_locations(
        self,
        located: list[tuple[Location, RawComponent]],
        anchor: Location,
        default_facing: Direction,
        facing_dir: Direction,
    ) -> list[CircuitPort]:
        result: list[CircuitPort] = []
        used_names: set[str] = set()
        for index, (port_loc, pin_component) in enumerate(sorted(located, key=lambda item: item[0])):
            offset = Location(port_loc.x - anchor.x, port_loc.y - anchor.y)
            if facing_dir != default_facing:
                offset = offset.rotate(default_facing, facing_dir, 0, 0)
            label = pin_component.get("label", "") or ""
            name = label if label and label not in used_names else f"p{index}"
            used_names.add(name)
            result.append(
                CircuitPort(
                    name=name,
                    offset=(offset.x, offset.y),
                    pin_loc=pin_component.loc,
                    direction="output" if pin_component.get_typed("output", False) else "input",
                    width=pin_component.get("width"),
                    label=label,
                )
            )
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "attrs": [attr.to_dict() for attr in self.attrs],
            "other_children": [child.to_dict() for child in self.other_children],
            "extra_attrs": dict(self.extra_attrs),
            "components": [component.to_dict() for component in self.components],
            "wires": [wire.to_dict() for wire in self.wires],
            "appearances": [appearance.to_dict() for appearance in self.appearances],
            "ports": [port.to_dict() for port in self.port_offsets()],
        }


def _resolve_item_order(groups: dict[str, list[Any]], existing: list[tuple[str, int]], fallback: tuple[str, ...]) -> list[tuple[str, int]]:
    order: list[tuple[str, int]] = []
    seen = {kind: set() for kind in groups}
    for kind, index in existing:
        group = groups.get(kind)
        if group is None or index < 0 or index >= len(group) or index in seen[kind]:
            continue
        order.append((kind, index))
        seen[kind].add(index)
    for kind in fallback:
        for index in range(len(groups[kind])):
            if index not in seen[kind]:
                order.append((kind, index))
    return order


@dataclass(slots=True)
class RawProject:
    root_attrs: dict[str, str]
    root_text: str
    circuits: list[RawCircuit]
    libraries: list[RawLibrary] = field(default_factory=list)
    main: RawMain | None = None
    options: RawOptions | None = None
    mappings: RawMappings | None = None
    toolbar: RawToolbar | None = None
    messages: list[RawMessage] = field(default_factory=list)
    other_root_children: list[XmlFragment] = field(default_factory=list)
    item_order: list[tuple[str, int]] = field(default_factory=list)

    def circuit_names(self) -> list[str]:
        return [circuit.name for circuit in self.circuits]

    def circuit(self, name: str) -> RawCircuit:
        for circuit in self.circuits:
            if circuit.name == name:
                return circuit
        raise KeyError(name)

    def has_circuit(self, name: str) -> bool:
        return any(circuit.name == name for circuit in self.circuits)

    @property
    def main_circuit_name(self) -> str | None:
        return None if self.main is None else self.main.name

    def set_main(self, name: str) -> None:
        if self.main is None:
            self.main = RawMain(name=name)
        else:
            self.main.name = name

    def resolved_item_order(self) -> list[tuple[str, int]]:
        groups = {
            "lib": self.libraries,
            "main": [] if self.main is None else [self.main],
            "options": [] if self.options is None else [self.options],
            "mappings": [] if self.mappings is None else [self.mappings],
            "toolbar": [] if self.toolbar is None else [self.toolbar],
            "message": self.messages,
            "circuit": self.circuits,
            "other": self.other_root_children,
        }
        return _resolve_item_order(groups, self.item_order, ("lib", "main", "options", "mappings", "toolbar", "message", "circuit", "other"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_attrs": dict(self.root_attrs),
            "root_text": self.root_text,
            "libraries": [library.to_dict() for library in self.libraries],
            "main": None if self.main is None else {"name": self.main.name, "extra_attrs": dict(self.main.extra_attrs)},
            "options": None if self.options is None else self.options.to_dict(),
            "mappings": None if self.mappings is None else self.mappings.to_dict(),
            "toolbar": None if self.toolbar is None else self.toolbar.to_dict(),
            "messages": [message.to_dict() for message in self.messages],
            "circuits": [circuit.to_dict() for circuit in self.circuits],
            "other_root_children": [child.to_dict() for child in self.other_root_children],
        }
