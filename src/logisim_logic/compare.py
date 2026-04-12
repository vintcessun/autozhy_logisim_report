from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import xml.etree.ElementTree as ET

from .model import RawProject, XmlFragment
from .xml_io import load_project


def _fragment_signature(fragment: XmlFragment) -> object:
    return (
        fragment.tag,
        tuple(sorted(fragment.attrs.items())),
        fragment.text,
        tuple(_fragment_signature(child) for child in fragment.children),
    )


def project_signature(project: RawProject) -> dict[str, object]:
    return {
        "root_attrs": tuple(sorted(project.root_attrs.items())),
        "root_text": project.root_text,
        "structured": project.to_dict(),
    }


def _normalize_element(elem: ET.Element, *, is_root: bool = False, is_attr: bool = False) -> tuple[object, ...]:
    text = elem.text or ""
    if not is_root and not is_attr and text.strip() == "":
        text = ""
    return (
        elem.tag,
        tuple(sorted(elem.attrib.items())),
        text,
        tuple(_normalize_element(child, is_attr=(elem.tag == "a")) for child in list(elem)),
    )


def normalize_xml_file(path: str | Path) -> tuple[object, ...]:
    tree = ET.parse(path)
    return _normalize_element(tree.getroot(), is_root=True)


@dataclass(slots=True)
class ProjectDiff:
    identical: bool
    details: list[str]


def compare_projects(left: RawProject, right: RawProject) -> ProjectDiff:
    left_sig = project_signature(left)
    right_sig = project_signature(right)
    details: list[str] = []
    if left_sig["root_attrs"] != right_sig["root_attrs"]:
        details.append("root attrs differ")
    if left_sig["root_text"] != right_sig["root_text"]:
        details.append("root text differs")
    if left_sig["structured"] != right_sig["structured"]:
        details.append("structured project model differs")
    return ProjectDiff(identical=not details, details=details)


def compare_project_files(left: str | Path, right: str | Path) -> ProjectDiff:
    xml_left = normalize_xml_file(left)
    xml_right = normalize_xml_file(right)
    if xml_left == xml_right:
        return ProjectDiff(identical=True, details=[])
    model_diff = compare_projects(load_project(left), load_project(right))
    details = list(model_diff.details)
    details.append("normalized XML tree differs")
    return ProjectDiff(identical=False, details=details)
