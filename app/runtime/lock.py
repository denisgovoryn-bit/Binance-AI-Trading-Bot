"""Single-instance advisory process lock for Unix runtimes."""

import fcntl
import os
from pathlib import Path
from types import TracebackType
from typing import TextIO


class AlreadyRunningError(RuntimeError):
    """Another bot process currently owns the runtime lock."""


class ProcessLock:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self._file: TextIO | None = None

    def __enter__(self) -> "ProcessLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.seek(0)
            pid = handle.read().strip() or "unknown"
            handle.close()
            raise AlreadyRunningError(f"bot already running (pid {pid})") from exc
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        self._file = handle
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        handle = self._file
        if handle is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
        self._file = None
        self.path.unlink(missing_ok=True)
