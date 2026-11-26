from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import jsonschema
import pytest
from PIL import Image

from ambitious_video.cli import engine
from ambitious_video.domain.load import load_script

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "examples" / "samples"
NAMES = {
    "example-plumbing": "Example Plumbing Co.",
    "example-accounting": "Example Accounting Office",
    "example-fitness": "Example Fitness Studio",
    "example-bookshop": "Example Bookshop",
}
SLUGS = sorted(NAMES)
MAX_BINARY_BYTES = 60_000


def _json(path: Path) -> dict[str, object]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _schema() -> dict[str, object]:
    loaded = json.loads((ROOT / "schemas" / "video-script.v2.json").read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _generator() -> ModuleType:
    path = ROOT / "scripts" / "make_sample_assets.py"
    spec = importlib.util.spec_from_file_location("make_sample_assets", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("a sample must not open a socket")

    monkeypatch.setattr("socket.socket", refuse)
    monkeypatch.setattr("socket.create_connection", refuse)
    monkeypatch.delenv("XAI_API_KEY", raising=False)


def test_the_four_samples_are_present() -> None:
    assert sorted(path.name for path in SAMPLES.iterdir() if path.is_dir()) == SLUGS


@pytest.mark.parametrize("slug", SLUGS)
def test_sample_script_matches_the_schema(slug: str) -> None:
    script = _json(SAMPLES / slug / "script.json")
    jsonschema.Draft202012Validator(_schema()).validate(script)
    assert script["schemaVersion"] == 2
    tracks = script["tracks"]
    assert isinstance(tracks, list)
    assert all(clip["type"] != "speech" for track in tracks for clip in track.get("clips", []))


@pytest.mark.parametrize("slug", SLUGS)
def test_sample_validates_offline(slug: str, no_network: None) -> None:
    script = _json(SAMPLES / slug / "script.json")
    checked = engine.validate_data(script, SAMPLES / slug)
    assert checked["ok"] is True and checked["problems"] == []


@pytest.mark.parametrize("slug", SLUGS)
def test_sample_plans_offline_and_the_same_twice(slug: str, no_network: None) -> None:
    script = _json(SAMPLES / slug / "script.json")
    first = engine.canonical(engine.plan_data(script, SAMPLES / slug))
    second = engine.canonical(engine.plan_data(_json(SAMPLES / slug / "script.json"), SAMPLES / slug))
    assert first == second
    plan = json.loads(first)
    assert {step["type"] for step in plan["steps"]} == {"title_card", "image", "voiceover"}
    assert all(not asset["path"].startswith("/") for asset in plan["assets"])
    assert str(ROOT) not in first


@pytest.mark.parametrize("slug", SLUGS)
def test_sample_brand_file_matches_its_script(slug: str) -> None:
    brand = _json(SAMPLES / slug / "brand.json")
    script = _json(SAMPLES / slug / "script.json")
    assert script["brand"] == brand
    assert "font" not in brand
    assert brand["logo"] == "assets/logo.png"
    whole = {"schemaVersion": 2, "output": script["output"], "tracks": script["tracks"], "brand": brand}
    loaded, problems = load_script(whole)
    assert loaded is not None and problems == ()


@pytest.mark.parametrize("slug", SLUGS)
def test_sample_says_it_is_fictional_sample_data(slug: str) -> None:
    readme = (SAMPLES / slug / "README.md").read_text(encoding="utf-8")
    assert "SAMPLE DATA" in readme and "fictional" in readme and NAMES[slug] in readme
    assert "SAMPLE DATA" in (SAMPLES / slug / "inputs.json").read_text(encoding="utf-8")
    script = _json(SAMPLES / slug / "script.json")
    tracks = script["tracks"]
    assert isinstance(tracks, list)
    opening = tracks[0]["clips"][0]
    assert opening["text"] == NAMES[slug]
    assert "SAMPLE DATA" in opening["subtitle"]


@pytest.mark.parametrize("slug", SLUGS)
def test_sample_binaries_stay_small(slug: str) -> None:
    binaries = [path for path in (SAMPLES / slug).rglob("*") if path.is_file() and path.suffix not in {".json", ".md"}]
    assert {path.suffix for path in binaries} == {".png"}
    assert sum(path.stat().st_size for path in binaries) < MAX_BINARY_BYTES


def test_sample_pictures_come_from_the_committed_script(tmp_path: Path) -> None:
    generator = _generator()
    written = generator.write_all(tmp_path)
    assert len(written) == 4 * 4
    for made in written:
        kept = SAMPLES / made.relative_to(tmp_path)
        with Image.open(made) as fresh, Image.open(kept) as committed:
            assert fresh.size == committed.size, kept
            assert fresh.convert("RGB").tobytes() == committed.convert("RGB").tobytes(), kept


def test_sample_pictures_use_the_brand_colors() -> None:
    generator = _generator()
    for sample in generator.SAMPLE_COLORS:
        colors = _json(SAMPLES / sample.slug / "brand.json")["colors"]
        assert isinstance(colors, dict)
        assert [generator.rgb(colors[key]) for key in ("bg", "fg", "accent")] == [sample.bg, sample.fg, sample.accent]
