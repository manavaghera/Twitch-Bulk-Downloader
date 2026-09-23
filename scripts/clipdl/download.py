"""Turning chosen clips into files on disk."""

import random
import re
import threading
from concurrent.futures import as_completed

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from .config import (MAX_CONCURRENT_DOWNLOADS, MAX_HEIGHT, MAX_PATH_CHARS,
                     POLITE_DELAY, STOP, ydl_format)
from .util import describe_height, human_size, sanitize, say, thread_pool

# ---------------------------------------------------------------------------
# Downloading
# ---------------------------------------------------------------------------
class DownloadJob:
    """One clip plus the exact file we intend to write."""

    def __init__(self, clip, path, existing, game_name, index):
        self.clip_id = clip.get("id") or ""
        self.url = clip.get("url") or ""
        self.title = clip.get("title") or ""
        self.streamer = clip.get("broadcaster_name") or "unknown"
        self.broadcaster_id = clip.get("broadcaster_id") or ""
        self.views = clip.get("view_count", 0)
        self.created_at = clip.get("created_at", "")
        self.path = path            # where we want the .mp4
        self.existing = existing    # a matching file from an earlier run, or None
        self.game_name = game_name
        self.index = index


class Counter:
    """A shared '[n/total]' counter for the progress lines."""

    def __init__(self):
        self.value = 0
        self._lock = threading.Lock()

    def bump(self):
        with self._lock:
            self.value += 1
            return self.value


class _QuietLogger:
    """yt-dlp prints to stdout unless given a logger; keep it quiet but keep
    hold of the last error line so a failure can be explained."""

    def __init__(self):
        self.last_error = ""

    def debug(self, message):
        pass

    def info(self, message):
        pass

    def warning(self, message):
        pass

    def error(self, message):
        self.last_error = str(message)


def trim_for_path(folder, prefix, title, extension=".mp4"):
    """Shorten a title so folder + file name stays under the Windows path limit."""
    allowance = MAX_PATH_CHARS - len(str(folder)) - 1 - len(prefix) - len(extension)
    if allowance < 8:
        # The folder itself is already very deep; keep a token name.
        return title[:8].rstrip(" .") or "clip"
    return title[:allowance].rstrip(" .") or "clip"


def name_tail(filename):
    """The 'Streamer_(Title).mp4' part of a name, used to match a rerun to a file."""
    tail = filename.split("_", 1)[1] if "_" in filename else filename
    # Dropping the brackets lets files saved before titles were bracketed still
    # match the name this run would give them, so they are not fetched twice.
    return tail.replace("(", "").replace(")", "")


def next_free_number(folder):
    """One past the highest 'NNN_' prefix already in the folder, or 1 if empty.

    A "new clips only" run adds a batch beside files it is deliberately not
    touching, so it cannot start counting at 001 again - two different clips
    would both want to be 001. Carrying on from the last number keeps every
    name unique and keeps each batch together in a directory listing.
    """
    highest = 0
    for path in folder.glob("*.mp4"):
        match = re.match(r"(\d+)_", path.name)
        if match:
            highest = max(highest, int(match.group(1)))
    return highest + 1


def build_jobs(clips, folder, game_name, start_index=1):
    """Turn clips into download jobs, spotting files an earlier run already got."""
    folder.mkdir(parents=True, exist_ok=True)

    # Index existing .mp4 files by the "streamer_(title).mp4" part of the name.
    # View counts shift between runs, so the same clip can get a different rank
    # number; matching on the part after the number makes resume reliable.
    existing_by_tail = {}
    for path in folder.glob("*.mp4"):
        try:
            if path.stat().st_size == 0:
                continue  # empty leftover from an interrupted run
        except OSError:
            continue
        existing_by_tail.setdefault(name_tail(path.name), path)
    # Note: each existing file is claimed by at most one job (see pop below), so
    # two clips that sanitize to the same name cannot both count as "already have".

    jobs = []
    for index, clip in enumerate(clips, start_index):
        streamer = sanitize(clip.get("broadcaster_name") or "unknown", 40)
        prefix = "%03d_%s_(" % (index, streamer)
        title = trim_for_path(folder, prefix, sanitize(clip.get("title") or "clip"),
                              ").mp4")
        filename = prefix + title + ").mp4"
        tail = name_tail(filename)
        jobs.append(DownloadJob(clip, folder / filename, existing_by_tail.pop(tail, None),
                                game_name, index))
    return jobs


_pace_lock = threading.Lock()


def polite_pause():
    """Stagger download starts by 1-2 seconds so we never burst the CDN."""
    # Holding the lock during the sleep spaces the starts out even though three
    # downloads run at once.
    with _pace_lock:
        STOP.wait(random.uniform(*POLITE_DELAY))


def explain_failure(error_text, logger):
    """Boil a yt-dlp error down to one readable line."""
    text = (str(error_text) or logger.last_error or "unknown error").strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"^ERROR:\s*", "", text)
    lowered = text.lower()
    if "subscriber" in lowered or "sub-only" in lowered or "subscribers only" in lowered:
        return "subscriber-only clip"
    if "not available in your" in lowered or ("geo" in lowered and "block" in lowered):
        return "region locked"
    if ("404" in lowered or "does not exist" in lowered or "unavailable" in lowered
            or "no longer available" in lowered):
        return "clip deleted or unavailable"
    if "timed out" in lowered or "timeout" in lowered:
        return "network timeout"
    return text[:160]


def download_one(job, total, counter, manifest, max_height=MAX_HEIGHT):
    """Download a single clip. Never raises: a failure is recorded and skipped."""
    if STOP.is_set():
        manifest.record(job, "cancelled")
        return
    polite_pause()
    if STOP.is_set():
        manifest.record(job, "cancelled")
        return

    position = counter.bump()
    say("[%d/%d] Downloading: %s" % (position, total, job.path.name))

    logger = _QuietLogger()
    options = {
        "outtmpl": str(job.path),      # a literal path, no yt-dlp template fields
        "format": ydl_format(max_height),
        # Resolution first, then frame rate: with no cap this is what makes a
        # 4K rendition win over a 1080p60 one.
        "format_sort": ["res", "fps"],
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 30,
        "overwrites": True,            # replace a half-written file from before
        "consoletitle": False,
        "logger": logger,
    }

    height = 0
    try:
        with YoutubeDL(options) as ydl:
            # extract_info downloads exactly like download() but hands back what
            # it actually fetched, so the run can report the real resolution
            # instead of the one we asked for.
            info = ydl.extract_info(job.url, download=True) or {}
            height = int(info.get("height") or 0)
    except DownloadError as exc:
        reason = explain_failure(exc, logger)
        manifest.record(job, "failed", reason=reason)
        say("[%d/%d] SKIPPED (%s): %s" % (position, total, reason, job.path.name))
        return
    except KeyboardInterrupt:
        STOP.set()
        manifest.record(job, "cancelled")
        return
    except Exception as exc:  # anything unexpected must not kill the whole run
        reason = explain_failure(exc, logger)
        manifest.record(job, "failed", reason=reason)
        say("[%d/%d] SKIPPED (%s): %s" % (position, total, reason, job.path.name))
        return

    # yt-dlp can report success yet leave nothing useful behind; verify.
    try:
        size_bytes = job.path.stat().st_size
    except OSError:
        size_bytes = 0
    if size_bytes <= 0:
        manifest.record(job, "failed", reason="no file was written")
        say("[%d/%d] SKIPPED (no file written): %s" % (position, total, job.path.name))
        return

    manifest.record(job, "downloaded", size_bytes=size_bytes, height=height)
    say("[%d/%d] Done (%s, %s): %s"
        % (position, total, describe_height(height), human_size(size_bytes),
           job.path.name))


def renumber_existing(jobs):
    """Rename files from an earlier run so the numbers still match the ranking.

    View counts move between runs, so the clip that was 007 last week can be
    003 today. Without this the folder slowly stops being in order.
    """
    moving = [job for job in jobs if job.existing and job.existing != job.path]
    if not moving:
        return 0

    # Two clips can want to swap numbers, so nothing is renamed straight onto
    # its target: everything goes to a temporary name first, then into place.
    staged = []
    for job in moving:
        temp = job.existing.with_name(job.existing.name + ".renaming")
        try:
            job.existing.rename(temp)
        except OSError:
            continue  # file locked or gone; it keeps its old name, no harm done
        staged.append((job, temp))

    renamed = 0
    for job, temp in staged:
        try:
            temp.rename(job.path)
        except OSError:
            # Something unrelated already sits on that name - put ours back.
            try:
                temp.rename(job.existing)
            except OSError:
                pass
            continue
        job.existing = job.path
        renamed += 1
    return renamed


def run_downloads(jobs, manifest, max_height=MAX_HEIGHT):
    """Record the files we already have, then fetch the rest, 3 at a time."""
    total = len(jobs)
    counter = Counter()

    renamed = renumber_existing(jobs)
    if renamed:
        say("Renumbered %d file(s) from an earlier run to match today's ranking."
            % renamed)

    already_have = [job for job in jobs if job.existing]
    pending = [job for job in jobs if not job.existing]

    for job in already_have:
        position = counter.bump()
        try:
            size_bytes = job.existing.stat().st_size
        except OSError:
            size_bytes = 0
        manifest.record(job, "skipped-exists", reason="file already on disk",
                        size_bytes=size_bytes)
        say("[%d/%d] Already have it: %s" % (position, total, job.existing.name))

    if not pending:
        manifest.flush()
        return

    say("")
    say("Downloading %d clip(s), %d at a time..." % (len(pending), MAX_CONCURRENT_DOWNLOADS))
    with thread_pool(MAX_CONCURRENT_DOWNLOADS) as pool:
        futures = [pool.submit(download_one, job, total, counter, manifest, max_height)
                   for job in pending]
        try:
            for future in as_completed(futures):
                future.result()  # download_one swallows its own errors
        except KeyboardInterrupt:
            STOP.set()
            say("")
            say("Ctrl+C - finishing the downloads already in flight, please wait...")
            for future in futures:
                future.cancel()
    manifest.flush()
