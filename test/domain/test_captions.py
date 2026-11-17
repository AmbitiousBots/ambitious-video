from __future__ import annotations

import yaml

from ambitious_video.domain.captions import Caption, segments, sentences
from ambitious_video.domain.load import load_script
from ambitious_video.domain.timeline import Timeline, timeline_for


def timeline_of(text: str) -> Timeline:
    script, problems = load_script(yaml.safe_load(text))
    assert script is not None, problems
    timeline = timeline_for(script, None, require_files=False)
    assert timeline.faults == ()
    return timeline


SCRIPT = """
schemaVersion: 2
output: { path: out.mp4, width: 320, height: 180, fps: 10 }
tracks:
  - id: main
    clips:
      - { type: title_card, duration: 4.0, text: "One" }
  - id: first
    clips:
      - { type: voiceover, at: 0.5, duration: 1.0, text: "First line." }
      - { type: voiceover, at: 2.0, duration: 1.0, text: "Second line." }
  - id: second
    clips:
      - { type: voiceover, at: 3.0, duration: 0.5, text: "Third line." }
"""


def test_sentences_split_after_a_full_stop_question_or_exclamation() -> None:
    assert sentences("One. Two? Three! Four") == ("One.", "Two?", "Three!", "Four")
    assert sentences("  Costs 3.5 units.  ") == ("Costs 3.5 units.",)
    assert sentences("No stop at the end") == ("No stop at the end",)


def test_one_sentence_is_one_segment_for_the_whole_clip() -> None:
    assert segments("captions", "One line.", 5, 20) == (Caption("captions", 5, 20, "One line."),)


def test_sentences_share_the_clip_by_their_length() -> None:
    found = segments("captions", "Short. A much longer sentence!", 10, 30)
    assert found == (
        Caption("captions", 10, 6, "Short."),
        Caption("captions", 16, 24, "A much longer sentence!"),
    )
    assert found[0].end == found[1].start and found[-1].end == 40


def test_segments_are_the_same_every_time() -> None:
    text = "One. Two two. Three three three."
    assert segments("captions", text, 0, 100) == segments("captions", text, 0, 100)
    assert sum(item.frames for item in segments("captions", text, 0, 100)) == 100


def test_a_clip_too_short_to_split_keeps_one_segment() -> None:
    assert segments("captions", "A. B. C.", 3, 2) == (Caption("captions", 3, 2, "A. B. C."),)


def test_no_captions_track_means_no_captions() -> None:
    assert timeline_of(SCRIPT).captions == ()


def test_a_captions_track_follows_only_its_source_track() -> None:
    timeline = timeline_of(SCRIPT + "  - id: captions\n    from: first\n")
    assert timeline.captions == (
        Caption("captions", 5, 10, "First line."),
        Caption("captions", 20, 10, "Second line."),
    )
    other = timeline_of(SCRIPT + "  - id: words\n    from: second\n")
    assert other.captions == (Caption("words", 30, 5, "Third line."),)
