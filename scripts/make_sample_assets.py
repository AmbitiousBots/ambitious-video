#!/usr/bin/env python3
from __future__ import annotations

import struct
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "examples" / "samples"
LABEL = "SAMPLE DATA"
LANDSCAPE = (960, 540)
PORTRAIT = (540, 960)
LOGO_SIZE = (256, 256)

Color = tuple[int, int, int]

GLYPHS: dict[str, tuple[str, ...]] = {
    " ": (".....", ".....", ".....", ".....", ".....", ".....", "....."),
    "A": (".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "D": ("####.", "#...#", "#...#", "#...#", "#...#", "#...#", "####."),
    "E": ("#####", "#....", "#....", "####.", "#....", "#....", "#####"),
    "G": (".###.", "#...#", "#....", "#.###", "#...#", "#...#", ".###."),
    "I": ("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "#####"),
    "L": ("#....", "#....", "#....", "#....", "#....", "#....", "#####"),
    "M": ("#...#", "##.##", "#.#.#", "#.#.#", "#...#", "#...#", "#...#"),
    "O": (".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "P": ("####.", "#...#", "#...#", "####.", "#....", "#....", "#...."),
    "S": (".####", "#....", "#....", ".###.", "....#", "....#", "####."),
    "T": ("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."),
    "1": ("..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."),
    "2": (".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#####"),
    "3": ("####.", "....#", "....#", ".###.", "....#", "....#", "####."),
}


@dataclass(frozen=True)
class Sample:
    slug: str
    bg: Color
    fg: Color
    accent: Color
    still_size: tuple[int, int] = LANDSCAPE


def rgb(text: str) -> Color:
    return int(text[1:3], 16), int(text[3:5], 16), int(text[5:7], 16)


SAMPLE_COLORS: tuple[Sample, ...] = (
    Sample("example-plumbing", rgb("#0F2A3D"), rgb("#F4F7FA"), rgb("#2E9BD6")),
    Sample("example-accounting", rgb("#1F2933"), rgb("#F5F1E8"), rgb("#C9A227")),
    Sample("example-fitness", rgb("#1B1B2F"), rgb("#FFFFFF"), rgb("#E4572E"), PORTRAIT),
    Sample("example-bookshop", rgb("#2D2A26"), rgb("#FBF6EC"), rgb("#7A9E7E")),
)


class Picture:
    def __init__(self, width: int, height: int, fill: Color) -> None:
        self.width = width
        self.height = height
        self.rows = [bytearray(bytes(fill) * width) for _ in range(height)]

    def rect(self, left: int, top: int, wide: int, high: int, color: Color) -> None:
        left, right = max(0, left), min(self.width, left + wide)
        if right <= left:
            return
        run = bytes(color) * (right - left)
        for y in range(max(0, top), min(self.height, top + high)):
            self.rows[y][left * 3 : right * 3] = run

    def disc(self, cx: int, cy: int, radius: int, color: Color) -> None:
        for y in range(cy - radius, cy + radius + 1):
            reach = radius * radius - (y - cy) * (y - cy)
            if reach < 0:
                continue
            half = int(reach**0.5)
            self.rect(cx - half, y, 2 * half + 1, 1, color)

    def text(self, value: str, center_x: int, top: int, scale: int, color: Color) -> None:
        wide = (len(value) * 6 - 1) * scale
        left = center_x - wide // 2
        for index, letter in enumerate(value):
            for row, line in enumerate(GLYPHS[letter]):
                for column, mark in enumerate(line):
                    if mark == "#":
                        self.rect(left + (index * 6 + column) * scale, top + row * scale, scale, scale, color)

    def png(self) -> bytes:
        def chunk(kind: bytes, body: bytes) -> bytes:
            return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

        header = struct.pack(">IIBBBBB", self.width, self.height, 8, 2, 0, 0, 0)
        raw = b"".join(b"\x00" + bytes(row) for row in self.rows)
        return b"".join(
            (
                b"\x89PNG\r\n\x1a\n",
                chunk(b"IHDR", header),
                chunk(b"IDAT", zlib.compress(raw, 9)),
                chunk(b"IEND", b""),
            )
        )


def mix(one: Color, other: Color, share: float) -> Color:
    return (
        round(one[0] + (other[0] - one[0]) * share),
        round(one[1] + (other[1] - one[1]) * share),
        round(one[2] + (other[2] - one[2]) * share),
    )


def logo(sample: Sample) -> Picture:
    wide, high = LOGO_SIZE
    picture = Picture(wide, high, sample.bg)
    picture.disc(wide // 2, 96, 64, sample.accent)
    picture.disc(wide // 2, 96, 40, sample.bg)
    picture.text("LOGO", wide // 2, 86, 3, sample.fg)
    picture.text(LABEL, wide // 2, 196, 3, sample.fg)
    return picture


def still(sample: Sample, number: int) -> Picture:
    wide, high = sample.still_size
    soft = mix(sample.bg, sample.fg, 0.18)
    half = mix(sample.bg, sample.accent, 0.45)
    picture = Picture(wide, high, sample.bg)

    def x(share: float) -> int:
        return round(wide * share)

    def y(share: float) -> int:
        return round(high * share)

    unit = min(wide, high)
    if number == 1:
        picture.rect(0, y(0.67), wide, high - y(0.67), soft)
        picture.disc(x(0.25), y(0.46), round(unit * 0.2), sample.accent)
        picture.rect(x(0.45), y(0.32), x(0.4), y(0.35), half)
    elif number == 2:
        for column in range(4):
            top = y(0.56 - column * 0.09)
            picture.rect(x(0.1 + column * 0.21), top, x(0.16), y(0.83) - top, sample.accent)
        picture.rect(x(0.075), y(0.83), x(0.85), y(0.02), soft)
    else:
        picture.rect(x(0.11), y(0.22), x(0.34), y(0.56), soft)
        picture.rect(x(0.55), y(0.22), x(0.34), y(0.56), half)
        picture.disc(x(0.5), y(0.5), round(unit * 0.14), sample.accent)
    scale = 6 if wide >= 960 else 5
    band = scale * 16
    picture.rect(0, 0, wide, band, mix(sample.bg, (0, 0, 0), 0.35))
    picture.text(LABEL, wide // 2, (band - scale * 7) // 2, scale, sample.fg)
    picture.text(f"STILL {number}", wide // 2, high - scale * 12, scale - 1, sample.fg)
    return picture


def pictures(sample: Sample) -> dict[str, Picture]:
    made = {"logo.png": logo(sample)}
    for number in (1, 2, 3):
        made[f"still-{number}.png"] = still(sample, number)
    return made


def write_all(target: Path) -> list[Path]:
    written: list[Path] = []
    for sample in SAMPLE_COLORS:
        folder = target / sample.slug / "assets"
        folder.mkdir(parents=True, exist_ok=True)
        for name, picture in pictures(sample).items():
            path = folder / name
            path.write_bytes(picture.png())
            written.append(path)
    return written


def main(argv: list[str]) -> int:
    target = Path(argv[1]) if len(argv) > 1 else SAMPLES
    for path in write_all(target):
        sys.stdout.write(f"{path.relative_to(target)} {path.stat().st_size} bytes\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
