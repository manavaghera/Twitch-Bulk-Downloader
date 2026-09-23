"""Locks shared by every program of the project: the web page, the scheduled
Autopilot, the command-line tools and the background stats recorder.

Two programs downloading at once would clash on file numbers in the same
folder and each would save the download history over the other's. A lock
file in data/ that the operating system holds for us stops that - and the OS
lets it go by itself if a program crashes, so a lock can never get stuck.
"""

import os
import sys
import threading
import time
from contextlib import contextmanager

from .config import DATA_DIR

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


class FileLock:
    """An exclusive lock on data/<name>.lock, across processes."""

    def __init__(self, name):
        self.path = DATA_DIR / ("%s.lock" % name)
        self._handle = None
        # Threads of this program take turns here first (it may be released
        # by a different thread than took it, so a plain Lock, not an RLock).
        self._inside = threading.Lock()

    def _try(self):
        if not self._inside.acquire(blocking=False):
            return False
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            handle = open(self.path, "a+")
        except OSError:
            self._inside.release()
            return False
        try:
            if sys.platform == "win32":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            self._inside.release()
            return False
        self._handle = handle
        return True

    def acquire(self, timeout=0.0, poll=0.5):
        """True once held; False if another program still holds it after `timeout` s."""
        deadline = time.time() + timeout
        while True:
            if self._try():
                return True
            if time.time() >= deadline:
                return False
            time.sleep(poll)

    def release(self):
        if self._handle is None:
            return
        try:
            if sys.platform == "win32":
                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        self._handle.close()
        self._handle = None
        self._inside.release()

    @property
    def held(self):
        return self._handle is not None

    def busy_elsewhere(self):
        """True when another program holds the lock right now (and we do not)."""
        if self.held:
            return False
        if self._try():
            self.release()
            return False
        return True

    @contextmanager
    def hold(self, timeout=30.0):
        if not self.acquire(timeout, poll=0.05):
            raise TimeoutError("%s is locked by another program" % self.path.name)
        try:
            yield self
        finally:
            self.release()


# One download run at a time across all programs.
DOWNLOADS = FileLock("downloads")
PID_NOTE = DATA_DIR / "downloads.owner"


def note_owner(label):
    """Say who holds the download lock, for the other programs' messages."""
    try:
        PID_NOTE.write_text("%s|%d|%d" % (label, os.getpid(), time.time()), encoding="utf-8")
    except OSError:
        pass


def owner():
    """(label, since) of whoever holds the download lock, or None."""
    try:
        label, _pid, since = PID_NOTE.read_text(encoding="utf-8").split("|")
        return label, float(since)
    except (OSError, ValueError):
        return None
