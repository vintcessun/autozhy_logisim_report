from __future__ import annotations

from collections.abc import Iterable
import re


def rom_contents_from_words(addr_width: int, data_width: int, words: Iterable[int], row_size: int = 8) -> str:
    header = f"addr/data: {addr_width} {data_width}"
    digits = max(1, (data_width + 3) // 4)
    encoded = [format(word & ((1 << data_width) - 1), f"0{digits}x") for word in words]
    rows = [" ".join(encoded[i : i + row_size]) for i in range(0, len(encoded), row_size)]
    return header + "\n" + "\n".join(rows) + "\n"


def raw_contents_from_words(words: Iterable[int], data_width: int = 16, row_size: int = 8) -> str:
    digits = max(1, (data_width + 3) // 4)
    encoded = [format(word & ((1 << data_width) - 1), f"0{digits}x") for word in words]
    rows = [" ".join(encoded[i : i + row_size]) for i in range(0, len(encoded), row_size)]
    return "v2.0 raw\n" + "\n".join(rows) + ("\n" if rows else "")


def rom_words_from_contents(contents: str) -> tuple[int, int, list[int]]:
    lines = [line.strip() for line in contents.splitlines() if line.strip()]
    if not lines:
        raise ValueError("empty ROM contents")
    match = re.fullmatch(r"addr/data:\s+(\d+)\s+(\d+)", lines[0], flags=re.IGNORECASE)
    if match is None:
        raise ValueError(f"invalid ROM header: {lines[0]!r}")
    addr_width = int(match.group(1))
    data_width = int(match.group(2))
    words: list[int] = []
    for token in " ".join(lines[1:]).split():
        if "*" in token:
            count_text, value_text = token.split("*", 1)
            words.extend([int(value_text, 16)] * int(count_text, 10))
        else:
            words.append(int(token, 16))
    return addr_width, data_width, words


def rom_image_from_contents(contents: str) -> tuple[int, int, list[int]]:
    addr_width, data_width, words = rom_words_from_contents(contents)
    size = 1 << addr_width
    if len(words) < size:
        words = words + [0] * (size - len(words))
    elif len(words) > size:
        words = words[:size]
    return addr_width, data_width, words


def gb2312_word_stream(text: str) -> list[int]:
    data = text.encode("gb2312")
    if len(data) % 2 != 0:
        raise ValueError("text must encode to an even number of bytes in GB2312")
    words: list[int] = []
    for i in range(0, len(data), 2):
        words.append((data[i] << 8) | data[i + 1])
    return words
