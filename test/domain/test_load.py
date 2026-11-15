from __future__ import annotations

import json
from pathlib import Path

import yaml

from ambitious_video.domain.load import load_script
from ambitious_video.domain.timeline import timeline_for

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples"


def test_valid_examples_load() -> None:
    for path in sorted(EXAMPLES.glob("*.yaml")):
        script, problems = load_script(yaml.safe_load(path.read_text(encoding="utf-8")))
        assert problems == (), path.name
        assert script is not None


def test_invalid_examples_are_rejected() -> None:
    expected = json.loads((EXAMPLES / "invalid" / "EXPECTED.json").read_text(encoding="utf-8"))
    for name in expected:
        script, problems = load_script(yaml.safe_load((EXAMPLES / "invalid" / name).read_text(encoding="utf-8")))
        assert script is None, name
        assert problems, name


def test_marketing_timeline_without_files() -> None:
    raw = yaml.safe_load((EXAMPLES / "ambitious-marketing.yaml").read_text(encoding="utf-8"))
    script, problems = load_script(raw)
    assert script is not None and problems == ()
    timeline = timeline_for(script, None, require_files=False)
    assert timeline.faults == ()
    assert timeline.total == 33 * 30


def test_minimal_timeline_without_files() -> None:
    raw = yaml.safe_load((EXAMPLES / "minimal.yaml").read_text(encoding="utf-8"))
    script, _problems = load_script(raw)
    assert script is not None
    timeline = timeline_for(script, None, require_files=False)
    assert timeline.faults == ()
    assert timeline.total == 7 * 30
