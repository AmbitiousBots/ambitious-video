from __future__ import annotations

import io
import urllib.error

import pytest

from ambitious_video.ai.speech import SpeechError, speak

SECRET = "not-a-real-key"


def _refused(*_args: object, **_kwargs: object) -> object:
    raise urllib.error.HTTPError(
        "https://api.x.ai/v1/tts",
        401,
        "denied",
        None,
        io.BytesIO(f"body {SECRET}".encode()),
    )


def _failed(*_args: object, **_kwargs: object) -> object:
    raise urllib.error.URLError(f"Bearer {SECRET}")


def _text(error: SpeechError) -> str:
    parts = [str(error), str(error.__cause__), str(error.__context__)]
    frame = error.__traceback__
    while frame is not None:
        if frame.tb_frame.f_code.co_name == "speak":
            parts.extend(repr(value) for value in frame.tb_frame.f_locals.values())
        frame = frame.tb_next
    return " ".join(parts)


def test_http_error_drops_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("urllib.request.urlopen", _refused)
    with pytest.raises(SpeechError) as caught:
        speak("hello", "default", SECRET)
    assert SECRET not in _text(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.status == 401


def test_other_error_drops_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("urllib.request.urlopen", _failed)
    with pytest.raises(SpeechError) as caught:
        speak("hello", "default", SECRET)
    assert SECRET not in _text(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.status == 0
