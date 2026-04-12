from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Literal

from .geometry import get_component_geometry
from .model import RawCircuit, RawComponent, RawProject, RawWire
from .project_tools import clone_project, remove_circuit, rename_circuit, replace_circuit, set_main
from .rebuild_support import (
    add_polyline,
    clone_circuit,
    find_component,
    get_attr,
    normalize_circuit_to_padding,
    normalize_project_root_circuits_to_padding,
    preserve_base_appearance,
    replace_text_exact,
    update_text_contains,
)
from .selection import ComponentSelector, select_component
from .template_tools import CircuitTemplate
from .xml_io import load_project, save_project


SelectorSource = Literal["base", "current"]


def _component_connection_points(component: RawComponent, *, project: RawProject | None = None) -> set[tuple[int, int]]:
    points = {tuple(component.loc)}
    try:
        geometry = get_component_geometry(component, project=project)
    except Exception:
        return points
    base_x, base_y = component.loc
    for port in geometry.ports:
        points.add((base_x + port.offset[0], base_y + port.offset[1]))
    return points


def _wire_touches_points(wire: RawWire, points: set[tuple[int, int]]) -> bool:
    return tuple(wire.start) in points or tuple(wire.end) in points


@dataclass(slots=True)
class CircuitEditor:
    project: RawProject | None
    base_circuit: RawCircuit
    circuit: RawCircuit
    selectors: dict[str, ComponentSelector] = field(default_factory=dict)

    @property
    def template(self) -> CircuitTemplate:
        return CircuitTemplate(self.base_circuit, self.circuit, self.selectors)

    def add_selector(self, key: str, selector: ComponentSelector) -> ComponentSelector:
        self.selectors[key] = selector
        return selector

    def select_component(self, key: str, kind: str | None = None, **attrs: Any) -> ComponentSelector:
        selector = select_component(kind, **attrs)
        self.selectors[key] = selector
        return selector

    def component(self, key: str, *, source: SelectorSource = "current") -> RawComponent:
        return self.template.component(key, source=source)

    def components(self, key: str, *, source: SelectorSource = "current") -> list[RawComponent]:
        return self.template.components(key, source=source)

    def component_at(
        self,
        *,
        kind: str | None = None,
        loc: tuple[int, int],
        source: SelectorSource = "current",
    ) -> RawComponent:
        circuit = self.base_circuit if source == "base" else self.circuit
        return find_component(circuit, name=kind, loc=loc)

    def attrs_copy(self, key: str, *, source: SelectorSource = "base") -> dict[str, str]:
        return self.template.attrs_copy(key, source=source)

    def loc(self, key: str, *, source: SelectorSource = "base") -> tuple[int, int]:
        return self.template.loc(key, source=source)

    def set_attrs(self, key: str, /, **attrs: object) -> RawComponent:
        return self.template.set_attrs(key, **attrs)

    def set_width(self, key: str, width: int | str) -> RawComponent:
        return self.template.set_width(key, width)

    def set_probe(
        self,
        key: str,
        *,
        width: int | str | None = None,
        label: str | None = None,
        radix: str | None = None,
        facing: str | None = None,
    ) -> RawComponent:
        return self.template.set_probe(key, width=width, label=label, radix=radix, facing=facing)

    def replace_text(self, key: str, text: str) -> RawComponent:
        return self.template.replace_text(key, text)

    def replace_text_exact(self, old: str, new: str) -> None:
        replace_text_exact(self.circuit, old, new)

    def update_text_contains(self, old: str, new: str) -> None:
        update_text_contains(self.circuit, old, new)

    def splitter_extract(self, key: str, *, incoming: int, selected: Iterable[int]) -> RawComponent:
        return self.template.splitter_extract(key, incoming=incoming, selected=selected)

    def set_rom_words(
        self,
        key: str,
        *,
        addr_width: int,
        data_width: int,
        words: list[int],
        source: SelectorSource = "current",
    ) -> RawComponent:
        return self.template.set_rom_words(
            key,
            addr_width=addr_width,
            data_width=data_width,
            words=words,
            source=source,
        )

    def port_point(
        self,
        ref: str | RawComponent,
        port_name: str,
        *,
        source: SelectorSource = "current",
    ) -> tuple[int, int]:
        component = self.component(ref, source=source) if isinstance(ref, str) else ref
        geometry = get_component_geometry(component, project=self.project)
        for port in geometry.ports:
            if port.name == port_name:
                return (component.loc[0] + port.offset[0], component.loc[1] + port.offset[1])
        raise KeyError((component.name, component.loc, port_name))

    def connect_points(self, *points: tuple[int, int]) -> list[RawWire]:
        if len(points) < 2:
            return []
        return add_polyline(self.circuit, list(points))

    def connect_ports(
        self,
        src_ref: str | RawComponent,
        src_port: str,
        dst_ref: str | RawComponent,
        dst_port: str,
        *,
        via: Iterable[tuple[int, int]] = (),
        source: SelectorSource = "current",
    ) -> list[RawWire]:
        start = self.port_point(src_ref, src_port, source=source)
        end = self.port_point(dst_ref, dst_port, source=source)
        return self.connect_points(start, *list(via), end)

    def labeled_components(
        self,
        *,
        kind: str | None = None,
        source: SelectorSource = "current",
    ) -> list[tuple[str, RawComponent]]:
        circuit = self.base_circuit if source == "base" else self.circuit
        items: list[tuple[str, RawComponent]] = []
        for component in circuit.components:
            if kind is not None and component.name != kind:
                continue
            label = (get_attr(component, "label", "") or "").strip()
            if label:
                items.append((label, component))
        return items

    def summary(self, *, source: SelectorSource = "current") -> dict[str, int]:
        circuit = self.base_circuit if source == "base" else self.circuit
        return dict(Counter(component.name for component in circuit.components))

    def remove_components(
        self,
        components: Iterable[RawComponent],
        *,
        prune_wires: bool = True,
    ) -> int:
        targets = list(components)
        if not targets:
            return 0
        target_ids = {id(component) for component in targets}
        if prune_wires:
            points: set[tuple[int, int]] = set()
            for component in targets:
                points.update(_component_connection_points(component, project=self.project))
            self.circuit.wires = [
                wire for wire in self.circuit.wires if not _wire_touches_points(wire, points)
            ]
        self.circuit.components = [
            component for component in self.circuit.components if id(component) not in target_ids
        ]
        return len(targets)

    def remove_selected(self, *keys: str, source: SelectorSource = "current", prune_wires: bool = True) -> int:
        selected: list[RawComponent] = []
        for key in keys:
            selected.extend(self.components(key, source=source))
        return self.remove_components(selected, prune_wires=prune_wires)

    def remove_where(
        self,
        *,
        kind: str | None = None,
        label: str | None = None,
        label_prefix: str | None = None,
        text_contains: str | None = None,
        predicate: Callable[[RawComponent], bool] | None = None,
        prune_wires: bool = True,
    ) -> int:
        selected: list[RawComponent] = []
        for component in self.circuit.components:
            if kind is not None and component.name != kind:
                continue
            current_label = (get_attr(component, "label", "") or "").strip()
            current_text = get_attr(component, "text", "") or ""
            if label is not None and current_label != label:
                continue
            if label_prefix is not None and not current_label.startswith(label_prefix):
                continue
            if text_contains is not None and text_contains not in current_text:
                continue
            if predicate is not None and not predicate(component):
                continue
            selected.append(component)
        return self.remove_components(selected, prune_wires=prune_wires)

    def preserve_appearance_from_base(self) -> None:
        preserve_base_appearance(self.base_circuit, self.circuit)

    def normalize_padding(self, *, padding: int = 20, grid: int = 10) -> tuple[int, int]:
        return normalize_circuit_to_padding(self.circuit, project=self.project, padding=padding, grid=grid)


@dataclass(slots=True)
class ProjectFacade:
    project: RawProject
    source_path: Path | None = None

    @classmethod
    def load(cls, path: str | Path) -> "ProjectFacade":
        resolved = Path(path)
        return cls(load_project(resolved), resolved)

    def clone(self) -> "ProjectFacade":
        return ProjectFacade(clone_project(self.project), self.source_path)

    @property
    def circuits(self) -> list[RawCircuit]:
        return self.project.circuits

    @property
    def main_circuit_name(self) -> str | None:
        return self.project.main_circuit_name

    def circuit_names(self) -> list[str]:
        return [circuit.name for circuit in self.project.circuits]

    def circuit(self, name: str) -> RawCircuit:
        return self.project.circuit(name)

    def rename_circuit(self, old_name: str, new_name: str) -> None:
        rename_circuit(self.project, old_name, new_name)

    def remove_circuit(self, name: str) -> None:
        remove_circuit(self.project, name)

    def replace_circuit(self, circuit: RawCircuit) -> None:
        replace_circuit(self.project, circuit)

    def set_main(self, name: str) -> None:
        set_main(self.project, name)

    def edit_circuit(
        self,
        name: str,
        *,
        selectors: dict[str, ComponentSelector] | None = None,
        base_name: str | None = None,
    ) -> CircuitEditor:
        return CircuitEditor(
            project=self.project,
            base_circuit=self.circuit(base_name or name),
            circuit=self.circuit(name),
            selectors=dict(selectors or {}),
        )

    def clone_circuit(
        self,
        source_name: str,
        new_name: str,
        *,
        selectors: dict[str, ComponentSelector] | None = None,
        set_as_main: bool = False,
    ) -> CircuitEditor:
        source = self.circuit(source_name)
        copied = clone_circuit(source, name=new_name)
        replace_circuit(self.project, copied)
        if set_as_main:
            set_main(self.project, new_name)
        return CircuitEditor(
            project=self.project,
            base_circuit=source,
            circuit=copied,
            selectors=dict(selectors or {}),
        )

    def import_circuit_from(
        self,
        other: "ProjectFacade | RawProject",
        source_name: str,
        *,
        as_name: str | None = None,
        selectors: dict[str, ComponentSelector] | None = None,
    ) -> CircuitEditor:
        other_project = other.project if isinstance(other, ProjectFacade) else other
        source = other_project.circuit(source_name)
        imported = clone_circuit(source, name=as_name or source_name)
        replace_circuit(self.project, imported)
        return CircuitEditor(
            project=self.project,
            base_circuit=source,
            circuit=imported,
            selectors=dict(selectors or {}),
        )

    def import_circuit_file(
        self,
        path: str | Path,
        source_name: str,
        *,
        as_name: str | None = None,
        selectors: dict[str, ComponentSelector] | None = None,
    ) -> CircuitEditor:
        donor = ProjectFacade.load(path)
        return self.import_circuit_from(
            donor,
            source_name,
            as_name=as_name,
            selectors=selectors,
        )

    def normalize_root_padding(self, *, padding: int = 20, grid: int = 10) -> dict[str, tuple[int, int]]:
        return normalize_project_root_circuits_to_padding(self.project, padding=padding, grid=grid)

    def save(
        self,
        path: str | Path | None = None,
        *,
        normalize_root_padding: bool = False,
        padding: int = 20,
        grid: int = 10,
    ) -> Path:
        output = Path(path) if path is not None else self.source_path
        if output is None:
            raise ValueError("save path is required when ProjectFacade has no source_path")
        if normalize_root_padding:
            self.normalize_root_padding(padding=padding, grid=grid)
        save_project(self.project, output)
        return output


__all__ = ["CircuitEditor", "ProjectFacade"]
