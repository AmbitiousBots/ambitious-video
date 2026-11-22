from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ambitious_video.providers.base import Deferred, RunResult


def await_result(provider: Deferred, request_id: str, secret: str, out: Path) -> RunResult | None:
    patience = provider.patience
    deadline = time.monotonic() + patience.wait_seconds
    try:
        while True:
            found = provider.collect(request_id, secret, out)
            if found is not None or time.monotonic() + patience.poll_seconds > deadline:
                return found
            time.sleep(patience.poll_seconds)
    except BaseException:
        del secret
        raise
