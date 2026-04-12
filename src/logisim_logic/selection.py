from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from .model import RawCircuit, RawComponent
from .rebuild_support import attrs_dict, get_attr, set_attr, set_splitter_extract


def _stringify(value: Any) -> str:
    return str(value)


def _sort_value(comp: RawComponent, key: str) -> int:
    if key == "x":
        return comp.loc[0]
    if key == "y":
        return comp.loc[1]
    if key == "-x":
        return -comp.loc[0]
    if key == "-y":
        return -comp.loc[1]
    raise ValueError(f"unsupported sort key {key!r}")


@dataclass(frozen=True, slots=True)
class ComponentSelector:
    kind: str | None = None
    attrs: dict[str, str] = field(default_factory=dict)
    attr_contains: dict[str, str] = field(default_factory=dict)
    index: int = 0
    order_by: tuple[str, ...] = ("x", "y")

    def matches(self, comp: RawComponent) -> bool:
        if self.kind is not None and comp.name != self.kind:
            return False
        for name, expected in self.attrs.items():
            if (get_attr(comp, name, "") or "") != expected:
                return False
        for name, fragment in self.attr_contains.items():
            if fragment not in (get_attr(comp, name, "") or ""):
                return False
        return True

    def resolve_all(self, circuit: RawCircuit) -> list[RawComponent]:
        matched = [comp for comp in circuit.components if self.matches(comp)]
        if self.order_by:
            matched.sort(key=lambda comp: tuple(_sort_value(comp, key) for key in self.order_by))
        return matched

    def resolve(self, circuit: RawCircuit) -> RawComponent:
        matched = self.resolve_all(circuit)
        if not matched:
            raise KeyError(f"selector {self!r} matched no components")
        try:
            return matched[self.index]
        except IndexError as exc:
            raise KeyError(f"selector {self!r} matched {len(matched)} components") from exc


def select_component(
    kind: str | None = None,
    *,
    index: int = 0,
    order_by: tuple[str, ...] = ("x", "y"),
    contains: dict[str, Any] | None = None,
    **attrs: Any,
) -> ComponentSelector:
    return ComponentSelector(
        kind=kind,
        attrs={name: _stringify(value) for name, value in attrs.items()},
        attr_contains={name: _stringify(value) for name, value in (contains or {}).items()},
        index=index,
        order_by=order_by,
    )


def select_tunnel(
    label: str,
    *,
    index: int = 0,
    order_by: tuple[str, ...] = ("x", "y"),
    contains: dict[str, Any] | None = None,
    **attrs: Any,
) -> ComponentSelector:
    return select_component("Tunnel", index=index, order_by=order_by, contains=contains, label=label, **attrs)


class SelectorView:
    def __init__(self, circuit: RawCircuit, selectors: dict[str, ComponentSelector]) -> None:
        self.circuit = circuit
        self.selectors = selectors

    def component(self, key: str) -> RawComponent:
        try:
            selector = self.selectors[key]
        except KeyError as exc:
            raise KeyError(key) from exc
        return selector.resolve(self.circuit)

    def components(self, key: str) -> list[RawComponent]:
        try:
            selector = self.selectors[key]
        except KeyError as exc:
            raise KeyError(key) from exc
        return selector.resolve_all(self.circuit)

    def loc(self, key: str) -> tuple[int, int]:
        return self.component(key).loc

    def attrs_copy(self, key: str) -> dict[str, str]:
        return attrs_dict(self.component(key))

    def set_attrs(self, key: str, /, **attrs: Any) -> RawComponent:
        comp = self.component(key)
        for name, value in attrs.items():
            set_attr(comp, name, _stringify(value), as_text=False if name == "text" else None)
        return comp

    def set_width(self, key: str, width: int | str) -> RawComponent:
        return self.set_attrs(key, width=width)

    def set_probe(
        self,
        key: str,
        *,
        width: int | str | None = None,
        label: str | None = None,
        radix: str | None = None,
        facing: str | None = None,
    ) -> RawComponent:
        attrs: dict[str, Any] = {}
        if width is not None:
            attrs["width"] = width
        if label is not None:
            attrs["label"] = label
        if radix is not None:
            attrs["radix"] = radix
        if facing is not None:
            attrs["facing"] = facing
        return self.set_attrs(key, **attrs)

    def replace_text(self, key: str, text: str) -> RawComponent:
        return self.set_attrs(key, text=text)

    def splitter_extract(self, key: str, *, incoming: int, selected: Iterable[int]) -> RawComponent:
        comp = self.component(key)
        set_splitter_extract(comp, incoming, list(selected))
        return comp


def selector_view(circuit: RawCircuit, selectors: dict[str, ComponentSelector]) -> SelectorView:
    return SelectorView(circuit, selectors)
