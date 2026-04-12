from __future__ import annotations

from copy import deepcopy

from .model import RawCircuit, RawProject


def replace_circuit(project: RawProject, circuit: RawCircuit) -> None:
    for index, current in enumerate(project.circuits):
        if current.name == circuit.name:
            project.circuits[index] = circuit
            return
    project.circuits.append(circuit)


def remove_circuit(project: RawProject, name: str) -> None:
    project.circuits = [circuit for circuit in project.circuits if circuit.name != name]


def rename_circuit(project: RawProject, old_name: str, new_name: str) -> None:
    for circuit in project.circuits:
        if circuit.name == old_name:
            circuit.name = new_name
            if circuit.get("circuit") is not None:
                circuit.set("circuit", new_name)
        for component in circuit.components:
            if component.name == old_name and component.lib is None:
                component.name = new_name
    if project.main is not None and project.main.name == old_name:
        project.main.name = new_name


def set_main(project: RawProject, name: str) -> None:
    project.set_main(name)


def clone_project(project: RawProject) -> RawProject:
    return deepcopy(project)
