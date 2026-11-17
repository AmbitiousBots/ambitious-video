from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from PIL import Image
from test_render import run, subprocess_verify

from ambitious_video.media.film import MEAN_LIMIT, distance

SCRIPT = "\n".join(
    [
        "schemaVersion: 2",
        "output: { path: NAME.mp4, width: 320, height: 180, fps: 10ENCODE }",
        "tracks:",
        "  - id: main",
        "    clips:",
        '      - { type: title_card, duration: 1.0, text: "TITLE" }',
        '      - { type: title_card, duration: 1.0, text: "Two" }',
        "  - id: voice",
        "    clips:",
        '      - { type: voiceover, at: 0.2, duration: 0.6, text: "A caption." }',
        "  - id: captions",
        "    from: voice",
        "cuts:",
        "  - { at: 1.0, transition: crossfade, duration: 0.2 }",
        "",
    ]
)

GRAIN = "\n".join(
    [
        "schemaVersion: 2",
        "output: { path: grain.mp4, width: 320, height: 180, fps: 10 }",
        "tracks:",
        "  - id: main",
        "    clips:",
        "      - { type: image, path: grain.png, duration: 1.0, kenburns: { from: 1.0, to: 1.1 } }",
        '      - { type: title_card, duration: 1.0, text: "Two" }',
        "cuts:",
        "  - { at: 1.0, transition: crossfade, duration: 0.4 }",
        "",
    ]
)


def script(name: str, encode: str | None = None, title: str = "One") -> str:
    chosen = "" if encode is None else ", encode: " + encode
    return SCRIPT.replace("NAME", name).replace("ENCODE", chosen).replace("TITLE", title)


def video_stream(path: Path) -> dict[str, object]:
    shown = ["stream=codec_name,profile,pix_fmt,color_space,color_range"]
    probe = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", *shown, "-of", "json", str(path)]
    result = subprocess.run(probe, capture_output=True, text=True, check=True)
    stream = json.loads(result.stdout)["streams"][0]
    assert isinstance(stream, dict)
    return stream


def rendered(home: Path, work: Path, text: str) -> dict[str, object]:
    made = run(home, work, ["render"], text)
    assert made["exit"] == 0, made
    data = made["data"]
    assert isinstance(data, dict)
    return data


def checks_of(payload: dict[str, object]) -> dict[str, bool]:
    data = payload["data"]
    assert isinstance(data, dict) and isinstance(data["checks"], list)
    return {str(item["name"]): item["ok"] is True for item in data["checks"] if isinstance(item, dict)}


def test_default_render_is_h264_yuv420p_and_verifies(tmp_path: Path) -> None:
    data = rendered(tmp_path / "home", tmp_path, script("web"))
    stream = video_stream(tmp_path / "web.mp4")
    assert stream["codec_name"] == "h264" and stream["pix_fmt"] == "yuv420p"
    assert stream["profile"] == "High"
    assert stream["color_space"] == "bt709" and stream["color_range"] == "tv"
    head = (tmp_path / "web.mp4").read_bytes()
    assert 0 < head.find(b"moov") < head.find(b"mdat")
    checked = subprocess_verify(tmp_path / "home", tmp_path, str(data["jobId"]))
    assert checked["exit"] == 0, checked
    assert checks_of(checked) == {
        "readable": True,
        "duration": True,
        "frames": True,
        "resolution": True,
        "audio": True,
        "encoding": True,
        "title-0": True,
        "title-1": True,
        "cut-10": True,
    }


def test_a_web_render_of_a_noise_still_with_a_crossfade_verifies(tmp_path: Path) -> None:
    noise = hashlib.shake_256(b"grain").digest(320 * 180 * 3)
    Image.frombytes("RGB", (320, 180), noise).save(tmp_path / "grain.png")
    data = rendered(tmp_path / "home", tmp_path, GRAIN)
    assert video_stream(tmp_path / "grain.mp4")["pix_fmt"] == "yuv420p"
    checked = subprocess_verify(tmp_path / "home", tmp_path, str(data["jobId"]))
    assert checked["exit"] == 0, checked
    assert checks_of(checked) == {
        "readable": True,
        "duration": True,
        "frames": True,
        "resolution": True,
        "audio": True,
        "encoding": True,
        "title-1": True,
        "cut-10": True,
    }


def test_an_even_shift_of_three_levels_is_over_the_mean_limit() -> None:
    drawn = Image.new("RGB", (320, 180), (100, 100, 100))
    mean, worst_block = distance(drawn, Image.new("RGB", (320, 180), (103, 103, 103)))
    assert mean == 3.0 and mean > MEAN_LIMIT
    assert worst_block == 3


def test_encode_web_is_the_default(tmp_path: Path) -> None:
    rendered(tmp_path / "home", tmp_path, script("named", "web"))
    rendered(tmp_path / "home", tmp_path, script("unnamed"))
    assert video_stream(tmp_path / "named.mp4") == video_stream(tmp_path / "unnamed.mp4")
    planned = run(tmp_path / "home", tmp_path, ["plan"], script("unnamed"))
    assert isinstance(planned["data"], dict) and planned["data"]["encode"] == "web"


def test_lossless_render_is_exact_rgb_and_repeats(tmp_path: Path) -> None:
    first = rendered(tmp_path / "home-a", tmp_path, script("exact", "lossless"))
    stream = video_stream(tmp_path / "exact.mp4")
    assert stream["codec_name"] == "h264" and stream["pix_fmt"] == "gbrp"
    second = rendered(tmp_path / "home-b", tmp_path, script("exact", "lossless"))
    assert first["sha256"] == second["sha256"]
    checked = subprocess_verify(tmp_path / "home-a", tmp_path, str(first["jobId"]))
    assert checked["exit"] == 0, checked
    assert all(checks_of(checked).values()) and "cut-10" in checks_of(checked)


def test_verify_rejects_another_video_in_either_encoding(tmp_path: Path) -> None:
    for encode in ("web", "lossless"):
        home = tmp_path / ("home-" + encode)
        data = rendered(home, tmp_path, script("kept-" + encode, encode))
        rendered(home, tmp_path, script("other-" + encode, encode, title="Else"))
        (tmp_path / ("kept-" + encode + ".mp4")).write_bytes((tmp_path / ("other-" + encode + ".mp4")).read_bytes())
        checked = subprocess_verify(home, tmp_path, str(data["jobId"]))
        assert checked["exit"] == 1, checked
        errors = checked["errors"]
        assert isinstance(errors, list) and isinstance(errors[0], dict) and errors[0]["code"] == "E_VERIFY"
        found = checks_of(checked)
        assert found["title-0"] is False and found["title-1"] is True and found["encoding"] is True


def test_verify_rejects_a_file_in_the_other_encoding(tmp_path: Path) -> None:
    for wanted, other in (("lossless", "web"), ("web", "lossless")):
        home = tmp_path / ("home-" + wanted)
        data = rendered(home, tmp_path, script("wanted-" + wanted, wanted))
        rendered(home, tmp_path, script("other-" + other, other))
        (tmp_path / ("wanted-" + wanted + ".mp4")).write_bytes((tmp_path / ("other-" + other + ".mp4")).read_bytes())
        checked = subprocess_verify(home, tmp_path, str(data["jobId"]))
        assert checked["exit"] == 1, checked
        errors = checked["errors"]
        assert isinstance(errors, list) and isinstance(errors[0], dict) and errors[0]["code"] == "E_VERIFY"
        assert checks_of(checked)["encoding"] is False


def test_an_unknown_encode_is_refused(tmp_path: Path) -> None:
    refused = run(tmp_path / "home", tmp_path, ["validate"], script("bad", "fast"))
    assert refused["exit"] == 1
    data = refused["data"]
    assert isinstance(data, dict) and isinstance(data["problems"], list) and isinstance(data["problems"][0], dict)
    assert data["problems"][0]["at"] == "/output/encode"
