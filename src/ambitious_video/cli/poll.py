from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ambitious_video.providers.base import Patience, RunResult


def await_result(patience: Patience, collect: Callable[[], RunResult | None]) -> RunResult | None:
    deadline = time.monotonic() + patience.wait_seconds
    while True:
        found = collect()
        if found is not None or time.monotonic() + patience.poll_seconds > deadline:
            return found
        time.sleep(patience.poll_seconds)
