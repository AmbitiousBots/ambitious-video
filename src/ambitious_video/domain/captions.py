from __future__ import annotations

import re
from dataclasses import dataclass

SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True, slots=True)
class Caption:
    track: str
    start: int
    frames: int
    text: str

    @property
    def end(self) -> int:
        return self.start + self.frames


def sentences(text: str) -> tuple[str, ...]:
    return tuple(part for part in SENTENCE_END.split(text.strip()) if part != "")


def segments(track: str, text: str, start: int, frames: int) -> tuple[Caption, ...]:
    whole = (Caption(track, start, frames, text.strip()),)
    parts = sentences(text)
    if len(parts) < 2:
        return whole
    total = sum(len(part) for part in parts)
    built: list[Caption] = []
    seen = 0
    begin = 0
    for part in parts:
        seen += len(part)
        end = (2 * frames * seen + total) // (2 * total)
        if end <= begin:
            return whole
        built.append(Caption(track, start + begin, end - begin, part))
        begin = end
    return tuple(built)
