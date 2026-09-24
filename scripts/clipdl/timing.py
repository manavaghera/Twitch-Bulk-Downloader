"""How long things take on this PC: an estimate before a run starts, and the
numbers behind the countdown while it runs.

Every step records how long it took (per clip, per Short, per run), and the
estimate is a running average of the last runs - so it starts from sensible
guesses and gets close to your own PC and connection after a run or two.
Kept in data/timings.json.
"""

import threading

from .config import DATA_DIR
from .util import load_json, save_json

FILE = DATA_DIR / "timings.json"
# Seconds per unit before anything was measured on this PC.
DEFAULTS = {
    "search": 20.0,             # finding and filtering the clips of one download run
    "download": 3.0,            # per clip (three download side by side)
    "short": 5.0,               # per Short (several made side by side)
    "short_captions": 8.0,      # per Short with captions
    "trends": 180.0,            # one trend research run
    "wishlist": 40.0,           # the Steam wishlist scan
    "compilation": 8.0,         # per clip of a compilation
    "trim": 6.0,                # one trimmed Short
    "upload": 25.0,             # per Short uploaded
    "radar_scan": 8.0,          # one clip radar scan
    "youtube_mb": 0.25,         # per MB of a YouTube download (about 4 MB a second)
    "autoclip_clip": 25.0,      # per auto clip, finding the moments included
}
WEIGHT = 0.35                   # how much the latest run moves the average
_lock = threading.Lock()


def per(step):
    """Seconds per unit of `step`: learned on this PC, or the starting guess."""
    data = load_json(FILE, {})
    learned = (data.get(step) or {}).get("per") if isinstance(data, dict) else None
    return float(learned) if learned else DEFAULTS.get(step, 10.0)


def record(step, seconds, units=1):
    """Remember that `units` of `step` took `seconds` in all."""
    if units <= 0 or seconds <= 0:
        return
    value = seconds / float(units)
    with _lock:
        data = load_json(FILE, {})
        data = data if isinstance(data, dict) else {}
        old = data.get(step) or {}
        average = old.get("per")
        data[step] = {"per": round(value if average is None
                                   else average * (1 - WEIGHT) + value * WEIGHT, 3),
                      "runs": int(old.get("runs", 0)) + 1}
        save_json(FILE, data)


def estimate(step, units=1):
    return per(step) * units


def download_run(clips, output="video", captions=False, searches=1):
    """A whole download: finding the clips, fetching them, making the Shorts."""
    seconds = searches * per("search") + clips * per("download")
    if output in ("short", "both"):
        seconds += clips * per("short_captions" if captions else "short")
    return seconds


def autopilot_run(config):
    """One Autopilot run with these settings."""
    if config.get("source") == "radar":
        clips = config.get("radar_clips", 15)
        return per("radar_scan") + download_run(clips, config.get("output"),
                                                config.get("captions"), searches=0)
    games = (len(config.get("game_list") or []) if config.get("source") == "list"
             else config.get("games", 3))
    seconds = download_run(games * config.get("clips_per_game", 5), config.get("output"),
                           config.get("captions"), searches=games)
    if config.get("source") in ("rising", "mine"):
        seconds += per("trends") * 0.3          # recent research is reused when there is one
    return seconds


def text(seconds):
    """"about 40 s", "about 3 min", "about 1 h 10 min"."""
    seconds = max(0, int(round(seconds)))
    if seconds < 60:
        return "about %d s" % max(5, int(round(seconds / 5.0)) * 5)
    minutes = int(round(seconds / 60.0))
    if minutes < 60:
        return "about %d min" % minutes
    return "about %d h %d min" % divmod(minutes, 60)


def clock(seconds):
    """1:05 - for the countdown."""
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    return ("%d:%02d:%02d" % (hours, rest // 60, rest % 60) if hours
            else "%d:%02d" % (rest // 60, rest % 60))
