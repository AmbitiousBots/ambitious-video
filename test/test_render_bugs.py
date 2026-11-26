from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from ambitious_video.domain.types import TitleCard
from ambitious_video.steps.common import Canvas
from ambitious_video.steps.title_card import frame as title_frame

ROOT = Path(__file__).resolve().parents[1]
FIXED = ["--now", "2026-01-01T00:00:00Z", "--seed", "1"]
BG = (32, 64, 96)
OPEN_STDIN_LIMIT_SECONDS = 60
SLOW_WRITER_PAUSE_SECONDS = 3.0
ENCODES = ["web", "lossless"]
WEB_COLOR_SLACK = 4


def environment(home: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key != "XAI_API_KEY"}
    env["PYTHONPATH"] = os.pathsep.join([str(ROOT / "src"), str(ROOT / "vendor")])
    env["AMBITIOUS_HOME"] = str(home)
    return env


def command(args: list[str]) -> list[str]:
    return [sys.executable, "-m", "ambitious_video.cli.main", *args, *FIXED]


def envelope(stdout: str, code: int) -> dict[str, object]:
    payload = json.loads(stdout)
    assert isinstance(payload, dict)
    payload["exit"] = code
    return payload


def run(home: Path, cwd: Path, args: list[str]) -> dict[str, object]:
    result = subprocess.run(
        command(args), cwd=cwd, env=environment(home), stdin=subprocess.DEVNULL, capture_output=True, text=True
    )
    return envelope(result.stdout, result.returncode)


def run_with_open_stdin(home: Path, cwd: Path, args: list[str]) -> dict[str, object]:
    proc = subprocess.Popen(
        command(args), cwd=cwd, env=environment(home), stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    )
    assert proc.stdin is not None and proc.stdout is not None
    try:
        code = proc.wait(timeout=OPEN_STDIN_LIMIT_SECONDS)
        return envelope(proc.stdout.read(), code)
    finally:
        proc.kill()
        proc.stdin.close()
        proc.stdout.close()
        proc.wait()


def ffmpeg(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", *args], stdin=subprocess.DEVNULL, check=True)


def probed(path: Path, entry: str, extra: tuple[str, ...] = ()) -> str:
    result = subprocess.run(
        ["ffprobe", "-v", "error", *extra, "-show_entries", entry, "-of", "default=nw=1:nk=1", str(path)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def frame_count(path: Path) -> int:
    return int(probed(path, "stream=nb_read_packets", ("-select_streams", "v:0", "-count_packets")))


def frame_of(video: Path, index: int, dest: Path) -> Image.Image:
    ffmpeg(["-i", str(video), "-vf", f"select=eq(n\\,{index})", "-vframes", "1", str(dest)])
    return Image.open(dest).convert("RGB")


def make_video(path: Path, size: str) -> None:
    ffmpeg(["-f", "lavfi", "-i", f"testsrc=size={size}:rate=10:duration=3", "-pix_fmt", "yuv420p", str(path)])


def shows(found: object, wanted: tuple[int, int, int], encode: str) -> bool:
    assert isinstance(found, tuple) and len(found) == 3
    slack = 0 if encode == "lossless" else WEB_COLOR_SLACK
    return all(abs(int(have) - want) <= slack for have, want in zip(found, wanted, strict=True))


def script_of(
    output: str,
    clips: list[dict[str, object]],
    logo: str | None = None,
    encode: str | None = None,
) -> str:
    out_obj: dict[str, object] = {"path": output, "width": 320, "height": 180, "fps": 10}
    if encode is not None:
        out_obj["encode"] = encode
    brand_obj: dict[str, object] = {"colors": {"bg": "#204060", "fg": "#ffffff", "accent": "#ff8800"}}
    if logo is not None:
        brand_obj["logo"] = logo
    return (
        json.dumps(
            {
                "schemaVersion": 2,
                "output": out_obj,
                "brand": brand_obj,
                "tracks": [{"id": "main", "clips": clips}],
            },
            indent=2,
        )
        + "\n"
    )


def rendered(home: Path, work: Path, script: str) -> dict[str, object]:
    (work / "script.json").write_text(script, encoding="utf-8")
    result = run(home, work, ["render", "--in", str(work / "script.json")])
    assert result["exit"] == 0, result
    data = result["data"]
    assert isinstance(data, dict)
    return data


def verified(home: Path, work: Path, job_id: object) -> dict[str, object]:
    (work / "job.json").write_text(json.dumps({"jobId": job_id}), encoding="utf-8")
    return run(home, work, ["verify", "--in", str(work / "job.json")])


SMALL_AND_LARGE = [("64x48", (40, 30)), ("480x360", (640, 480))]


@pytest.mark.parametrize("encode", ENCODES)
@pytest.mark.parametrize(("video_size", "image_size"), SMALL_AND_LARGE)
def test_a_clip_of_another_size_fills_its_scheduled_time(
    tmp_path: Path, video_size: str, image_size: tuple[int, int], encode: str
) -> None:
    Image.new("RGB", image_size, (200, 30, 30)).save(tmp_path / "still.png")
    make_video(tmp_path / "clip.mp4", video_size)
    clips: list[dict[str, object]] = [
        {"type": "title_card", "duration": 2.0, "text": "One"},
        {"type": "image", "path": "still.png", "duration": 2.0},
        {"type": "video", "path": "clip.mp4", "in": 0, "out": 3},
        {"type": "title_card", "duration": 4.0, "text": "Two"},
    ]
    data = rendered(tmp_path / "home", tmp_path, script_of("out.mp4", clips, None, encode))
    assert data["durationSec"] == 11
    output = tmp_path / "out.mp4"
    assert frame_count(output) == 110
    assert abs(float(probed(output, "format=duration")) - 11.0) <= 0.05
    assert probed(output, "stream=width,height", ("-select_streams", "v:0")).split() == ["320", "180"]
    still = frame_of(output, 30, tmp_path / "still-frame.png")
    assert shows(still.getpixel((160, 90)), (200, 30, 30), encode)
    clip = frame_of(output, 55, tmp_path / "clip-frame.png")
    assert shows(clip.getpixel((2, 90)), BG, encode) and shows(clip.getpixel((317, 90)), BG, encode)
    assert not shows(clip.getpixel((160, 90)), BG, encode)
    assert shows(frame_of(output, 90, tmp_path / "last-card.png").getpixel((2, 2)), BG, encode)
    checked = verified(tmp_path / "home", tmp_path, data["jobId"])
    assert checked["exit"] == 0, checked


@pytest.mark.parametrize("encode", ENCODES)
def test_a_small_picture_alone_keeps_the_scheduled_time(tmp_path: Path, encode: str) -> None:
    Image.new("RGB", (16, 16), (10, 200, 10)).save(tmp_path / "dot.png")
    clips: list[dict[str, object]] = [
        {"type": "image", "path": "dot.png", "duration": 3.0, "kenburns": {"from": 1.0, "to": 1.2}}
    ]
    data = rendered(tmp_path / "home", tmp_path, script_of("picture.mp4", clips, None, encode))
    assert data["durationSec"] == 3
    assert frame_count(tmp_path / "picture.mp4") == 30
    assert verified(tmp_path / "home", tmp_path, data["jobId"])["exit"] == 0


@pytest.mark.parametrize("encode", ENCODES)
def test_verify_names_a_short_video_instead_of_a_missing_tool(tmp_path: Path, encode: str) -> None:
    clips: list[dict[str, object]] = [
        {"type": "title_card", "duration": 2.0, "text": "One"},
        {"type": "title_card", "duration": 2.0, "text": "Two"},
    ]
    data = rendered(tmp_path / "home", tmp_path, script_of("cards.mp4", clips, None, encode))
    output = tmp_path / "cards.mp4"
    ffmpeg(["-i", str(output), "-t", "2.5", "-c", "copy", str(tmp_path / "short.mp4")])
    os.replace(tmp_path / "short.mp4", output)
    checked = verified(tmp_path / "home", tmp_path, data["jobId"])
    assert checked["exit"] == 1
    errors = checked["errors"]
    assert isinstance(errors, list)
    codes = {item["code"] for item in errors if isinstance(item, dict)}
    messages = [str(item["message"]) for item in errors if isinstance(item, dict)]
    assert codes == {"E_VERIFY"}
    assert any(text.startswith("The video lasts 2.") and text.endswith(" s, not 4 s.") for text in messages)
    assert any(text.endswith(" frames, not 40.") for text in messages)
    assert "Frame 30 (title-1) is missing from the video." in messages
    body = checked["data"]
    assert isinstance(body, dict) and body["passed"] is False
    checks = body["checks"]
    assert isinstance(checks, list)
    by_name = {item["name"]: item for item in checks if isinstance(item, dict)}
    assert by_name["duration"]["ok"] is False and by_name["duration"]["expected"] == 4.0
    assert by_name["frames"]["ok"] is False and by_name["frames"]["expected"] == 40
    assert by_name["title-0"]["ok"] is True and by_name["encoding"]["ok"] is True
    assert by_name["title-1"] == {"name": "title-1", "ok": False, "frame": 30, "found": False}


@pytest.mark.parametrize("encode", ENCODES)
def test_verify_names_a_file_that_is_not_a_video(tmp_path: Path, encode: str) -> None:
    clips: list[dict[str, object]] = [{"type": "title_card", "duration": 1.0, "text": "One"}]
    data = rendered(tmp_path / "home", tmp_path, script_of("card.mp4", clips, None, encode))
    (tmp_path / "card.mp4").write_bytes(b"not a video")
    checked = verified(tmp_path / "home", tmp_path, data["jobId"])
    errors = checked["errors"]
    assert isinstance(errors, list) and len(errors) == 1 and isinstance(errors[0], dict)
    assert errors[0]["code"] == "E_VERIFY"
    assert errors[0]["message"] == "The rendered file is not a video."


def transparent_logo(path: Path) -> None:
    logo = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(logo).rectangle((16, 16, 47, 47), fill=(255, 0, 0, 255))
    logo.save(path)


def test_a_transparent_logo_shows_the_background_around_it(tmp_path: Path) -> None:
    transparent_logo(tmp_path / "logo.png")
    canvas = Canvas(640, 512, BG, (255, 255, 255), (255, 136, 0), tmp_path, "logo.png", 0, {}, {})
    drawn = title_frame_of(canvas)
    left, top = 640 // 24, 512 // 24
    for corner in [(0, 0), (63, 0), (0, 63), (63, 63), (8, 32), (55, 32)]:
        assert drawn.getpixel((left + corner[0], top + corner[1])) == BG
    assert drawn.getpixel((left + 32, top + 32)) == (255, 0, 0)


def title_frame_of(canvas: Canvas) -> Image.Image:
    image = title_frame(TitleCard(1.0, ".", None), 0, 1, canvas)
    assert isinstance(image, Image.Image)
    return image


@pytest.mark.parametrize("encode", ENCODES)
def test_a_transparent_logo_renders_without_a_black_box(tmp_path: Path, encode: str) -> None:
    transparent_logo(tmp_path / "logo.png")
    clips: list[dict[str, object]] = [{"type": "title_card", "duration": 1.0, "text": "One"}]
    data = rendered(tmp_path / "home", tmp_path, script_of("logo.mp4", clips, "logo.png", encode))
    shot = frame_of(tmp_path / "logo.mp4", 5, tmp_path / "logo-frame.png")
    left, top = 320 // 24, 180 // 24
    assert shows(shot.getpixel((left, top)), BG, encode)
    assert shows(shot.getpixel((left + 21, top)), BG, encode)
    assert shows(shot.getpixel((left + 11, top + 11)), (255, 0, 0), encode)
    assert verified(tmp_path / "home", tmp_path, data["jobId"])["exit"] == 0


def test_no_command_hangs_when_stdin_stays_open(tmp_path: Path) -> None:
    make_video(tmp_path / "clip.mp4", "64x48")
    clips: list[dict[str, object]] = [
        {"type": "title_card", "duration": 1.0, "text": "One"},
        {"type": "video", "path": "clip.mp4", "in": 0, "out": 1},
    ]
    path = tmp_path / "script.json"
    path.write_text(script_of("open.mp4", clips), encoding="utf-8")
    home = tmp_path / "home"
    for name in ["validate", "plan", "estimate"]:
        assert run_with_open_stdin(home, tmp_path, [name, "--in", str(path)])["exit"] == 0
    made = run_with_open_stdin(home, tmp_path, ["render", f"--in={path}"])
    assert made["exit"] == 0, made
    data = made["data"]
    assert isinstance(data, dict)
    (tmp_path / "job.json").write_text(json.dumps({"jobId": data["jobId"]}), encoding="utf-8")
    assert run_with_open_stdin(home, tmp_path, ["verify", "--in", str(tmp_path / "job.json")])["exit"] == 0
    for name in ["jobs", "ledger", "selfcheck", "commands", "guide", "--help", "--version"]:
        assert run_with_open_stdin(home, tmp_path, [name])["exit"] == 0


def test_a_command_without_input_gives_up_on_an_open_stdin(tmp_path: Path) -> None:
    result = run_with_open_stdin(tmp_path / "home", tmp_path, ["validate"])
    assert result["exit"] == 1
    errors = result["errors"]
    assert isinstance(errors, list) and isinstance(errors[0], dict)
    assert errors[0]["code"] == "E_INPUT_SCHEMA"


@pytest.mark.parametrize("flags", [["--in", "-"], ["--in=-"], []])
def test_a_script_on_stdin_is_still_read(tmp_path: Path, flags: list[str]) -> None:
    script = script_of("piped.mp4", [{"type": "title_card", "duration": 1.0, "text": "One"}])
    result = subprocess.run(
        command(["validate", *flags]),
        cwd=tmp_path,
        env=environment(tmp_path / "home"),
        input=script,
        capture_output=True,
        text=True,
    )
    payload = envelope(result.stdout, result.returncode)
    assert payload["exit"] == 0, payload
    assert payload["data"] == {"ok": True, "durationSec": 1, "problems": []}


@pytest.mark.parametrize("name", ["validate", "render"])
def test_a_slow_writer_on_stdin_is_read_to_the_end(tmp_path: Path, name: str) -> None:
    from ambitious_video.cli.main import STDIN_WAIT_SECONDS

    assert SLOW_WRITER_PAUSE_SECONDS > STDIN_WAIT_SECONDS
    cards: list[dict[str, object]] = [
        {"type": "title_card", "duration": 2.0, "text": word} for word in ["One", "Two", "Three"]
    ]
    lines = script_of("slow.mp4", cards).splitlines(keepends=True)
    proc = subprocess.Popen(
        command([name]),
        cwd=tmp_path,
        env=environment(tmp_path / "home"),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert proc.stdin is not None and proc.stdout is not None
    try:
        proc.stdin.write("".join(lines[:-2]))
        proc.stdin.flush()
        with pytest.raises(subprocess.TimeoutExpired):
            proc.wait(timeout=SLOW_WRITER_PAUSE_SECONDS)
        proc.stdin.write("".join(lines[-2:]))
        proc.stdin.close()
        code = proc.wait(timeout=OPEN_STDIN_LIMIT_SECONDS)
        payload = envelope(proc.stdout.read(), code)
    finally:
        proc.kill()
        proc.stdout.close()
        proc.wait()
    assert payload["exit"] == 0, payload
    data = payload["data"]
    assert isinstance(data, dict)
    assert data["durationSec"] == 6
