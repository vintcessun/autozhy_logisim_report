from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET

from .model import (
    RawAppearance,
    RawAttribute,
    RawCircuit,
    RawComponent,
    RawLibrary,
    RawMain,
    RawMappings,
    RawMessage,
    RawOptions,
    RawProject,
    RawTool,
    RawToolbar,
    RawToolbarItem,
    RawWire,
    XmlFragment,
)


def parse_point(text: str) -> tuple[int, int]:
    value = text.strip()
    if value.startswith("(") and value.endswith(")"):
        value = value[1:-1]
    x, y = value.split(",", 1)
    return (int(x.strip()), int(y.strip()))


def format_point(point: tuple[int, int]) -> str:
    return f"({point[0]},{point[1]})"


def _parse_attribute(elem: ET.Element) -> RawAttribute:
    extra_attrs = {key: value for key, value in elem.attrib.items() if key not in {"name", "val"}}
    if "val" in elem.attrib:
        return RawAttribute(name=elem.get("name", ""), value=elem.get("val", ""), as_text=False, extra_attrs=extra_attrs)
    return RawAttribute(name=elem.get("name", ""), value=elem.text or "", as_text=True, extra_attrs=extra_attrs)


def _attribute_to_element(attr: RawAttribute) -> ET.Element:
    elem = ET.Element("a")
    elem.set("name", attr.name)
    for key, value in attr.extra_attrs.items():
        elem.set(key, value)
    if attr.as_text:
        elem.text = attr.value
    else:
        elem.set("val", attr.value)
    return elem


def _parse_tool(elem: ET.Element) -> RawTool:
    attrs = []
    other_children = []
    for child in elem:
        if child.tag == "a":
            attrs.append(_parse_attribute(child))
        else:
            other_children.append(XmlFragment.from_element(child))
    return RawTool(
        name=elem.get("name", ""),
        lib=elem.get("lib"),
        attrs=attrs,
        extra_attrs={key: value for key, value in elem.attrib.items() if key not in {"name", "lib"}},
        other_children=other_children,
    )


def _tool_to_element(tool: RawTool) -> ET.Element:
    elem = ET.Element("tool")
    if tool.lib is not None:
        elem.set("lib", tool.lib)
    elem.set("name", tool.name)
    for key, value in tool.extra_attrs.items():
        elem.set(key, value)
    for attr in tool.attrs:
        elem.append(_attribute_to_element(attr))
    for child in tool.other_children:
        elem.append(child.to_element())
    return elem


def _parse_library(elem: ET.Element) -> RawLibrary:
    tools = []
    other_children = []
    for child in elem:
        if child.tag == "tool":
            tools.append(_parse_tool(child))
        else:
            other_children.append(XmlFragment.from_element(child))
    return RawLibrary(
        name=elem.get("name", ""),
        desc=elem.get("desc", ""),
        tools=tools,
        extra_attrs={key: value for key, value in elem.attrib.items() if key not in {"name", "desc"}},
        other_children=other_children,
    )


def _library_to_element(library: RawLibrary) -> ET.Element:
    elem = ET.Element("lib")
    elem.set("name", library.name)
    elem.set("desc", library.desc)
    for key, value in library.extra_attrs.items():
        elem.set(key, value)
    for tool in library.tools:
        elem.append(_tool_to_element(tool))
    for child in library.other_children:
        elem.append(child.to_element())
    return elem


def _parse_options(elem: ET.Element) -> RawOptions:
    attrs = []
    other_children = []
    for child in elem:
        if child.tag == "a":
            attrs.append(_parse_attribute(child))
        else:
            other_children.append(XmlFragment.from_element(child))
    return RawOptions(attrs=attrs, extra_attrs=dict(elem.attrib), other_children=other_children)


def _options_to_element(options: RawOptions) -> ET.Element:
    elem = ET.Element("options", dict(options.extra_attrs))
    for attr in options.attrs:
        elem.append(_attribute_to_element(attr))
    for child in options.other_children:
        elem.append(child.to_element())
    return elem


def _parse_mappings(elem: ET.Element) -> RawMappings:
    tools = []
    other_children = []
    for child in elem:
        if child.tag == "tool":
            tools.append(_parse_tool(child))
        else:
            other_children.append(XmlFragment.from_element(child))
    return RawMappings(tools=tools, extra_attrs=dict(elem.attrib), other_children=other_children)


def _mappings_to_element(mappings: RawMappings) -> ET.Element:
    elem = ET.Element("mappings", dict(mappings.extra_attrs))
    for tool in mappings.tools:
        elem.append(_tool_to_element(tool))
    for child in mappings.other_children:
        elem.append(child.to_element())
    return elem


def _parse_toolbar(elem: ET.Element) -> RawToolbar:
    items: list[RawToolbarItem] = []
    other_children: list[XmlFragment] = []
    for child in elem:
        if child.tag == "sep":
            items.append(RawToolbarItem(kind="sep"))
        elif child.tag == "tool":
            items.append(RawToolbarItem(kind="tool", tool=_parse_tool(child)))
        else:
            fragment = XmlFragment.from_element(child)
            items.append(RawToolbarItem(kind="other", fragment=fragment))
            other_children.append(fragment)
    return RawToolbar(items=items, extra_attrs=dict(elem.attrib), other_children=other_children)


def _toolbar_to_element(toolbar: RawToolbar) -> ET.Element:
    elem = ET.Element("toolbar", dict(toolbar.extra_attrs))
    for item in toolbar.items:
        if item.kind == "sep":
            elem.append(ET.Element("sep"))
        elif item.kind == "tool" and item.tool is not None:
            elem.append(_tool_to_element(item.tool))
        elif item.fragment is not None:
            elem.append(item.fragment.to_element())
    return elem


def _parse_appearance(elem: ET.Element) -> RawAppearance:
    shapes = [XmlFragment.from_element(child) for child in elem]
    return RawAppearance(shapes=shapes, extra_attrs=dict(elem.attrib))


def _appearance_to_element(appearance: RawAppearance) -> ET.Element:
    elem = ET.Element("appear", dict(appearance.extra_attrs))
    for shape in appearance.shapes:
        elem.append(shape.to_element())
    for child in appearance.other_children:
        elem.append(child.to_element())
    return elem


def _parse_component(elem: ET.Element) -> RawComponent:
    attrs = []
    other_children = []
    for child in elem:
        if child.tag == "a":
            attrs.append(_parse_attribute(child))
        else:
            other_children.append(XmlFragment.from_element(child))
    return RawComponent(
        name=elem.get("name", ""),
        lib=elem.get("lib"),
        loc=parse_point(elem.get("loc", "(0,0)")),
        attrs=attrs,
        extra_attrs={key: value for key, value in elem.attrib.items() if key not in {"name", "lib", "loc"}},
        other_children=other_children,
    )


def _component_to_element(component: RawComponent) -> ET.Element:
    elem = ET.Element("comp")
    elem.set("name", component.name)
    if component.lib is not None:
        elem.set("lib", component.lib)
    elem.set("loc", format_point(component.loc))
    for key, value in component.extra_attrs.items():
        elem.set(key, value)
    for attr in component.attrs:
        elem.append(_attribute_to_element(attr))
    for child in component.other_children:
        elem.append(child.to_element())
    return elem


def _parse_circuit(elem: ET.Element) -> RawCircuit:
    attrs = []
    appearances = []
    components = []
    wires = []
    other_children = []
    item_order: list[tuple[str, int]] = []
    for child in elem:
        if child.tag == "a":
            attrs.append(_parse_attribute(child))
            item_order.append(("attr", len(attrs) - 1))
        elif child.tag == "appear":
            appearances.append(_parse_appearance(child))
            item_order.append(("appear", len(appearances) - 1))
        elif child.tag == "comp":
            components.append(_parse_component(child))
            item_order.append(("comp", len(components) - 1))
        elif child.tag == "wire":
            wires.append(
                RawWire(
                    start=parse_point(child.get("from", "(0,0)")),
                    end=parse_point(child.get("to", "(0,0)")),
                    extra_attrs={key: value for key, value in child.attrib.items() if key not in {"from", "to"}},
                )
            )
            item_order.append(("wire", len(wires) - 1))
        else:
            other_children.append(XmlFragment.from_element(child))
            item_order.append(("other", len(other_children) - 1))
    return RawCircuit(
        name=elem.get("name", ""),
        attrs=attrs,
        other_children=other_children,
        extra_attrs={key: value for key, value in elem.attrib.items() if key != "name"},
        components=components,
        wires=wires,
        appearances=appearances,
        item_order=item_order,
    )


def _circuit_to_element(circuit: RawCircuit) -> ET.Element:
    elem = ET.Element("circuit", dict(circuit.extra_attrs))
    elem.set("name", circuit.name)
    for kind, index in circuit.resolved_item_order():
        if kind == "attr":
            elem.append(_attribute_to_element(circuit.attrs[index]))
        elif kind == "appear":
            elem.append(_appearance_to_element(circuit.appearances[index]))
        elif kind == "wire":
            wire = circuit.wires[index]
            wire_elem = ET.Element("wire", dict(wire.extra_attrs))
            wire_elem.set("from", format_point(wire.start))
            wire_elem.set("to", format_point(wire.end))
            elem.append(wire_elem)
        elif kind == "comp":
            elem.append(_component_to_element(circuit.components[index]))
        elif kind == "other":
            elem.append(circuit.other_children[index].to_element())
    return elem


def _sanitize_circuit_appearance(circuit: RawCircuit) -> None:
    pin_locations = {tuple(component.loc) for component in circuit.components if component.name == "Pin"}
    if not pin_locations:
        return
    for appearance in circuit.appearances:
        filtered_shapes: list[XmlFragment] = []
        for shape in appearance.shapes:
            if shape.tag != "circ-port":
                filtered_shapes.append(shape)
                continue
            pin_text = shape.attrs.get("pin")
            if pin_text is None:
                continue
            try:
                pin_loc = parse_point(pin_text)
            except Exception:
                continue
            if pin_loc in pin_locations:
                filtered_shapes.append(shape)
        appearance.shapes = filtered_shapes


def _sanitize_project(project: RawProject) -> None:
    for circuit in project.circuits:
        _sanitize_circuit_appearance(circuit)


def load_project(path: str | Path) -> RawProject:
    tree = ET.parse(path)
    root = tree.getroot()
    libraries = []
    circuits = []
    messages = []
    other_root_children = []
    main: RawMain | None = None
    options: RawOptions | None = None
    mappings: RawMappings | None = None
    toolbar: RawToolbar | None = None
    item_order: list[tuple[str, int]] = []

    for child in root:
        if child.tag == "lib":
            libraries.append(_parse_library(child))
            item_order.append(("lib", len(libraries) - 1))
        elif child.tag == "main":
            main = RawMain(name=child.get("name", ""), extra_attrs={key: value for key, value in child.attrib.items() if key != "name"})
            item_order.append(("main", 0))
        elif child.tag == "options":
            options = _parse_options(child)
            item_order.append(("options", 0))
        elif child.tag == "mappings":
            mappings = _parse_mappings(child)
            item_order.append(("mappings", 0))
        elif child.tag == "toolbar":
            toolbar = _parse_toolbar(child)
            item_order.append(("toolbar", 0))
        elif child.tag == "message":
            messages.append(
                RawMessage(
                    value=child.get("value", ""),
                    extra_attrs={key: value for key, value in child.attrib.items() if key != "value"},
                    text=child.text or "",
                )
            )
            item_order.append(("message", len(messages) - 1))
        elif child.tag == "circuit":
            circuits.append(_parse_circuit(child))
            item_order.append(("circuit", len(circuits) - 1))
        else:
            other_root_children.append(XmlFragment.from_element(child))
            item_order.append(("other", len(other_root_children) - 1))

    return RawProject(
        root_attrs=dict(root.attrib),
        root_text=root.text or "",
        circuits=circuits,
        libraries=libraries,
        main=main,
        options=options,
        mappings=mappings,
        toolbar=toolbar,
        messages=messages,
        other_root_children=other_root_children,
        item_order=item_order,
    )


def save_project(project: RawProject, path: str | Path) -> Path:
    _sanitize_project(project)
    root = ET.Element("project", dict(project.root_attrs))
    root.text = project.root_text
    for kind, index in project.resolved_item_order():
        if kind == "lib":
            root.append(_library_to_element(project.libraries[index]))
        elif kind == "main" and project.main is not None:
            elem = ET.Element("main", dict(project.main.extra_attrs))
            elem.set("name", project.main.name)
            root.append(elem)
        elif kind == "options" and project.options is not None:
            root.append(_options_to_element(project.options))
        elif kind == "mappings" and project.mappings is not None:
            root.append(_mappings_to_element(project.mappings))
        elif kind == "toolbar" and project.toolbar is not None:
            root.append(_toolbar_to_element(project.toolbar))
        elif kind == "message":
            message = project.messages[index]
            elem = ET.Element("message", dict(message.extra_attrs))
            elem.set("value", message.value)
            elem.text = message.text
            root.append(elem)
        elif kind == "circuit":
            root.append(_circuit_to_element(project.circuits[index]))
        elif kind == "other":
            root.append(project.other_root_children[index].to_element())

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    xml_body = ET.tostring(root, encoding="utf-8")
    target = Path(path)
    target.write_bytes(b'<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n' + xml_body)
    return target
