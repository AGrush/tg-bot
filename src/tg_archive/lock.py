from __future__ import annotations

import os
from pathlib import Path

from tg_archive.logutil import say


class ArchiveLock:
    """One writer at a time. A crash releases the lock when the process exits."""

    def __init__(self, path: Path):
        self.path = path
        self._handle = None

    def __enter__(self) -> "ArchiveLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+", encoding="utf-8")
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            raise SystemExit(
                "Another tg-archive command is using this data folder. Stop it, then try again."
            ) from None
        except ImportError:
            say("File locking is unavailable on this system. Run one tg-archive command at a time.")
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        self._handle = handle
        return self

    def __exit__(self, *exc: object) -> None:
        handle = self._handle
        if handle is None:
            return
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except ImportError:
            pass
        handle.close()
        self._handle = None
