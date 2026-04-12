from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from . import extract_logical_circuit, gb2312_word_stream, load_project, rom_contents_from_words


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Utilities for Logisim .circ XML and logic views")
    sub = parser.add_subparsers(dest="command", required=True)

    dump_project = sub.add_parser("dump-project", help="Dump the raw project model as JSON")
    dump_project.add_argument("circ", type=Path)

    dump_logic = sub.add_parser("dump-logic", help="Dump one circuit as a logical netlist JSON")
    dump_logic.add_argument("circ", type=Path)
    dump_logic.add_argument("circuit_name")
    dump_logic.add_argument("--radius", type=int, default=60)

    rom_text = sub.add_parser("gb2312-rom", help="Generate Logisim ROM text from a GB2312 string")
    rom_text.add_argument("text")
    rom_text.add_argument("--addr-width", type=int, default=8)
    rom_text.add_argument("--data-width", type=int, default=16)

    return parser


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args()
    try:
        if args.command == "dump-project":
            project = load_project(args.circ)
            print(json.dumps(project.to_dict(), ensure_ascii=False, indent=2))
            return 0
        if args.command == "dump-logic":
            project = load_project(args.circ)
            circuit = project.circuit(args.circuit_name)
            logic = extract_logical_circuit(circuit, radius=args.radius, project=project)
            print(json.dumps(logic.to_dict(), ensure_ascii=False, indent=2))
            return 0
        if args.command == "gb2312-rom":
            words = gb2312_word_stream(args.text)
            print(rom_contents_from_words(args.addr_width, args.data_width, words), end="")
            return 0
        raise SystemExit(f"unknown command: {args.command}")
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
