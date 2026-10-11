from pathlib import Path
from threading import RLock, local

import portalocker

import bafser_config


class UpdateLock:
    """Reentrant within a thread; shared by processes in this project directory."""

    def __init__(self):
        self._thread_lock = RLock()
        self._state = local()

    def acquire(self):
        self._thread_lock.acquire()
        try:
            depth = getattr(self._state, "depth", 0)
            if depth == 0:
                Path(bafser_config.update_lock_path).parent.mkdir(parents=True, exist_ok=True)
                lock = portalocker.Lock(bafser_config.update_lock_path, timeout=30)
                lock.acquire()
                self._state.file_lock = lock
            self._state.depth = depth + 1
        except BaseException:
            self._thread_lock.release()
            raise

    def release(self):
        try:
            self._state.depth -= 1
            if self._state.depth == 0:
                self._state.file_lock.release()
                del self._state.file_lock
        finally:
            self._thread_lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.release()


update_lock = UpdateLock()
