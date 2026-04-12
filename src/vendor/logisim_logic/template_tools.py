from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .logic_builder import LogicCircuitBuilder
from .model import RawCircuit, RawComponent, RawProject
from .rebuild_support import (
    attach_bit_extender_to_port,
    attach_component_to_port,
    attach_subcircuit_to_port,
    attrs_dict,
    connect_pin_to_tunnel,
    set_attr,
)
from .rom import rom_contents_from_words
from .selection import ComponentSelector, SelectorView, selector_view


SelectorSource = Literal["base", "current"]


def _side_tunnel_spec(side: str) -> tuple[int, int, str]:
    side_offsets = {
        "left": (-30, 0, "east"),
        "right": (30, 0, "west"),
        "top": (0, -30, "south"),
        "bottom": (0, 30, "north"),
    }
    try:
        return side_offsets[side]
    except KeyError as exc:
        raise ValueError(f"unsupported side {side!r}") from exc


@dataclass(slots=True)
class CircuitTemplate:
    base_circuit: RawCircuit
    circuit: RawCircuit
    selectors: dict[str, ComponentSelector]

    @property
    def base(self) -> SelectorView:
        return selector_view(self.base_circuit, self.selectors)

    @property
    def current(self) -> SelectorView:
        return selector_view(self.circuit, self.selectors)

    def view(self, source: SelectorSource = "current") -> SelectorView:
        return self.base if source == "base" else self.current

    def component(self, key: str, *, source: SelectorSource = "current") -> RawComponent:
        return self.view(source).component(key)

    def components(self, key: str, *, source: SelectorSource = "current") -> list[RawComponent]:
        return self.view(source).components(key)

    def attrs_copy(self, key: str, *, source: SelectorSource = "base") -> dict[str, str]:
        return attrs_dict(self.component(key, source=source))

    def loc(self, key: str, *, source: SelectorSource = "base") -> tuple[int, int]:
        return self.component(key, source=source).loc

    def set_attrs(self, key: str, /, **attrs: object) -> RawComponent:
        return self.current.set_attrs(key, **attrs)

    def set_width(self, key: str, width: int | str) -> RawComponent:
        return self.current.set_width(key, width)

    def set_probe(
        self,
        key: str,
        *,
        width: int | str | None = None,
        label: str | None = None,
        radix: str | None = None,
        facing: str | None = None,
    ) -> RawComponent:
        return self.current.set_probe(key, width=width, label=label, radix=radix, facing=facing)

    def replace_text(self, key: str, text: str) -> RawComponent:
        return self.current.replace_text(key, text)

    def splitter_extract(self, key: str, *, incoming: int, selected: list[int]) -> RawComponent:
        return self.current.splitter_extract(key, incoming=incoming, selected=selected)

    def sorted_components(
        self,
        *keys: str,
        source: SelectorSource = "current",
        axis: Literal["x", "y"] = "y",
    ) -> list[RawComponent]:
        index = 0 if axis == "x" else 1
        return sorted((self.component(key, source=source) for key in keys), key=lambda comp: comp.loc[index])

    def add_builder_instance(
        self,
        builder: LogicCircuitBuilder,
        instance_id: str,
        key: str,
        *,
        source: SelectorSource = "base",
        attrs: dict[str, str] | None = None,
        kind: str | None = None,
        lib: str | None = None,
        stage: int | None = None,
        lane: int | None = None,
        anchor: bool = True,
    ):
        component = self.component(key, source=source)
        merged_attrs = attrs_dict(component)
        if attrs:
            merged_attrs.update(attrs)
        kwargs: dict[str, object] = {}
        if anchor:
            kwargs["loc"] = component.loc
        if lib is not None:
            kwargs["lib"] = lib
        elif component.lib is not None:
            kwargs["lib"] = component.lib
        if stage is not None:
            kwargs["rank"] = stage
        if lane is not None:
            kwargs["track"] = lane
        return builder.add_instance(instance_id, kind or component.name, merged_attrs, **kwargs)

    def connect_side_tunnel(
        self,
        key: str,
        *,
        side: str,
        label: str,
        width: int,
        source: SelectorSource = "base",
    ) -> None:
        pin_loc = self.loc(key, source=source)
        dx, dy, facing = _side_tunnel_spec(side)
        connect_pin_to_tunnel(
            self.circuit,
            pin_loc=pin_loc,
            tunnel_loc=(pin_loc[0] + dx, pin_loc[1] + dy),
            label=label,
            width=width,
            facing=facing,
        )

    def set_rom_words(
        self,
        key: str,
        *,
        addr_width: int,
        data_width: int,
        words: list[int],
        source: SelectorSource = "current",
    ) -> RawComponent:
        rom = self.component(key, source=source)
        set_attr(rom, "addrWidth", str(addr_width))
        set_attr(rom, "dataWidth", str(data_width))
        set_attr(rom, "contents", rom_contents_from_words(addr_width, data_width, words), as_text=False)
        return rom

    def attach_component(
        self,
        anchor_key: str,
        port_name: str,
        name: str,
        attrs: dict[str, str],
        *,
        lib: str | None,
        project: RawProject | None = None,
        source: SelectorSource = "current",
        distance: int = 20,
        component_padding: int = 10,
        tangent_limit: int = 240,
        distance_limit: int = 240,
        follow_port_facing: bool = True,
        attached_port_name: str | None = None,
    ) -> RawComponent:
        anchor = self.component(anchor_key, source=source)
        return attach_component_to_port(
            self.circuit,
            anchor,
            port_name,
            name,
            attrs,
            lib=lib,
            project=project,
            distance=distance,
            component_padding=component_padding,
            tangent_limit=tangent_limit,
            distance_limit=distance_limit,
            follow_port_facing=follow_port_facing,
            attached_port_name=attached_port_name,
        )

    def attach_subcircuit(
        self,
        anchor_key: str,
        port_name: str,
        name: str,
        *,
        attached_port_name: str | None = None,
        project: RawProject | None = None,
        source: SelectorSource = "current",
        distance: int = 20,
        component_padding: int = 10,
        tangent_limit: int = 240,
        distance_limit: int = 240,
    ) -> RawComponent:
        anchor = self.component(anchor_key, source=source)
        return attach_subcircuit_to_port(
            self.circuit,
            anchor,
            port_name,
            name,
            attached_port_name=attached_port_name,
            project=project,
            distance=distance,
            component_padding=component_padding,
            tangent_limit=tangent_limit,
            distance_limit=distance_limit,
        )

    def attach_bit_extender(
        self,
        anchor_key: str,
        port_name: str,
        *,
        in_width: int,
        out_width: int,
        extend_type: str = "zero",
        project: RawProject | None = None,
        source: SelectorSource = "current",
        distance: int = 20,
        component_padding: int = 10,
        tangent_limit: int = 240,
        distance_limit: int = 240,
        attached_port_name: str = "in",
    ) -> RawComponent:
        anchor = self.component(anchor_key, source=source)
        return attach_bit_extender_to_port(
            self.circuit,
            anchor,
            port_name,
            in_width=in_width,
            out_width=out_width,
            extend_type=extend_type,
            project=project,
            distance=distance,
            component_padding=component_padding,
            tangent_limit=tangent_limit,
            distance_limit=distance_limit,
            attached_port_name=attached_port_name,
        )
