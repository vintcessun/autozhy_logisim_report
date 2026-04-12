from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from math import ceil
from pathlib import Path
import re
from typing import Any

from .java_types import Direction, EAST, NORTH, SOUTH, WEST
from .model import RawCircuit, RawComponent, RawProject

try:
    from PIL import ImageFont
except Exception:  # pragma: no cover - optional dependency fallback
    ImageFont = None


Point = tuple[int, int]
Bounds = tuple[int, int, int, int]
_WINDOWS_FONTS = Path(r"C:\Windows\Fonts")
_FONT_CANDIDATES = {
    "dialog": ["arial.ttf", "segoeui.ttf", "tahoma.ttf", "msyh.ttc", "simhei.ttf"],
    "sansserif": ["arial.ttf", "segoeui.ttf", "tahoma.ttf", "msyh.ttc", "simhei.ttf"],
    "serif": ["times.ttf", "timesbd.ttf", "simsun.ttc", "simhei.ttf"],
    "monospaced": ["consola.ttf", "cour.ttf", "simhei.ttf"],
}


@dataclass(frozen=True, slots=True)
class PortGeometry:
    name: str
    offset: Point
    direction: str
    width: str | None = None


@dataclass(frozen=True, slots=True)
class ComponentGeometry:
    bounds: Bounds
    ports: tuple[PortGeometry, ...]

    @property
    def width(self) -> int:
        return self.bounds[2]

    @property
    def height(self) -> int:
        return self.bounds[3]

    def absolute_bounds(self, loc: Point) -> Bounds:
        return (loc[0] + self.bounds[0], loc[1] + self.bounds[1], self.bounds[2], self.bounds[3])

    def port(self, name: str) -> PortGeometry:
        for port in self.ports:
            if port.name == name:
                return port
        raise KeyError(name)

    def absolute_port(self, loc: Point, name: str) -> Point:
        port = self.port(name)
        return (loc[0] + port.offset[0], loc[1] + port.offset[1])


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


def _rotate_point(point: Point, from_dir: Direction, to_dir: Direction) -> Point:
    x, y = point
    degrees = to_dir.degrees - from_dir.degrees
    while degrees >= 360:
        degrees -= 360
    while degrees < 0:
        degrees += 360
    if degrees == 90:
        return (y, -x)
    if degrees == 180:
        return (-x, -y)
    if degrees == 270:
        return (-y, x)
    return point


def _single_port(bounds: Bounds, direction: str, *, name: str = "io", width: str | None = None) -> ComponentGeometry:
    return ComponentGeometry(bounds=bounds, ports=(PortGeometry(name=name, offset=(0, 0), direction=direction, width=width),))


def _facing(component: RawComponent, default: str = "east") -> Direction:
    value = component.get("facing", default) or default
    return Direction.parse(value)


def _width(component: RawComponent, default: str | None = "1") -> str | None:
    return component.get("width", default)


def _int_attr(component: RawComponent, name: str, default: int) -> int:
    raw = component.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _font_size(component: RawComponent, attr_name: str = "labelfont", default: int = 12) -> int:
    raw = component.get(attr_name)
    if raw is None:
        return default
    match = re.search(r"(\d+)\s*$", raw)
    return int(match.group(1)) if match else default


def _font_family(component: RawComponent, attr_name: str = "labelfont", default: str = "Dialog") -> str:
    raw = component.get(attr_name)
    if raw is None:
        return default
    match = re.match(r"\s*(.+?)\s+(?:plain|bold|italic|bolditalic)\s+\d+\s*$", raw, flags=re.IGNORECASE)
    if match:
        return match.group(1)
    raw = raw.strip()
    return raw if raw else default


@lru_cache(maxsize=64)
def _resolve_font_path(family: str, prefer_wide_unicode: bool) -> str | None:
    family_key = family.strip().lower() or "dialog"
    names = list(_FONT_CANDIDATES.get(family_key, _FONT_CANDIDATES["dialog"]))
    if prefer_wide_unicode:
        names = ["msyh.ttc", "simhei.ttf", *names]
    seen: set[str] = set()
    for name in names:
        if name in seen:
            continue
        seen.add(name)
        path = _WINDOWS_FONTS / name
        if path.exists():
            return str(path)
    return None


@lru_cache(maxsize=128)
def _measure_text(text: str, family: str, size: int) -> tuple[int, int, int]:
    if not text:
        return (0, size, max(1, size // 4))
    lines = text.splitlines() or [text]
    prefer_wide_unicode = any(ord(ch) > 127 for ch in text)
    if ImageFont is not None:
        try:
            font_path = _resolve_font_path(family, prefer_wide_unicode)
            if font_path is not None:
                font = ImageFont.truetype(font_path, size=size)
            else:
                font = ImageFont.load_default()
            try:
                ascent, descent = font.getmetrics()
            except Exception:
                ascent, descent = size, max(1, size // 4)
            width = 0
            for line in lines:
                sample = line if line else " "
                bbox = font.getbbox(sample)
                width = max(width, max(0, bbox[2] - bbox[0]))
            return (width, ascent, descent)
        except Exception:
            pass
    width = max((ceil(len(line) * size * 0.6) for line in lines), default=0)
    return (width, size, max(1, size // 4))


def _text_bounds(text: str, x: int, y: int, halign: int, valign: int, *, family: str, size: int) -> Bounds:
    width, ascent, descent = _measure_text(text, family, size)
    height = ascent + descent
    left = x
    top = y
    if halign == 0:
        left -= width // 2
    elif halign == 1:
        left -= width
    if valign == 0:
        top -= ascent // 2
    elif valign == 3:
        top -= height // 2
    elif valign == 1:
        top -= ascent
    elif valign == 2:
        top -= height
    return (left, top, width, height)


def _combine_bounds(first: Bounds, second: Bounds) -> Bounds:
    min_x = min(first[0], second[0])
    min_y = min(first[1], second[1])
    max_x = max(first[0] + first[2], second[0] + second[2])
    max_y = max(first[1] + first[3], second[1] + second[3])
    return (min_x, min_y, max_x - min_x, max_y - min_y)


def _label_loc(component: RawComponent, default: str = "center") -> str:
    raw = (component.get("labelloc", default) or default).strip().lower()
    return raw if raw else default


def _default_label_bounds(component: RawComponent, body_bounds: Bounds) -> Bounds | None:
    label = component.get("label", "") or ""
    if not label or component.name in {"Tunnel", "Text"}:
        return None
    facing = _facing(component, "east")
    label_loc = _label_loc(component)
    x = body_bounds[0] + body_bounds[2] // 2
    y = body_bounds[1] + body_bounds[3] // 2
    halign = 0
    valign = 0
    if label_loc == "north":
        x = body_bounds[0] + body_bounds[2] // 2
        y = body_bounds[1] - 2
        halign = 0
        valign = 2
        if facing == NORTH:
            halign = -1
            x += 2
    elif label_loc == "south":
        x = body_bounds[0] + body_bounds[2] // 2
        y = body_bounds[1] + body_bounds[3] + 2
        halign = 0
        valign = -1
        if facing == SOUTH:
            halign = -1
            x += 2
    elif label_loc == "east":
        x = body_bounds[0] + body_bounds[2] + 2
        y = body_bounds[1] + body_bounds[3] // 2
        halign = -1
        valign = 0
        if facing == EAST:
            valign = 2
            y -= 2
    elif label_loc == "west":
        x = body_bounds[0] - 2
        y = body_bounds[1] + body_bounds[3] // 2
        halign = 1
        valign = 0
        if facing == WEST:
            valign = 2
            y -= 2
    elif label_loc == "center":
        x = body_bounds[0] + body_bounds[2] // 2
        y = body_bounds[1] + body_bounds[3] // 2
        halign = 0
        valign = 0
        if component.name == "Button":
            x = body_bounds[0] + (body_bounds[2] - 3) // 2
            y = body_bounds[1] + (body_bounds[3] - 3) // 2
    else:
        return None
    return _text_bounds(label, x, y, halign, valign, family=_font_family(component), size=_font_size(component))


def get_component_visual_bounds(component: RawComponent, *, project: RawProject | None = None) -> Bounds:
    body = get_component_geometry(component, project=project).absolute_bounds(component.loc)
    label_bounds = _default_label_bounds(component, body)
    return body if label_bounds is None else _combine_bounds(body, label_bounds)


def _probe_like_bounds(facing: Direction, width_value: int) -> Bounds:
    logical_len = max(1, width_value)
    if logical_len <= 2:
        east_wid = 20
        vertical_ht = 20
    elif logical_len <= 8:
        east_wid = 10 * logical_len
        vertical_ht = 20
    elif logical_len <= 16:
        east_wid = 80
        vertical_ht = 40
    elif logical_len <= 24:
        east_wid = 80
        vertical_ht = 60
    else:
        east_wid = 80
        vertical_ht = 80

    if facing == EAST:
        return (-east_wid, -10 if logical_len <= 8 else -vertical_ht // 2, east_wid, vertical_ht)
    if facing == WEST:
        return (0, -10 if logical_len <= 8 else -vertical_ht // 2, east_wid, vertical_ht)
    if facing == NORTH:
        return (-east_wid // 2, -20 if logical_len <= 8 else -vertical_ht, east_wid, vertical_ht)
    return (-east_wid // 2, 0, east_wid, vertical_ht)


def _pin_geometry(component: RawComponent) -> ComponentGeometry:
    facing = _facing(component, "east")
    width_value = int(component.get("width", "1") or "1")
    direction = "input" if component.get("output", "false") == "true" else "output"
    return _single_port(_probe_like_bounds(facing, width_value), direction, width=str(width_value))


def _probe_geometry(component: RawComponent) -> ComponentGeometry:
    facing = _facing(component, "east")
    width_value = int(component.get("width", "1") or "1")
    return _single_port(_probe_like_bounds(facing, width_value), "input", width=str(width_value))


def _clock_geometry(component: RawComponent) -> ComponentGeometry:
    facing = _facing(component, "east")
    return _single_port(_probe_like_bounds(facing, 1), "output", width="1")


def _button_geometry(component: RawComponent) -> ComponentGeometry:
    facing = _facing(component, "east")
    bounds = rotate_bounds((-20, -10, 20, 20), EAST, facing)
    return _single_port(bounds, "output", width="1")


def _led_geometry(component: RawComponent) -> ComponentGeometry:
    facing = _facing(component, "west")
    bounds = rotate_bounds((0, -10, 20, 20), WEST, facing)
    return _single_port(bounds, "input", width="1")


def _constant_geometry(component: RawComponent) -> ComponentGeometry:
    facing = _facing(component, "east")
    bounds = rotate_bounds((-20, -10, 20, 20), EAST, facing)
    return _single_port(bounds, "output", width=component.get("width", "1"))


def _random_geometry(component: RawComponent) -> ComponentGeometry:
    bounds = (-40, -20, 40, 40)
    return _single_port(bounds, "output", width=component.get("width", "8"))


def _tunnel_geometry(component: RawComponent) -> ComponentGeometry:
    facing = _facing(component, "east")
    label = component.get("label", "") or ""
    font_size = _font_size(component)
    font_family = _font_family(component)
    text_width, ascent, descent = _measure_text(label, font_family, font_size)
    text_height = ascent + descent
    margin = 3
    min_dim = 16 - 2 * margin
    body_width = max(min_dim, text_width)
    body_height = max(min_dim, text_height)
    if facing == NORTH:
        label_x, label_y, halign, valign = 0, 5, 0, -1
    elif facing == SOUTH:
        label_x, label_y, halign, valign = 0, -5, 0, 2
    elif facing == EAST:
        label_x, label_y, halign, valign = -5, 0, 1, 3
    else:
        label_x, label_y, halign, valign = 5, 0, -1, 3
    if halign == -1:
        bx = label_x
    elif halign == 1:
        bx = label_x - body_width
    else:
        bx = label_x - body_width // 2
    if valign == -1:
        by = label_y
    elif valign == 2:
        by = label_y - body_height
    else:
        by = label_y - body_height // 2
    bounds = (bx - margin, by - margin, body_width + 2 * margin, body_height + 2 * margin)
    bounds = _combine_bounds(bounds, (0, 0, 1, 1))
    return _single_port(bounds, "inout", width=component.get("width"))


def _comparator_geometry(component: RawComponent) -> ComponentGeometry:
    return ComponentGeometry(
        bounds=(-40, -20, 40, 40),
        ports=(
            PortGeometry("A", (-40, -10), "input", component.get("width", "8")),
            PortGeometry("B", (-40, 10), "input", component.get("width", "8")),
            PortGeometry("gt", (0, -10), "output", "1"),
            PortGeometry("eq", (0, 0), "output", "1"),
            PortGeometry("lt", (0, 10), "output", "1"),
        ),
    )


def _adder_geometry(component: RawComponent) -> ComponentGeometry:
    width = component.get("width", "8")
    return ComponentGeometry(
        bounds=(-40, -20, 40, 40),
        ports=(
            PortGeometry("A", (-40, -10), "input", width),
            PortGeometry("B", (-40, 10), "input", width),
            PortGeometry("out", (0, 0), "output", width),
            PortGeometry("cin", (-20, -20), "input", "1"),
            PortGeometry("cout", (-20, 20), "output", "1"),
        ),
    )


def _multiplier_geometry(component: RawComponent) -> ComponentGeometry:
    width = component.get("width", "8")
    return ComponentGeometry(
        bounds=(-40, -20, 40, 40),
        ports=(
            PortGeometry("A", (-40, -10), "input", width),
            PortGeometry("B", (-40, 10), "input", width),
            PortGeometry("out", (0, 0), "output", width),
            PortGeometry("cin", (-20, -20), "input", width),
            PortGeometry("cout", (-20, 20), "output", width),
        ),
    )


def _subtractor_geometry(component: RawComponent) -> ComponentGeometry:
    width = component.get("width", "8")
    return ComponentGeometry(
        bounds=(-40, -20, 40, 40),
        ports=(
            PortGeometry("A", (-40, -10), "input", width),
            PortGeometry("B", (-40, 10), "input", width),
            PortGeometry("out", (0, 0), "output", width),
            PortGeometry("bin", (-20, -20), "input", "1"),
            PortGeometry("bout", (-20, 20), "output", "1"),
        ),
    )


def _translate_point(point: Point, direction: Direction, distance: int, *, right: int = 0) -> Point:
    x, y = point
    if direction == EAST:
        return (x + distance, y + right)
    if direction == WEST:
        return (x - distance, y - right)
    if direction == SOUTH:
        return (x - right, y + distance)
    return (x + right, y - distance)


def _multiplexer_geometry(component: RawComponent) -> ComponentGeometry:
    facing = _facing(component, "east")
    select_width = _int_attr(component, "select", 1)
    inputs = 1 << select_width
    data_width = component.get("width", "1")
    select_loc = component.get("selloc", "bl") or "bl"
    select_mult = 1 if select_loc == "bl" else -1
    enable = (component.get("enable", "true") or "true").lower() == "true"

    ports: list[PortGeometry] = []
    if inputs == 2:
        east_bounds = (-30, -20, 30, 40)
        if facing == WEST:
            input_points = [(30, -10), (30, 10)]
            select_point = (20, select_mult * 20)
        elif facing == NORTH:
            input_points = [(-10, 30), (10, 30)]
            select_point = (select_mult * -20, 20)
        elif facing == SOUTH:
            input_points = [(-10, -30), (10, -30)]
            select_point = (select_mult * -20, -20)
        else:
            input_points = [(-30, -10), (-30, 10)]
            select_point = (-20, select_mult * 20)
    else:
        offs = -(inputs // 2) * 10 - 10
        length = inputs * 10 + 20
        east_bounds = (-40, offs, 40, length)
        dx = -(inputs // 2) * 10
        ddx = 10
        dy = -(inputs // 2) * 10
        ddy = 10
        if facing == WEST:
            dx = 40
            ddx = 0
            select_point = (20, select_mult * (dy + 10 * inputs))
        elif facing == NORTH:
            dy = 40
            ddy = 0
            select_point = (select_mult * dx, 20)
        elif facing == SOUTH:
            dy = -40
            ddy = 0
            select_point = (select_mult * dx, -20)
        else:
            dx = -40
            ddx = 0
            select_point = (-20, select_mult * (dy + 10 * inputs))
        input_points = [(dx + index * ddx, dy + index * ddy) for index in range(inputs)]

    for index, point in enumerate(input_points):
        ports.append(PortGeometry(f"in{index}", point, "input", data_width))
    ports.append(PortGeometry("select", select_point, "input", str(select_width)))
    if enable:
        enable_point = _translate_point(select_point, facing, 10)
        ports.append(PortGeometry("enable", enable_point, "input", "1"))
    ports.append(PortGeometry("out", (0, 0), "output", data_width))
    return ComponentGeometry(bounds=rotate_bounds(east_bounds, EAST, facing), ports=tuple(ports))


def _decoder_geometry(component: RawComponent) -> ComponentGeometry:
    facing = _facing(component, "east")
    select_width = _int_attr(component, "select", 1)
    outputs = 1 << select_width
    select_loc = component.get("selloc", "bl") or "bl"
    enable = (component.get("enable", "true") or "true").lower() == "true"

    if outputs == 2:
        reversed_dir = facing in {WEST, NORTH}
        if select_loc == "tr":
            reversed_dir = not reversed_dir
        y = 0 if reversed_dir else -40
        east_bounds = (-20, y, 30, 40)
        if facing in {NORTH, SOUTH}:
            port_y = -10 if facing == NORTH else 10
            if select_loc == "tr":
                output_points = [(-30, port_y), (-10, port_y)]
            else:
                output_points = [(10, port_y), (30, port_y)]
        else:
            port_x = -10 if facing == WEST else 10
            if select_loc == "tr":
                output_points = [(port_x, 10), (port_x, 30)]
            else:
                output_points = [(port_x, -30), (port_x, -10)]
    else:
        x = -20
        y = -10 if (facing in {WEST, NORTH}) ^ (select_loc == "tr") else -(outputs * 10 + 10)
        east_bounds = (x, y, 40, outputs * 10 + 20)
        if facing in {NORTH, SOUTH}:
            dy = -20 if facing == NORTH else 20
            dx = -10 * outputs if select_loc == "tr" else 0
            output_points = [(dx + index * 10, dy) for index in range(outputs)]
        else:
            dx = -20 if facing == WEST else 20
            dy = 0 if select_loc == "tr" else -10 * outputs
            output_points = [(dx, dy + index * 10) for index in range(outputs)]

    ports = [PortGeometry(f"out{index}", point, "output", "1") for index, point in enumerate(output_points)]
    ports.append(PortGeometry("select", (0, 0), "input", str(select_width)))
    if enable:
        enable_point = _translate_point((0, 0), facing, -10)
        ports.append(PortGeometry("enable", enable_point, "input", "1"))
    return ComponentGeometry(bounds=rotate_bounds(east_bounds, EAST, facing), ports=tuple(ports))


def _bit_extender_geometry(component: RawComponent) -> ComponentGeometry:
    ports = [
        PortGeometry("out", (0, 0), "output", component.get("out_width")),
        PortGeometry("in", (-40, 0), "input", component.get("in_width")),
    ]
    if (component.get("type") or "zero") == "input":
        ports.append(PortGeometry("extend", (-20, -20), "input", "1"))
    return ComponentGeometry(bounds=(-40, -20, 40, 40), ports=tuple(ports))


def _rom_geometry(component: RawComponent) -> ComponentGeometry:
    return ComponentGeometry(
        bounds=(-140, -40, 140, 80),
        ports=(
            PortGeometry("data", (0, 0), "inout", component.get("dataWidth")),
            PortGeometry("addr", (-140, 0), "input", component.get("addrWidth")),
            PortGeometry("cs", (-90, 40), "input", "1"),
        ),
    )


def _dot_matrix_geometry(component: RawComponent) -> ComponentGeometry:
    cols = int(component.get("matrixcols", "5") or "5")
    rows = int(component.get("matrixrows", "7") or "7")
    input_type = component.get("inputtype", "column") or "column"
    if input_type == "column":
        bounds = (-5, -10 * rows, 10 * cols, 10 * rows)
        ports = tuple(PortGeometry(f"col{idx}", (10 * idx, 0), "input", str(rows)) for idx in range(cols))
    elif input_type == "row":
        bounds = (0, -5, 10 * cols, 10 * rows)
        ports = tuple(PortGeometry(f"row{idx}", (0, 10 * idx), "input", str(cols)) for idx in range(rows))
    elif rows <= 1:
        bounds = (0, -5, 10 * cols, 10 * rows)
        ports = (PortGeometry("data", (0, 0), "input", str(cols)),)
    elif cols <= 1:
        bounds = (0, -5 * rows + 5, 10 * cols, 10 * rows)
        ports = (PortGeometry("data", (0, 0), "input", str(rows)),)
    else:
        bounds = (0, -5 * rows + 5, 10 * cols, 10 * rows)
        ports = (
            PortGeometry("cols", (0, 0), "input", str(cols)),
            PortGeometry("rows", (0, 10), "input", str(rows)),
        )
    return ComponentGeometry(bounds=bounds, ports=ports)


def _buffer_geometry(component: RawComponent) -> ComponentGeometry:
    facing = _facing(component, "east")
    bounds = {
        SOUTH: (-9, -20, 18, 20),
        NORTH: (-9, 0, 18, 20),
        WEST: (0, -9, 20, 18),
        EAST: (-20, -9, 20, 18),
    }[facing]
    input_offset = _rotate_point((-20, 0), EAST, facing)
    return ComponentGeometry(
        bounds=bounds,
        ports=(
            PortGeometry("out", (0, 0), "output", component.get("width", "1")),
            PortGeometry("in", input_offset, "input", component.get("width", "1")),
        ),
    )


def _not_gate_geometry(component: RawComponent) -> ComponentGeometry:
    facing = _facing(component, "east")
    size = int(component.get("size", "30") or "30")
    if size <= 20:
        east = (-20, -9, 20, 18)
        input_offset = (-20, 0)
    else:
        east = (-30, -9, 30, 18)
        input_offset = (-30, 0)
    return ComponentGeometry(
        bounds=rotate_bounds(east, EAST, facing),
        ports=(
            PortGeometry("out", (0, 0), "output", component.get("width", "1")),
            PortGeometry("in", _rotate_point(input_offset, EAST, facing), "input", component.get("width", "1")),
        ),
    )


_ABSTRACT_GATE_KINDS = {
    "AND Gate",
    "OR Gate",
    "XOR Gate",
    "NAND Gate",
    "NOR Gate",
    "XNOR Gate",
    "Odd Parity",
    "Odd Parity Gate",
}


def _register_geometry(component: RawComponent) -> ComponentGeometry:
    width = component.get("width", "8")
    # Logisim Register: Data West, Clock South, Out East, EN South, CLR South
    return ComponentGeometry(
        bounds=(-30, -20, 30, 40),
        ports=(
            PortGeometry("in", (-30, 0), "input", width),
            PortGeometry("out", (0, 0), "output", width),
            PortGeometry("cp", (-20, 20), "input", "1"),
            PortGeometry("en", (-10, 20), "input", "1"),
            PortGeometry("clr", (0, 20), "input", "1"),
        ),
    )


def _counter_geometry(component: RawComponent) -> ComponentGeometry:
    width = component.get("width", "8")
    # Logisim Counter: Clock West(ish), Load West, Out East, EN South, CLR South
    return ComponentGeometry(
        bounds=(-40, -20, 40, 40),
        ports=(
            PortGeometry("out", (0, 0), "output", width),
            PortGeometry("in", (-40, 0), "input", width),
            PortGeometry("cp", (-40, 10), "input", "1"),
            PortGeometry("load", (-40, -10), "input", "1"),
            PortGeometry("en", (-20, 20), "input", "1"),
            PortGeometry("clr", (-10, 20), "input", "1"),
            PortGeometry("up", (-30, 20), "input", "1"),
        ),
    )


def _d_flip_flop_geometry(component: RawComponent) -> ComponentGeometry:
    # Logisim D Flip-Flop: D West, Clock West, Q East, ~Q East, PRE North, CLR South
    return ComponentGeometry(
        bounds=(-40, -20, 40, 40),
        ports=(
            PortGeometry("D", (-40, -10), "input", "1"),
            PortGeometry("cp", (-40, 10), "input", "1"),
            PortGeometry("Q", (0, -10), "output", "1"),
            PortGeometry("~Q", (0, 10), "output", "1"),
            PortGeometry("pre", (-20, -20), "input", "1"),
            PortGeometry("clr", (-20, 20), "input", "1"),
        ),
    )


def _abstract_gate_geometry(component: RawComponent) -> ComponentGeometry:
    facing = _facing(component, "east")
    size = int(component.get("size", "30") or "30")
    inputs = int(component.get("inputs", "2") or "2")
    effective_inputs = inputs + 1 if inputs % 2 == 0 else inputs
    bonus_width = 10 if component.name in {"XOR Gate", "XNOR Gate"} else 0
    negate_output = component.name in {"NAND Gate", "NOR Gate", "XNOR Gate"}
    negated_mask = 0
    for index in range(inputs):
        if (component.get(f"negate{index}", "false") or "false").lower() == "true":
            negated_mask |= 1 << index
    axis_length = size + bonus_width + (10 if negate_output else 0)
    width = axis_length + (10 if negated_mask else 0)
    height = max(10 * effective_inputs, size)
    east_bounds = (-width, -height // 2, width, height)
    if inputs <= 3:
        if size < 40:
            skip_start, skip_dist, skip_lower_even = -5, 10, 10
        elif size < 60 or inputs <= 2:
            skip_start, skip_dist, skip_lower_even = -10, 20, 20
        else:
            skip_start, skip_dist, skip_lower_even = -15, 30, 30
    elif inputs == 4 and size >= 60:
        skip_start, skip_dist, skip_lower_even = -5, 20, 0
    else:
        skip_start, skip_dist, skip_lower_even = -5, 10, 10
    ports = [PortGeometry("out", (0, 0), "output", component.get("width", "1"))]
    for index in range(inputs):
        if inputs & 1:
            dy = skip_start * (inputs - 1) + skip_dist * index
        else:
            dy = skip_start * inputs + skip_dist * index
            if index >= inputs // 2:
                dy += skip_lower_even
        dx = axis_length + (10 if negated_mask & (1 << index) else 0)
        offset = _rotate_point((-dx, dy), EAST, facing)
        ports.append(PortGeometry(f"in{index}", offset, "input", component.get("width", "1")))
    return ComponentGeometry(bounds=rotate_bounds(east_bounds, EAST, facing), ports=tuple(ports))


def _splitter_default_distribution(fanout: int, bits: int) -> list[int]:
    if fanout <= 0:
        return [0] * bits
    if fanout >= bits:
        return [index + 1 for index in range(bits)]
    threads_per_end = bits // fanout
    ends_with_extra = bits % fanout
    cur_end = -1
    left_in_end = 0
    result: list[int] = []
    for _ in range(bits):
        if left_in_end == 0:
            cur_end += 1
            left_in_end = threads_per_end
            if ends_with_extra > 0:
                left_in_end += 1
                ends_with_extra -= 1
        result.append(cur_end + 1)
        left_in_end -= 1
    return result


def _splitter_parameters(facing: Direction, fanout: int, appear: str) -> tuple[int, int, int, int, int, int, int, int, int, int]:
    justify = 0 if appear in {"center", "legacy"} else (1 if appear == "right" else -1)
    width = 20
    offs = 6
    if facing in {NORTH, SOUTH}:
        m = 1 if facing == NORTH else -1
        dx_end0 = 10 * (((fanout + 1) // 2) - 1) if justify == 0 else (-10 if m * justify < 0 else 10 * fanout)
        dy_end0 = -m * width
        ddx_end = -10
        ddy_end = 0
        dx_end_spine = 0
        dy_end_spine = m * (width - offs)
        dx_spine0 = m * justify * (10 * fanout - 1)
        dy_spine0 = -m * offs
        dx_spine1 = m * justify * offs
        dy_spine1 = -m * offs
    else:
        m = -1 if facing == WEST else 1
        dx_end0 = m * width
        dy_end0 = -10 * (fanout // 2) if justify == 0 else (10 if m * justify > 0 else -10 * fanout)
        ddx_end = 0
        ddy_end = 10
        dx_end_spine = -m * (width - offs)
        dy_end_spine = 0
        dx_spine0 = m * offs
        dy_spine0 = m * justify * (10 * fanout - 1)
        dx_spine1 = m * offs
        dy_spine1 = m * justify * offs
    return (dx_end0, dy_end0, ddx_end, ddy_end, dx_end_spine, dy_end_spine, dx_spine0, dy_spine0, dx_spine1, dy_spine1)


def _splitter_geometry(component: RawComponent) -> ComponentGeometry:
    print(f"DEBUG Splitter: {component.attrs}")
    facing = _facing(component, "east")
    incoming = _int_attr(component, "incoming", 2)
    fanout = _int_attr(component, "fanout", 2)
    appear = component.get("appear", "left") or "left"
    defaults = _splitter_default_distribution(fanout, incoming)
    bit_targets: list[int] = []
    for bit in range(incoming):
        raw = component.get(f"bit{bit}")
        if raw is None:
            target = defaults[bit]
        elif raw == "none":
            target = 0
        else:
            target = int(raw) + 1
        bit_targets.append(target)
    end_widths = [0] * (fanout + 1)
    end_widths[0] = incoming
    for target in bit_targets:
        if 1 <= target <= fanout:
            end_widths[target] += 1
    (
        dx_end0,
        dy_end0,
        ddx_end,
        ddy_end,
        dx_end_spine,
        dy_end_spine,
        dx_spine0,
        dy_spine0,
        dx_spine1,
        dy_spine1,
    ) = _splitter_parameters(facing, fanout, appear)
    ports = [PortGeometry("combined", (0, 0), "inout", str(incoming))]
    points = [(0, 0), (dx_spine0, dy_spine0), (dx_spine1, dy_spine1)]
    for index in range(fanout):
        x = dx_end0 + index * ddx_end
        y = dy_end0 + index * ddy_end
        points.append((x, y))
        points.append((x + dx_end_spine, y + dy_end_spine))
        ports.append(PortGeometry(f"out{index}", (x, y), "inout", str(end_widths[index + 1])))
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    min_x = min(xs)
    max_x = max(xs)
    min_y = min(ys)
    max_y = max(ys)
    bounds = (min_x, min_y, max(1, max_x - min_x + 1), max(1, max_y - min_y + 1))
    return ComponentGeometry(bounds=bounds, ports=tuple(ports))


def _subcircuit_geometry(component: RawComponent, project: RawProject) -> ComponentGeometry:
    target = project.circuit(component.name)
    facing = _facing(component, "east")
    ports = []
    for port in target.port_offsets(facing=facing):
        ports.append(PortGeometry(port.name, port.offset, port.direction, port.width))
    bounds = target.appearance_offset_bounds(facing=facing)
    return ComponentGeometry(bounds=bounds, ports=tuple(ports))


def get_component_geometry(component: RawComponent, project: RawProject | None = None) -> ComponentGeometry:
    if component.lib is None and project is not None and project.has_circuit(component.name):
        return _subcircuit_geometry(component, project)
    if component.name == "Pin":
        return _pin_geometry(component)
    if component.name == "Probe":
        return _probe_geometry(component)
    if component.name == "Clock":
        return _clock_geometry(component)
    if component.name == "Button":
        return _button_geometry(component)
    if component.name == "LED":
        return _led_geometry(component)
    if component.name == "Constant":
        return _constant_geometry(component)
    if component.name == "Random":
        return _random_geometry(component)
    if component.name == "Tunnel":
        return _tunnel_geometry(component)
    if component.name == "Comparator":
        return _comparator_geometry(component)
    if component.name == "Adder":
        return _adder_geometry(component)
    if component.name == "Subtractor":
        return _subtractor_geometry(component)
    if component.name == "Multiplier":
        return _multiplier_geometry(component)
    if component.name == "Multiplexer":
        return _multiplexer_geometry(component)
    if component.name == "Decoder":
        return _decoder_geometry(component)
    if component.name == "Splitter":
        return _splitter_geometry(component)
    if component.name == "Register":
        return _register_geometry(component)
    if component.name == "Counter":
        return _counter_geometry(component)
    if component.name == "D Flip-Flop":
        return _d_flip_flop_geometry(component)
    if component.name == "Bit Extender":
        return _bit_extender_geometry(component)
    if component.name == "ROM":
        return _rom_geometry(component)
    if component.name == "DotMatrix":
        return _dot_matrix_geometry(component)
    if component.name == "Buffer":
        return _buffer_geometry(component)
    if component.name == "NOT Gate":
        return _not_gate_geometry(component)
    if component.name in _ABSTRACT_GATE_KINDS:
        return _abstract_gate_geometry(component)
    if component.name in {"Power", "Ground", "Pull Resistor"}:
        return _single_port((-20, -20, 20, 20), "output" if component.name in {"Power", "Ground"} else "input", width=component.get("width", "1"))
    return _single_port((-20, -20, 40, 40), "inout", width=component.get("width"))


def resolve_library_label(project: RawProject | None, kind: str) -> str | None:
    if project is None:
        return _FALLBACK_LIBS.get(kind)
    if project.has_circuit(kind):
        return None
    for library in project.libraries:
        if any(tool.name == kind for tool in library.tools):
            return library.name
    return _FALLBACK_LIBS.get(kind)


_FALLBACK_LIBS = {
    "Pin": "0",
    "Probe": "0",
    "Tunnel": "0",
    "Pull Resistor": "0",
    "Clock": "0",
    "Constant": "0",
    "Power": "0",
    "Ground": "0",
    "Bit Extender": "0",
    "AND Gate": "1",
    "OR Gate": "1",
    "XOR Gate": "1",
    "NAND Gate": "1",
    "NOR Gate": "1",
    "XNOR Gate": "1",
    "Odd Parity": "1",
    "NOT Gate": "1",
    "Buffer": "1",
    "Comparator": "2",
    "Multiplexer": "2",
    "Decoder": "2",
    "Adder": "3",
    "Subtractor": "3",
    "Multiplier": "3",
    "Random": "4",
    "ROM": "4",
    "Button": "5",
    "LED": "5",
    "DotMatrix": "5",
    "Text": "6",
    "Splitter": "0",
}
