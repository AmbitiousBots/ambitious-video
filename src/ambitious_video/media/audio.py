from __future__ import annotations

import array
import wave
from dataclasses import dataclass
from pathlib import Path

from ambitious_video.media.probe import decode_pcm

RATE = 48000
DUCK = 10 ** (-12 / 20)


@dataclass(frozen=True, slots=True)
class Piece:
    path: Path
    start_frame: int
    gain_db: float
    ducks: tuple[tuple[int, int], ...]


def write_wav(path: Path, frames: int, fps: int, pieces: tuple[Piece, ...]) -> None:
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
            mixed = int(samples[at] + sample * level)
            if mixed > 32767:
                mixed = 32767
            if mixed < -32768:
                mixed = -32768
            samples[at] = mixed
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(samples.tobytes())
