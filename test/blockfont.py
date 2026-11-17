from __future__ import annotations

import struct
from pathlib import Path

UNITS = 1000
ADVANCE = 600
FIRST = 32
LAST = 126


def _pad(data: bytes) -> bytes:
    return data + b"\0" * (-len(data) % 4)


def _checksum(data: bytes) -> int:
    padded = _pad(data)
    return sum(struct.unpack(f">{len(padded) // 4}I", padded)) & 0xFFFFFFFF


def _left(code: int) -> int:
    return 60 + 40 * (code % 3)


def _block(code: int) -> bytes:
    top = 300 + 100 * (code % 5)
    left = _left(code)
    right = 540
    header = struct.pack(">hhhhh", 1, left, 0, right, top)
    points = struct.pack(">HH4B", 3, 0, 1, 1, 1, 1)
    across = struct.pack(">4h", left, 0, right - left, 0)
    up = struct.pack(">4h", 0, top, 0, -top)
    return _pad(header + points + across + up)


def _names() -> bytes:
    texts = {1: "Blocks", 2: "Regular", 4: "Blocks Regular", 6: "Blocks-Regular"}
    records = b""
    strings = b""
    for name_id, text in texts.items():
        encoded = text.encode("utf-16-be")
        records += struct.pack(">6H", 3, 1, 0x409, name_id, len(encoded), len(strings))
        strings += encoded
    return struct.pack(">3H", 0, len(texts), 6 + 12 * len(texts)) + records + strings


def _cmap() -> bytes:
    ends = (LAST, 0xFFFF)
    starts = (FIRST, 0xFFFF)
    deltas = ((1 - FIRST) & 0xFFFF, 1)
    body = struct.pack(">4H", 4, 4, 1, 0)
    body += struct.pack(">2H", *ends) + struct.pack(">H", 0) + struct.pack(">2H", *starts)
    body += struct.pack(">2H", *deltas) + struct.pack(">2H", 0, 0)
    table = struct.pack(">3H", 4, 6 + len(body), 0) + body
    return struct.pack(">2H", 0, 1) + struct.pack(">2HI", 3, 1, 12) + table


def block_font() -> bytes:
    glyphs = [b"", b""] + [_block(code) for code in range(FIRST + 1, LAST + 1)]
    count = len(glyphs)
    offsets = [0]
    for glyph in glyphs:
        offsets.append(offsets[-1] + len(glyph))
    head = struct.pack(">IIIIHH", 0x00010000, 0x00010000, 0, 0x5F0F3CF5, 3, UNITS)
    head += struct.pack(">qq", 0, 0) + struct.pack(">4h", 0, 0, ADVANCE, 700)
    head += struct.pack(">HHhhh", 0, 8, 2, 1, 0)
    hhea = struct.pack(">I3hH3h", 0x00010000, 800, -200, 0, ADVANCE, 0, 0, ADVANCE)
    hhea += struct.pack(">3h4hhH", 1, 0, 0, 0, 0, 0, 0, 0, count)
    maxp = struct.pack(">IH13H", 0x00010000, count, 4, 1, 0, 0, 2, 0, 0, 0, 0, 0, 0, 0, 0)
    post = struct.pack(">IIhhI4I", 0x00030000, 0, -100, 50, 1, 0, 0, 0, 0)
    tables = {
        b"cmap": _cmap(),
        b"glyf": b"".join(glyphs),
        b"head": head,
        b"hhea": hhea,
        b"hmtx": struct.pack(">HhHh", ADVANCE, 0, ADVANCE, 0)
        + b"".join(struct.pack(">Hh", ADVANCE, _left(code)) for code in range(FIRST + 1, LAST + 1)),
        b"loca": struct.pack(f">{count + 1}I", *offsets),
        b"maxp": maxp,
        b"name": _names(),
        b"post": post,
    }
    directory = struct.pack(">I4H", 0x00010000, len(tables), 128, 3, len(tables) * 16 - 128)
    cursor = 12 + 16 * len(tables)
    body = b""
    for tag, data in tables.items():
        directory += tag + struct.pack(">3I", _checksum(data), cursor, len(data))
        body += _pad(data)
        cursor += len(_pad(data))
    return directory + body


def write_block_font(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(block_font())
    return path
