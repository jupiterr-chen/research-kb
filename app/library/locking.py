"""Cross-process advisory file lock for ingest/render mutual exclusion."""

from __future__ import annotations

import os
import time

try:  # POSIX
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None

try:  # Windows
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None


class LockBusy(Exception):
    pass


class FileLock:
    """Advisory exclusive lock held for the lifetime of the object.

    Works across processes on the same host (``flock`` on POSIX, ``msvcrt`` on
    Windows), so a scheduled in-process ingest and a manual ``docker exec ...
    ingest`` cannot run concurrently.
    """

    def __init__(self, path: str):
        self.path = path
        self._fh = None

    def acquire(self, blocking: bool = False, timeout: float = 0.0) -> bool:
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._fh = open(self.path, "a+")
        if os.path.getsize(self.path) == 0:
            self._fh.write("0")
            self._fh.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                self._lock()
                return True
            except OSError:
                if not blocking or time.monotonic() >= deadline:
                    self._fh.close()
                    self._fh = None
                    return False
                time.sleep(0.2)

    def _lock(self) -> None:
        assert self._fh is not None
        if fcntl is not None:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        elif msvcrt is not None:
            self._fh.seek(0)
            msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:  # pragma: no cover
            raise OSError("no file locking backend available")

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            elif msvcrt is not None:
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        finally:
            self._fh.close()
            self._fh = None

    def __enter__(self) -> "FileLock":
        if not self.acquire():
            raise LockBusy(self.path)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
