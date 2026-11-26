from __future__ import annotations

import json
from pathlib import Path

import pytest
from blockfont import write_block_font
from PIL.Image import Image
from test_render import run

from ambitious_video.domain.load import load_script
from ambitious_video.domain.timeline import timeline_for
from ambitious_video.media.film import canvas_for, frame_at
from ambitious_video.steps.common import readable_font

BAR_TOP = 130


def build_script(
    *,
    path: str = "out.mp4",
    encode: str | None = None,
    font: str | None = None,
    voice: list[dict[str, object]] | None = None,
    captions: bool = False,
) -> str:
    output: dict[str, object] = {"path": path, "width": 320, "height": 180, "fps": 10}
    if encode is not None:
        output["encode"] = encode
    tracks: list[dict[str, object]] = [
        {"id": "main", "clips": [{"type": "title_card", "duration": 4.0, "text": "One"}]}
    ]
    if voice is not None:
        tracks.append({"id": "voice", "clips": voice})
    if captions:
        tracks.append({"id": "captions", "from": "voice"})
    doc: dict[str, object] = {"schemaVersion": 2, "output": output, "tracks": tracks}
    if font is not None:
        doc["brand"] = {"font": font}
    return json.dumps(doc)


DEFAULT_VOICE: list[dict[str, object]] = [
    {"type": "voiceover", "at": 0.5, "duration": 1.0, "text": "First line."},
    {"type": "voiceover", "at": 2.0, "duration": 1.0, "text": "Second line."},
]


def frames(text: str, root: Path, indexes: tuple[int, ...]) -> list[Image]:
    script, problems = load_script(json.loads(text))
    assert script is not None, problems
    timeline = timeline_for(script, None, require_files=False)
    assert timeline.faults == ()
    canvas = canvas_for(timeline, root)
    return [frame_at(timeline, index, canvas) for index in indexes]


def bar(image: Image) -> bytes:
    return image.crop((0, BAR_TOP, image.width, image.height)).tobytes()


def problems_of(payload: dict[str, object]) -> list[dict[str, object]]:
    data = payload["data"]
    assert isinstance(data, dict) and isinstance(data["problems"], list)
    return [item for item in data["problems"] if isinstance(item, dict)]


def test_a_script_without_a_captions_track_draws_no_caption_bar(tmp_path: Path) -> None:
    plain = frames(build_script(), tmp_path, (0, 10, 25, 39))
    spoken = frames(build_script(voice=DEFAULT_VOICE), tmp_path, (0, 10, 25, 39))
    assert [image.tobytes() for image in spoken] == [image.tobytes() for image in plain]


def test_captions_show_only_during_each_voiceover_clip(tmp_path: Path) -> None:
    plain = frames(build_script(), tmp_path, (0,))[0]
    before, first, between, second, after = frames(
        build_script(voice=DEFAULT_VOICE, captions=True), tmp_path, (4, 5, 17, 20, 30)
    )
    assert before.tobytes() == plain.tobytes()
    assert between.tobytes() == plain.tobytes()
    assert after.tobytes() == plain.tobytes()
    assert bar(first) != bar(plain) and bar(second) != bar(plain)
    assert bar(first) != bar(second)
    assert first.crop((0, 0, first.width, BAR_TOP)).tobytes() == plain.crop((0, 0, plain.width, BAR_TOP)).tobytes()


def test_a_clip_with_two_sentences_shows_one_sentence_at_a_time(tmp_path: Path) -> None:
    two = [
        {"type": "voiceover", "at": 0.5, "duration": 1.0, "text": "Short. A much longer sentence!"},
        DEFAULT_VOICE[1],
    ]
    one_line = [{"type": "voiceover", "at": 0.5, "duration": 1.0, "text": "Short."}, DEFAULT_VOICE[1]]
    early, late = frames(build_script(voice=two, captions=True), tmp_path, (5, 14))
    only = frames(build_script(voice=one_line, captions=True), tmp_path, (5,))[0]
    assert bar(early) == bar(only)
    assert bar(late) != bar(early)


def test_plan_lists_the_caption_segments(tmp_path: Path) -> None:
    without = run(tmp_path / "home", tmp_path, ["plan"], build_script(voice=DEFAULT_VOICE))
    assert isinstance(without["data"], dict) and without["data"]["captions"] == []
    planned = run(tmp_path / "home", tmp_path, ["plan"], build_script(voice=DEFAULT_VOICE, captions=True))
    assert isinstance(planned["data"], dict)
    assert planned["data"]["captions"] == [
        {"track": "captions", "atFrame": 5, "frames": 10, "text": "First line."},
        {"track": "captions", "atFrame": 20, "frames": 10, "text": "Second line."},
    ]


def test_brand_font_changes_the_drawn_title_and_captions(tmp_path: Path) -> None:
    write_block_font(tmp_path / "fonts" / "blocks.ttf")
    usual_title, usual_caption = frames(build_script(voice=DEFAULT_VOICE, captions=True), tmp_path, (0, 5))
    block_title, block_caption = frames(
        build_script(font="fonts/blocks.ttf", voice=DEFAULT_VOICE, captions=True), tmp_path, (0, 5)
    )
    assert block_title.tobytes() != usual_title.tobytes()
    assert bar(block_caption) != bar(usual_caption)
    assert block_title.getbbox() is not None
    again = frames(build_script(font="fonts/blocks.ttf", voice=DEFAULT_VOICE, captions=True), tmp_path, (0,))[0]
    assert again.tobytes() == block_title.tobytes()


def test_validate_accepts_a_font_file_and_render_uses_it(tmp_path: Path) -> None:
    write_block_font(tmp_path / "fonts" / "blocks.ttf")
    checked = run(tmp_path / "home", tmp_path, ["validate"], build_script(font="fonts/blocks.ttf"))
    assert checked["exit"] == 0, checked
    with_font = run(
        tmp_path / "home",
        tmp_path,
        ["render"],
        build_script(path="font.mp4", encode="lossless", font="fonts/blocks.ttf"),
    )
    without = run(
        tmp_path / "home",
        tmp_path,
        ["render"],
        build_script(path="plain.mp4", encode="lossless"),
    )
    assert with_font["exit"] == 0 and without["exit"] == 0, (with_font, without)
    assert isinstance(with_font["data"], dict) and isinstance(without["data"], dict)
    assert with_font["data"]["sha256"] != without["data"]["sha256"]


def test_a_font_name_is_refused_and_asks_for_a_file(tmp_path: Path) -> None:
    for command in ("validate", "render"):
        refused = run(tmp_path / "home", tmp_path, [command], build_script(font="Sans"))
        assert refused["exit"] == 1 and refused["ok"] is False
        found = problems_of(refused)
        assert [(item["code"], item["at"]) for item in found] == [("E_MEDIA", "/brand/font")]
        assert "path of a font file" in str(found[0]["message"])
    assert not (tmp_path / "out.mp4").exists()


def test_a_missing_or_unreadable_font_file_is_refused(tmp_path: Path) -> None:
    missing = run(tmp_path / "home", tmp_path, ["validate"], build_script(font="fonts/blocks.ttf"))
    assert missing["exit"] == 1
    assert "font file is missing" in str(problems_of(missing)[0]["message"])
    (tmp_path / "fonts").mkdir()
    (tmp_path / "fonts" / "blocks.ttf").write_text("this is not a font\n", encoding="utf-8")
    unread = run(tmp_path / "home", tmp_path, ["validate"], build_script(font="fonts/blocks.ttf"))
    assert unread["exit"] == 1
    found = problems_of(unread)
    assert [(item["code"], item["at"]) for item in found] == [("E_MEDIA", "/brand/font")]
    assert "cannot be read as a font" in str(found[0]["message"])


def test_a_file_that_is_not_a_font_never_loads_a_system_font_of_that_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    system = tmp_path / "system"
    folder = tmp_path / "script"
    write_block_font(system / "Library" / "Fonts" / "blocks.ttf")
    write_block_font(system / "fonts" / "blocks.ttf")
    (folder / "fonts").mkdir(parents=True)
    (folder / "fonts" / "blocks.ttf").write_text("this is not a font\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(system))
    monkeypatch.setenv("XDG_DATA_HOME", str(system))
    assert not readable_font(str(folder / "fonts" / "blocks.ttf"))
    refused = run(tmp_path / "home", folder, ["validate"], build_script(font="fonts/blocks.ttf"))
    assert refused["exit"] == 1, refused
    found = problems_of(refused)
    assert [(item["code"], item["at"]) for item in found] == [("E_MEDIA", "/brand/font")]
    assert "cannot be read as a font" in str(found[0]["message"])


def test_a_font_outside_the_script_folder_is_refused(tmp_path: Path) -> None:
    folder = tmp_path / "scripts"
    folder.mkdir()
    write_block_font(tmp_path / "blocks.ttf")
    for font in ("../blocks.ttf", "/fonts/blocks.ttf"):
        refused = run(tmp_path / "home", folder, ["validate"], build_script(font=font))
        assert refused["exit"] == 1, font
        found = problems_of(refused)
        assert [(item["code"], item["at"]) for item in found] == [("E_MEDIA", "/brand/font")]
        assert "in the folder that holds the script" in str(found[0]["message"])


def test_plan_and_estimate_do_not_need_the_font_file(tmp_path: Path) -> None:
    for command in ("plan", "estimate"):
        answered = run(tmp_path / "home", tmp_path, [command], build_script(font="fonts/blocks.ttf"))
        assert answered["exit"] == 0, answered
