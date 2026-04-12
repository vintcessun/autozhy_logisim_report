from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any


@dataclass(frozen=True, slots=True)
class Direction:
    name: str
    degrees: int

    def __str__(self) -> str:
        return self.name

    @property
    def radians(self) -> float:
        return math.radians(self.degrees)

    def reverse(self) -> "Direction":
        return {
            "east": WEST,
            "west": EAST,
            "north": SOUTH,
            "south": NORTH,
        }[self.name]

    def get_right(self) -> "Direction":
        return {
            "east": SOUTH,
            "west": NORTH,
            "north": EAST,
            "south": WEST,
        }[self.name]

    def get_left(self) -> "Direction":
        return {
            "east": NORTH,
            "west": SOUTH,
            "north": WEST,
            "south": EAST,
        }[self.name]

    @classmethod
    def parse(cls, value: str) -> "Direction":
        normalized = value.strip().lower()
        try:
            return DIRECTIONS[normalized]
        except KeyError as exc:
            raise ValueError(f"illegal direction {value!r}") from exc


EAST = Direction("east", 0)
WEST = Direction("west", 180)
NORTH = Direction("north", 90)
SOUTH = Direction("south", 270)
DIRECTIONS = {
    "east": EAST,
    "west": WEST,
    "north": NORTH,
    "south": SOUTH,
}


@dataclass(frozen=True, order=True, slots=True)
class Location:
    x: int
    y: int

    def __str__(self) -> str:
        return f"({self.x},{self.y})"

    @classmethod
    def parse(cls, value: str) -> "Location":
        base = value
        text = value.strip()
        if not text:
            raise ValueError("location string cannot be empty")
        if text[0] == "(":
            if text[-1] != ")":
                raise ValueError(f"invalid point {base!r}")
            text = text[1:-1]
        comma = text.find(",")
        if comma < 0:
            comma = text.find(" ")
        if comma < 0:
            raise ValueError(f"invalid point {base!r}")
        x = int(text[:comma].strip())
        y = int(text[comma + 1 :].strip())
        return cls(x, y)

    def translate(
        self,
        dx_or_dir: int | Direction,
        dy: int = 0,
        right: int = 0,
    ) -> "Location":
        if isinstance(dx_or_dir, Direction):
            direction = dx_or_dir
            dist = dy
            if direction == EAST:
                return Location(self.x + dist, self.y + right)
            if direction == WEST:
                return Location(self.x - dist, self.y - right)
            if direction == SOUTH:
                return Location(self.x - right, self.y + dist)
            return Location(self.x + right, self.y - dist)
        return Location(self.x + dx_or_dir, self.y + dy)

    def rotate(self, from_dir: Direction, to_dir: Direction, xc: int, yc: int) -> "Location":
        degrees = to_dir.degrees - from_dir.degrees
        while degrees >= 360:
            degrees -= 360
        while degrees < 0:
            degrees += 360
        dx = self.x - xc
        dy = self.y - yc
        if degrees == 90:
            return Location(xc + dy, yc - dx)
        if degrees == 180:
            return Location(xc - dx, yc - dy)
        if degrees == 270:
            return Location(xc - dy, yc + dx)
        return self


@dataclass(frozen=True, order=True, slots=True)
class BitWidth:
    width: int

    def __post_init__(self) -> None:
        if self.width < 0:
            raise ValueError(f"width {self.width} must be non-negative")

    def __str__(self) -> str:
        return str(self.width)

    @classmethod
    def parse(cls, value: str) -> "BitWidth":
        text = value.strip()
        if text.startswith("/"):
            text = text[1:]
        width = int(text)
        if width < 0:
            raise ValueError(f"width {value!r} must be non-negative")
        return cls(width)


@dataclass(frozen=True, slots=True)
class AttributeOption:
    value: Any
    name: str | None = None

    def __post_init__(self) -> None:
        if self.name is None:
            object.__setattr__(self, "name", str(self.value))

    def __str__(self) -> str:
        return self.name or str(self.value)


@dataclass(frozen=True, slots=True)
class LogisimFont:
    family: str
    style: str
    size: int

    def __str__(self) -> str:
        return f"{self.family} {self.style} {self.size}"

    @classmethod
    def parse(cls, value: str) -> "LogisimFont":
        text = value.strip()
        parts = text.rsplit(" ", 2)
        if len(parts) != 3:
            raise ValueError(f"invalid font {value!r}")
        family, style, size = parts
        return cls(family, style, int(size))


@dataclass(frozen=True, slots=True)
class LogisimColor:
    red: int
    green: int
    blue: int
    alpha: int = 255

    def __str__(self) -> str:
        base = f"#{self.red:02x}{self.green:02x}{self.blue:02x}"
        if self.alpha != 255:
            return f"{base}{self.alpha:02x}"
        return base

    @classmethod
    def parse(cls, value: str) -> "LogisimColor":
        text = value.strip()
        if text.startswith("#") and len(text) in {7, 9}:
            red = int(text[1:3], 16)
            green = int(text[3:5], 16)
            blue = int(text[5:7], 16)
            alpha = 255 if len(text) == 7 else int(text[7:9], 16)
            return cls(red, green, blue, alpha)
        decoded = int(text, 0)
        if decoded < 0:
            decoded &= 0xFFFFFFFF
        if decoded <= 0xFFFFFF:
            return cls((decoded >> 16) & 0xFF, (decoded >> 8) & 0xFF, decoded & 0xFF)
        return cls((decoded >> 16) & 0xFF, (decoded >> 8) & 0xFF, decoded & 0xFF, (decoded >> 24) & 0xFF)


class _Codec:
    def parse(self, value: str) -> Any:
        raise NotImplementedError

    def format(self, value: Any) -> str:
        return str(value)


class _StringCodec(_Codec):
    def parse(self, value: str) -> str:
        return value


class _BoolCodec(_Codec):
    def parse(self, value: str) -> bool:
        return value.strip().lower() == "true"

    def format(self, value: Any) -> str:
        return "true" if bool(value) else "false"


class _IntCodec(_Codec):
    def parse(self, value: str) -> int:
        return int(value.strip())


class _IntegerBaseCodec(_Codec):
    def parse(self, value: str) -> int:
        text = value.strip().lower()
        if text.startswith("0x"):
            return int(text[2:], 16)
        if text.startswith("0b"):
            return int(text[2:], 2)
        if len(text) > 1 and text.startswith("0"):
            return int(text[1:], 8)
        return int(text, 10)

    def format(self, value: Any) -> str:
        return hex(int(value))


class _DirectionCodec(_Codec):
    def parse(self, value: str) -> Direction:
        return Direction.parse(value)

    def format(self, value: Any) -> str:
        if isinstance(value, Direction):
            return value.name
        return Direction.parse(str(value)).name


class _BitWidthCodec(_Codec):
    def parse(self, value: str) -> BitWidth:
        return BitWidth.parse(value)

    def format(self, value: Any) -> str:
        if isinstance(value, BitWidth):
            return str(value.width)
        return str(BitWidth.parse(str(value)).width)


class _FontCodec(_Codec):
    def parse(self, value: str) -> LogisimFont:
        return LogisimFont.parse(value)

    def format(self, value: Any) -> str:
        if isinstance(value, LogisimFont):
            return str(value)
        return str(LogisimFont.parse(str(value)))


class _ColorCodec(_Codec):
    def parse(self, value: str) -> LogisimColor:
        return LogisimColor.parse(value)

    def format(self, value: Any) -> str:
        if isinstance(value, LogisimColor):
            return str(value)
        return str(LogisimColor.parse(str(value)))


class _LocationCodec(_Codec):
    def parse(self, value: str) -> Location:
        return Location.parse(value)

    def format(self, value: Any) -> str:
        if isinstance(value, Location):
            return str(value)
        return str(Location.parse(str(value)))


STRING_CODEC = _StringCodec()
BOOL_CODEC = _BoolCodec()
INT_CODEC = _IntCodec()
INTEGER_BASE_CODEC = _IntegerBaseCodec()
DIRECTION_CODEC = _DirectionCodec()
BITWIDTH_CODEC = _BitWidthCodec()
FONT_CODEC = _FontCodec()
COLOR_CODEC = _ColorCodec()
LOCATION_CODEC = _LocationCodec()

_INTEGER_NAMES = {
    "addrWidth",
    "dataWidth",
    "fanout",
    "highDuration",
    "incoming",
    "inputs",
    "lowDuration",
    "nState",
    "seed",
    "simlimit",
    "simrand",
    "size",
}
_DIRECTION_NAMES = {"facing", "gate", "labelloc", "clabelup"}
_BITWIDTH_NAMES = {"in_width", "out_width", "width"}
_BOOLEAN_NAMES = {"active", "output", "tristate"}
_LOCATION_NAMES = {"loc", "pin"}
_BIT_INDEX_RE = re.compile(r"bit\d+\Z")
_NEGATE_RE = re.compile(r"negate\d+\Z")


def infer_codec(name: str) -> _Codec:
    if name in _DIRECTION_NAMES:
        return DIRECTION_CODEC
    if name in _BITWIDTH_NAMES:
        return BITWIDTH_CODEC
    if name in _BOOLEAN_NAMES or _NEGATE_RE.match(name):
        return BOOL_CODEC
    if name in {"color", "offcolor", "labelcolor"} or name.endswith("color"):
        return COLOR_CODEC
    if name in {"font", "labelfont", "clabelfont"} or name.endswith("font"):
        return FONT_CODEC
    if name in _LOCATION_NAMES:
        return LOCATION_CODEC
    if name == "value":
        return INTEGER_BASE_CODEC
    if name in _INTEGER_NAMES or _BIT_INDEX_RE.match(name):
        return INT_CODEC
    return STRING_CODEC


def parse_attribute_value(name: str, value: str) -> Any:
    codec = infer_codec(name)
    try:
        return codec.parse(value)
    except Exception:
        return value


def format_attribute_value(name: str, value: Any) -> str:
    if isinstance(value, str):
        return value
    codec = infer_codec(name)
    try:
        return codec.format(value)
    except Exception:
        return str(value)
