from __future__ import annotations

import fcntl
import os


class Hold:
    def __init__(self, path: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
        self.held = False

    def try_lock(self) -> bool:
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        self.held = True
        return True

    def lock(self) -> None:
        fcntl.flock(self._fd, fcntl.LOCK_EX)
        self.held = True

    def close(self) -> None:
        if self.held:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            self.held = False
        os.close(self._fd)
