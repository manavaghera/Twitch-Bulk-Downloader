"""Minimum quality: keep only clips Twitch has at 1080p, 1440p or 4K.

Twitch's clip list does not say how sharp a clip is - a clip is kept at the
resolution the stream was broadcast in, so most are 1080p or 720p and few are
1440p or 4K. So each candidate is looked up (yt-dlp reads the clip's
renditions, about a fifth of a second each, eight at a time), best clips
first, until enough pass. The answers are kept in data/clip_heights.json: a
clip never changes resolution, so it is never looked up twice.
"""

import threading

from .config import DATA_DIR, STOP
from .util import load_json, save_json, say, thread_pool

FILE = DATA_DIR / "clip_heights.json"
WORKERS = 8
_lock = threading.Lock()


class _Quiet:
    def debug(self, message):
        pass

    warning = error = debug


def clip_height(url):
    """The tallest landscape rendition Twitch has of this clip (0 if unknown)."""
    from yt_dlp import YoutubeDL
    try:
        with YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True,
                        "logger": _Quiet()}) as ydl:
            info = ydl.extract_info(url, download=False) or {}
    except Exception:                       # deleted, sub-only, network: unknown
        return 0
    heights = [f.get("height") or 0 for f in info.get("formats") or []
               if "portrait" not in (f.get("format_id") or "")]
    return max(heights or [0])


def split(clips, min_height, need, label="Checking quality", probe=None):
    """(clips at min_height or better, clips below it) - looked up in order until
    `need` pass; clips never reached are left out of both."""
    probe = probe or clip_height
    known = load_json(FILE, {})
    known = known if isinstance(known, dict) else {}
    good, low, todo = [], [], list(clips)
    checked, total = 0, len(todo)
    while todo and len(good) < need and not STOP.is_set():
        # Small batches: the looking up stops soon after enough have passed, and
        # the progress line (and the countdown) moves every few seconds.
        batch, todo = todo[:WORKERS * 4], todo[WORKERS * 4:]
        fresh = [c for c in batch if c.get("id") not in known]
        if fresh:
            with thread_pool(WORKERS) as pool:
                for clip, height in zip(fresh, pool.map(lambda c: probe(c.get("url")), fresh)):
                    known[clip.get("id")] = height
        for clip in batch:
            checked += 1
            (good if known.get(clip.get("id"), 0) >= min_height else low).append(clip)
        say("[%d/%d] %s: %d at %dp or better so far" % (checked, total, label, len(good),
                                                         min_height))
    with _lock:
        stored = load_json(FILE, {})
        stored = stored if isinstance(stored, dict) else {}
        stored.update(known)
        save_json(FILE, stored)
    return good, low
