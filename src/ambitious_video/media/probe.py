from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from ambitious_video.domain.need import NeedTool


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False)
    except FileNotFoundError as error:
        raise NeedTool("ffmpeg") from error


def duration_of(path: str) -> float | None:
    result = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            path,
        ]
    )
    text = result.stdout.strip()
    if result.returncode != 0 or text in {"", "N/A"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def extract_frames(source: str, start: float, count: int, fps: int, folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    result = _run(
        [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            str(start),
            "-i",
            source,
            "-frames:v",
            str(count),
            "-vf",
            "fps=" + str(fps),
            "-start_number",
            "0",
            str(folder / "f%04d.png"),
        ]
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        raise NeedTool("ffmpeg")


def inspect(path: str) -> tuple[int, int, float, bool]:
    result = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,width,height",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            path,
        ]
    )
    if result.returncode != 0:
        return 0, 0, -1.0, False
    payload = json.loads(result.stdout)
    streams = payload.get("streams") if isinstance(payload, dict) else None
    if not isinstance(streams, list):
        return 0, 0, -1.0, False
    width = 0
    height = 0
    audio = False
    for stream in streams:
        if not isinstance(stream, dict):
            continue
        if stream.get("codec_type") == "video":
            raw_w = stream.get("width")
            raw_h = stream.get("height")
            if isinstance(raw_w, int) and isinstance(raw_h, int):
                width, height = raw_w, raw_h
        if stream.get("codec_type") == "audio":
            audio = True
    form = payload.get("format") if isinstance(payload, dict) else None
    raw_duration = form.get("duration") if isinstance(form, dict) else None
    try:
        duration = float(raw_duration) if isinstance(raw_duration, str | int | float) else -1.0
    except ValueError:
        duration = -1.0
    return width, height, duration, audio


def frame_count(path: str) -> int:
    result = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-count_packets",
            "-show_entries",
            "stream=nb_read_packets",
            "-of",
            "default=nw=1:nk=1",
            path,
        ]
    )
    text = result.stdout.strip()
    return int(text) if result.returncode == 0 and text.isdigit() else -1


def stored_as(path: str) -> tuple[str, str]:
    result = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,pix_fmt",
            "-of",
            "json",
            path,
        ]
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        raise NeedTool("ffprobe")
    payload = json.loads(result.stdout)
    streams = payload.get("streams") if isinstance(payload, dict) else None
    first = streams[0] if isinstance(streams, list) and streams else None
    if not isinstance(first, dict):
        return "", ""
    return str(first.get("codec_name", "")), str(first.get("pix_fmt", ""))


def decode_pcm(path: str) -> bytes:
    try:
        result = subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-i", path, "-f", "s16le", "-ac", "1", "-ar", "48000", "pipe:1"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as error:
        raise NeedTool("ffmpeg") from error
    if result.returncode != 0:
        sys.stderr.write(result.stderr.decode("utf-8", errors="replace"))
        raise NeedTool("ffmpeg")
    return result.stdout
