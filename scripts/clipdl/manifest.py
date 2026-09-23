"""The per-clip log that makes resuming, skipping and reporting possible."""

import threading
import time
from datetime import datetime, timezone

from .locks import FileLock
from .util import load_json, save_json

# Held for the moment of a save, so two programs never write the file at once.
_FILE_LOCK = FileLock("manifest")


def _read(path):
    data = load_json(path, None)
    if not isinstance(data, dict) or not isinstance(data.get("clips"), dict):
        data = {"version": 1, "clips": {}}
    return data

# ---------------------------------------------------------------------------
# Download manifest: what happened to every clip, for resume and reporting
# ---------------------------------------------------------------------------
class Manifest:
    """A JSON log of clip_id -> outcome, shared by the download threads.

    Saving merges: the file is read again and only the clips this run touched
    are written over it, so a download in another program (the scheduled
    Autopilot, a second window) never loses what it recorded meanwhile.
    """

    def __init__(self, path):
        self.path = path
        self.data = _read(path)
        self._touched = set()
        self._lock = threading.Lock()
        self._last_save = 0.0

    def record(self, job, status, reason="", size_bytes=0, height=0):
        """Store one outcome: downloaded / skipped-exists / failed / cancelled."""
        with self._lock:
            self._touched.add(job.clip_id)
            self.data["clips"][job.clip_id] = {
                "height": height,
                "game": job.game_name,
                "streamer": job.streamer,
                "title": job.title,
                "url": job.url,
                "file": str(job.path),
                "status": status,
                "reason": reason,
                "bytes": size_bytes,
                "views": job.views,
                "created_at": job.created_at,
                "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            # Writing on every single clip would hammer the disk on a 1000-clip
            # run, so save at most every couple of seconds plus a final flush.
            if time.time() - self._last_save > 2.0:
                self._save()

    def flush(self):
        with self._lock:
            self._save()

    def _save(self):
        """Merge this run's clips into the file on disk (call with self._lock held)."""
        try:
            with _FILE_LOCK.hold(timeout=30):
                disk = _read(self.path)
                for clip_id in self._touched:
                    disk["clips"][clip_id] = self.data["clips"][clip_id]
                save_json(self.path, disk)
                self.data = disk             # and pick up what the others recorded
        except TimeoutError:                # never lose the run's work over a stuck lock
            save_json(self.path, self.data)
        self._last_save = time.time()

    def status_of(self, clip_id):
        return (self.data["clips"].get(clip_id) or {}).get("status")

    def known_ids(self, game_name):
        """Clip ids this game has already pulled down on an earlier run.

        Only clips that actually landed count. A clip that failed (deleted,
        sub-only, a dead connection) is deliberately left out, so a later run
        is free to try it again.
        """
        done = ("downloaded", "skipped-exists")
        return {clip_id for clip_id, entry in self.data["clips"].items()
                if entry.get("game") == game_name and entry.get("status") in done}
