from __future__ import annotations

from dataclasses import dataclass

from .model import RawAttribute, RawCircuit, RawComponent, RawWire


Point = tuple[int, int]


def pt(x: int, y: int) -> Point:
    return (x, y)


def hline(start: Point, end_x: int) -> list[RawWire]:
    return [RawWire(start=start, end=(end_x, start[1]))]


def vline(start: Point, end_y: int) -> list[RawWire]:
    return [RawWire(start=start, end=(start[0], end_y))]


def orthogonal(start: Point, end: Point, jog_x: int | None = None, jog_y: int | None = None) -> list[RawWire]:
    if start[0] == end[0] or start[1] == end[1]:
        return [RawWire(start=start, end=end)]
    if jog_x is not None:
        mid1 = (jog_x, start[1])
        mid2 = (jog_x, end[1])
        return [RawWire(start=start, end=mid1), RawWire(start=mid1, end=mid2), RawWire(start=mid2, end=end)]
    if jog_y is not None:
        mid1 = (start[0], jog_y)
        mid2 = (end[0], jog_y)
        return [RawWire(start=start, end=mid1), RawWire(start=mid1, end=mid2), RawWire(start=mid2, end=end)]
    mid = (end[0], start[1])
    return [RawWire(start=start, end=mid), RawWire(start=mid, end=end)]


def attr(name: str, value: str, as_text: bool = False) -> RawAttribute:
    return RawAttribute(name=name, value=value, as_text=as_text)


def component(name: str, loc: Point, attrs: dict[str, str] | None = None, *, lib: str | None = None) -> RawComponent:
    raw_attrs = [attr(k, v) for k, v in (attrs or {}).items()]
    return RawComponent(name=name, loc=loc, lib=lib, attrs=raw_attrs)


@dataclass(slots=True)
class CircuitBuilder:
    name: str
    components: list[RawComponent] | None = None
    wires: list[RawWire] | None = None

    def __post_init__(self) -> None:
        if self.components is None:
            self.components = []
        if self.wires is None:
            self.wires = []

    def add(self, comp: RawComponent) -> RawComponent:
        self.components.append(comp)
        return comp

    def add_wire(self, start: Point, end: Point) -> None:
        self.wires.append(RawWire(start=start, end=end))

    def add_path(self, start: Point, end: Point, *, jog_x: int | None = None, jog_y: int | None = None) -> None:
        self.wires.extend(orthogonal(start, end, jog_x=jog_x, jog_y=jog_y))

    def pin(
        self,
        loc: Point,
        *,
        facing: str,
        width: int,
        output: bool,
        label: str = "",
        labelloc: str = "north",
    ) -> RawComponent:
        return self.add(
            component(
                "Pin",
                loc,
                {
                    "facing": facing,
                    "output": "true" if output else "false",
                    "width": str(width),
                    "tristate": "true" if output else "false",
                    "pull": "none",
                    "label": label,
                    "labelloc": labelloc,
                    "labelfont": "Dialog plain 12",
                    "labelcolor": "#000000",
                },
                lib="0",
            )
        )

    def text(self, loc: Point, text_value: str, font: str = "SansSerif plain 12") -> RawComponent:
        comp = RawComponent(name="Text", loc=loc, lib="6", attrs=[
            RawAttribute("text", text_value, as_text=False),
            RawAttribute("font", font),
            RawAttribute("color", "#000000"),
            RawAttribute("halign", "center"),
            RawAttribute("valign", "base"),
        ])
        return self.add(comp)

    def probe(self, loc: Point, *, facing: str, width: int, label: str, radix: str) -> RawComponent:
        return self.add(
            component(
                "Probe",
                loc,
                {
                    "facing": facing,
                    "radix": radix,
                    "label": label,
                    "labelloc": "south" if facing == "north" else "north",
                    "labelfont": "Dialog plain 12",
                    "labelcolor": "#000000",
                    "width": str(width),
                },
                lib="0",
            )
        )

    def led(self, loc: Point, *, facing: str, label: str) -> RawComponent:
        return self.add(
            component(
                "LED",
                loc,
                {
                    "facing": facing,
                    "color": "#f00000",
                    "offcolor": "#ffffff",
                    "active": "true",
                    "label": label,
                    "labelloc": "east" if facing == "west" else "north",
                    "labelfont": "Dialog plain 12",
                    "labelcolor": "#000000",
                },
                lib="5",
            )
        )

    def clock(self, loc: Point) -> RawComponent:
        return self.add(
            component(
                "Clock",
                loc,
                {
                    "facing": "north",
                    "highDuration": "1",
                    "lowDuration": "1",
                    "label": "",
                    "labelloc": "west",
                    "labelfont": "Dialog plain 12",
                    "labelcolor": "#000000",
                },
                lib="0",
            )
        )

    def button(self, loc: Point, *, label: str) -> RawComponent:
        return self.add(
            component(
                "Button",
                loc,
                {
                    "facing": "north",
                    "color": "#ffffff",
                    "label": label,
                    "labelloc": "south",
                    "labelfont": "Dialog plain 12",
                    "labelcolor": "#000000",
                },
                lib="5",
            )
        )

    def constant(self, loc: Point, *, width: int, value: int, facing: str = "east") -> RawComponent:
        return self.add(
            component(
                "Constant",
                loc,
                {"facing": facing, "width": str(width), "value": hex(value)},
                lib="0",
            )
        )

    def comparator(self, loc: Point, *, width: int) -> RawComponent:
        return self.add(component("Comparator", loc, {"width": str(width), "mode": "twosComplement"}, lib="2"))

    def xor_gate(self, loc: Point, *, width: int, inputs: int = 2, size: int = 30) -> RawComponent:
        attrs = {
            "facing": "east",
            "width": str(width),
            "size": str(size),
            "inputs": str(inputs),
            "out": "01",
            "label": "",
            "labelfont": "Dialog plain 12",
            "labelcolor": "#000000",
            "xor": "odd",
        }
        for idx in range(inputs):
            attrs[f"negate{idx}"] = "false"
        return self.add(component("XOR Gate", loc, attrs, lib="1"))

    def random(self, loc: Point, *, width: int, seed: int = 0) -> RawComponent:
        return self.add(
            component(
                "Random",
                loc,
                {
                    "width": str(width),
                    "seed": str(seed),
                    "trigger": "rising",
                    "label": "随机信号",
                    "labelfont": "Dialog plain 12",
                    "labelcolor": "#000000",
                },
                lib="4",
            )
        )

    def rom(self, loc: Point, *, addr_width: int, data_width: int, contents_text: str) -> RawComponent:
        comp = RawComponent(
            name="ROM",
            loc=loc,
            lib="4",
            attrs=[
                RawAttribute("addrWidth", str(addr_width)),
                RawAttribute("dataWidth", str(data_width)),
                RawAttribute("label", ""),
                RawAttribute("labelfont", "Dialog plain 12"),
                RawAttribute("labelcolor", "#000000"),
                RawAttribute("contents", contents_text, as_text=True),
                RawAttribute("Select", "high"),
            ],
        )
        return self.add(comp)

    def subcircuit(self, name: str, loc: Point) -> RawComponent:
        return self.add(
            component(
                name,
                loc,
                {
                    "facing": "east",
                    "label": "",
                    "labelloc": "north",
                    "labelfont": "Dialog plain 12",
                    "labelcolor": "#000000",
                },
                lib=None,
            )
        )

    def build(self) -> RawCircuit:
        return RawCircuit(name=self.name, components=list(self.components), wires=list(self.wires))
