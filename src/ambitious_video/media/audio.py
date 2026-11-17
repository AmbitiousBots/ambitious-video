from __future__ import annotations

import array
import math
import wave
from dataclasses import dataclass
from pathlib import Path

from ambitious_video.media.probe import decode_pcm

RATE = 48000
DUCK = 10 ** (-12 / 20)
LEVEL_STEPS = 8
QUIET = 64


@dataclass(frozen=True, slots=True)
class Piece:
    path: Path
    start_frame: int
    gain_db: float
    ducks: tuple[tuple[int, int], ...]


def mixed(frames: int, fps: int, pieces: tuple[Piece, ...]) -> array.array[int]:
    count = frames * RATE // fps
    samples = array.array("h", [0] * count)
    for piece in pieces:
        pcm = decode_pcm(str(piece.path))
        incoming = array.array("h")
        incoming.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
        gain = 10 ** (piece.gain_db / 20)
        start = piece.start_frame * RATE // fps
        duck_samples = tuple((begin * RATE // fps, end * RATE // fps) for begin, end in piece.ducks)
        for offset, sample in enumerate(incoming):
            at = start + offset
            if at < 0 or at >= count:
                continue
            level = gain
            for begin, end in duck_samples:
                if begin <= at < end:
                    level *= DUCK
            summed = int(samples[at] + sample * level)
            if summed > 32767:
                summed = 32767
            if summed < -32768:
                summed = -32768
            samples[at] = summed
    return samples


def levels(samples: array.array[int], frames: int, fps: int) -> tuple[float, ...]:
    loud: list[int] = []
    for index in range(frames):
        window = samples[index * RATE // fps : (index + 1) * RATE // fps]
        total = sum(sample * sample for sample in window)
        loud.append(math.isqrt(total // len(window)) if window else 0)
    peak = max(loud, default=0)
    if peak < QUIET:
        return tuple(0.0 for _ in loud)
    return tuple(round(value * LEVEL_STEPS / peak) / LEVEL_STEPS for value in loud)


def write_wav(path: Path, frames: int, fps: int, pieces: tuple[Piece, ...]) -> None:
    samples = mixed(frames, fps, pieces)
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(samples.tobytes())
