from __future__ import annotations

import logging
import threading
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import BaseRequestHandler, TCPServer

import pytest

from ambitious_video import providers
from ambitious_video.domain import types as clips
from ambitious_video.providers.base import Deferred, Provider, ProviderError

KEY = "provider-contract-test-value"
LOOPBACK = ("127.0.0.1", 0)


@contextmanager
def serving(server: TCPServer) -> Iterator[int]:
    with server:
        thread = threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True)
        thread.start()
        try:
            yield server.server_address[1]
        finally:
            server.shutdown()
            thread.join()


def example(provider: Provider) -> clips.PaidClip:
    name, model = provider.id, provider.models[0]
    if provider.kind == "speech":
        return clips.Speech(0, "Hello", None, name, model, "speaker")
    if provider.kind == "scene":
        fallback = clips.TitleCard(1, "Title", None)
        return clips.GeneratedScene(1, "A lake", name, model, None, None, None, None, None, fallback)
    if provider.kind == "lipsync":
        return clips.LipSync(1, name, model, clips.RelativePath("video.mp4"), None, clips.TrackId("voice"), None)
    raise AssertionError(f"Add a transport example for {name}")


@pytest.mark.parametrize("status", [302, 401, 0], ids=["redirect", "unauthorized", "disconnect"])
@pytest.mark.parametrize(
    ("provider", "operation"),
    [
        pytest.param(provider, operation, id=f"{provider.id}-{operation}")
        for provider in providers.registered()
        for operation in (("run", "collect") if isinstance(provider, Deferred) else ("run",))
    ],
)
def test_authenticated_transport(
    provider: Provider, operation: str, status: int, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    authenticated: list[bool] = []
    reached = threading.Event()

    class RejectTLS(BaseRequestHandler):
        def handle(self) -> None:
            reached.set()

    class Origin(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            authenticated.append(any(value in (KEY, "Bearer " + KEY) for value in self.headers.values()))
            if status == 0:
                return
            self.send_response(status)
            if status == 302:
                self.send_header("Location", f"https://localhost:{target}/")
            self.end_headers()
            if status == 401:
                self.wfile.write(KEY.encode())

        do_GET = do_POST

        def log_message(self, *_args: object) -> None:
            pass

    monkeypatch.setenv("no_proxy", "*")
    caplog.set_level(logging.DEBUG)
    with serving(TCPServer(LOOPBACK, RejectTLS)) as target, serving(HTTPServer(LOOPBACK, Origin)) as port:
        url = f"http://127.0.0.1:{port}"
        urls = {"submit_url": url, "status_url": url + "/"} if isinstance(provider, Deferred) else {"endpoint": url}
        assert hasattr(provider, "config")
        config = replace(provider.config, timeout_seconds=3, **urls)
        monkeypatch.setattr(provider, "config", config)
        out = tmp_path / "result"
        with pytest.raises(ProviderError) as caught:
            if operation == "collect":
                assert isinstance(provider, Deferred)
                provider.collect("job", KEY, out)
            else:
                media = tmp_path / "video.mp4"
                media.write_bytes(b"fixture")
                provider.run(provider.params(example(provider)), {"video": media, "audio": media}, KEY, out)

    assert authenticated == [True]
    assert not reached.is_set(), "Route authenticated requests through providers/http.py to reject redirects"
    error = caught.value
    assert (error.provider, error.status) == (provider.id, status)
    assert error.__cause__ is None and error.__context__ is None
    detail = traceback.TracebackException.from_exception(error, capture_locals=True)
    if KEY in "".join(detail.format()) or KEY in repr(vars(error)) or KEY in caplog.text:
        pytest.fail("provider error or log retained the test credential")
    assert not out.exists()
