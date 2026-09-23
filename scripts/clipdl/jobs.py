"""Running a download or a trend scan in the background for the web page.

The page cannot sit inside a five-minute download: it has to keep redrawing to
show progress. So the work runs on its own thread, everything it says() is
collected here, and the page reads that back on every redraw.

One job of each KIND at a time: a download and a trend scan can run side by
side, two downloads cannot. Each kind has its own Cancel switch and its own
log, so cancelling a download never stops the research and the two logs never
mix. Downloads also take a lock shared with the other programs of the project
(the scheduled Autopilot, the command line), so two programs never download
into the same folders at once.
"""

import re
import threading
import time
import traceback

from . import locks
from .api import TwitchError
from .config import STOP, set_kind
from .util import set_output_sink

_kind_locks = {}                    # kind -> threading.Lock
_latest = {}                        # kind -> the most recent Job of that kind
_PROGRESS = re.compile(r"\[(\d+)/(\d+)\]")


class Busy(RuntimeError):
    """Raised when a job is asked to start while another one of its kind runs."""


def stop_for(kind):
    """The Cancel switch a job of this kind answers to."""
    return STOP.event(kind)


class Job:
    def __init__(self, kind, label, work):
        self.kind = kind            # "download" or "trends"
        self.label = label          # shown on the page, e.g. "VALORANT, 50 clips"
        self._work = work
        self.lines = []
        self._partial = ""
        self._lock = threading.Lock()
        self.result = None
        self.error = None
        self.cancelled = False
        self.started = time.time()
        self.finished = None
        self._thread = None

    # -- output capture -------------------------------------------------------
    def _write(self, text, end):
        with self._lock:
            chunk = self._partial + text + end
            *complete, self._partial = chunk.split("\n")
            self.lines.extend(complete)

    def text(self, last=None):
        with self._lock:
            lines = self.lines + ([self._partial] if self._partial else [])
        return "\n".join(lines[-last:] if last else lines)

    def progress(self):
        """(done, total) from the latest "[12/50]" line, or None."""
        with self._lock:
            for line in reversed(self.lines[-200:]):
                match = _PROGRESS.search(line)
                if match:
                    return int(match.group(1)), int(match.group(2))
        return None

    # -- lifecycle ------------------------------------------------------------
    @property
    def running(self):
        return self.finished is None

    @property
    def elapsed(self):
        return (self.finished or time.time()) - self.started

    @property
    def stop(self):
        return stop_for(self.kind)

    def _run(self):
        set_kind(self.kind)             # say() and STOP now mean this job's log and switch
        try:
            self.result = self._work()
        except TwitchError as error:
            self.error = str(error)
        except BaseException:           # the page must hear about anything at all
            self.error = traceback.format_exc(limit=6)
        finally:
            self.cancelled = self.stop.is_set()
            set_output_sink(None, self.kind)
            self.finished = time.time()
            if self.kind == "download":
                locks.DOWNLOADS.release()
            _kind_locks[self.kind].release()

    def cancel(self):
        """Ask the work to stop. Downloads already in flight finish first."""
        self.stop.set()


def start(kind, label, work):
    """Start `work()` in the background and return its Job. Raises Busy."""
    lock = _kind_locks.setdefault(kind, threading.Lock())
    if not lock.acquire(blocking=False):
        raise Busy("Another %s is still going - wait for it or cancel it first."
                   % ("download" if kind == "download" else "scan"))
    if kind == "download" and not locks.DOWNLOADS.acquire():
        lock.release()
        who = locks.owner()
        raise Busy("%s is downloading right now in another window - this can start when "
                   "it is done." % (who[0] if who else "Another program"))
    if kind == "download":
        locks.note_owner("The web page")
    job = Job(kind, label, work)
    job.stop.clear()                # a cancelled earlier run must not stop this one
    set_output_sink(job._write, kind)
    _latest[kind] = job
    job._thread = threading.Thread(target=job._run, name="clipdl-job-%s" % kind, daemon=True)
    job._thread.start()
    return job


def latest(kind):
    """The newest job of this kind, running or finished, or None."""
    return _latest.get(kind)


def running(kind=None):
    """The job running right now (of `kind`, or any), or None."""
    return next((job for k, job in _latest.items()
                 if job.running and (kind is None or k == kind)), None)
