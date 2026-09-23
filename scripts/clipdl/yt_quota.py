"""Today's YouTube quota, counted in one place for every program of the project.

A YouTube API key gets 10,000 free units a day, reset at midnight Pacific
time. Every call costs units - a search 100, an upload 1,600, most reads 1 -
and the stats recorder, the Shorts checks, the repost checks and the uploads
all draw on the same pot. So every YouTube call is charged here before it is
made (WebClient does it by itself for the calls it makes), a call that would
overdraw the day is not made at all, and the stats recorder leaves a reserve
for the things you ask for by hand.

The count lives in data/youtube_quota.json. It is our own tally, so it can only
under-count calls made by other apps with the same key; when YouTube itself
says the quota is used up, the day is marked spent.
"""

import threading
from datetime import datetime, timedelta, timezone

from .config import DATA_DIR
from .locks import FileLock
from .util import load_json, save_json

FILE = DATA_DIR / "youtube_quota.json"
DAILY = 10000
# What the stats recorder leaves for Shorts checks, repost checks and an upload.
RESERVE = 2000
COSTS = {"search": 100, "videos.insert": 1600, "thumbnails.set": 50}
_lock = threading.Lock()
_file_lock = FileLock("youtube_quota")

try:
    from zoneinfo import ZoneInfo
    PACIFIC = ZoneInfo("America/Los_Angeles")
except Exception:                   # no time zone data on this PC: standard time is close
    PACIFIC = timezone(timedelta(hours=-8))


def today():
    return datetime.now(PACIFIC).strftime("%Y-%m-%d")


def cost(url, method="GET"):
    """Units a call to this YouTube API address costs."""
    name = url.split("/youtube/v3/", 1)[-1].split("?")[0].strip("/").replace("/", ".")
    if method == "POST" and name == "videos":
        name = "videos.insert"
    return COSTS.get(name, 1)


def _read():
    data = load_json(FILE, {})
    if not isinstance(data, dict) or data.get("day") != today():
        data = {"day": today(), "used": 0, "by": {}}
    return data


def used_today():
    return int(_read().get("used", 0))


def remaining():
    return max(DAILY - used_today(), 0)


def can_spend(units, reserve=0):
    return remaining() - units >= reserve


def spend(units, what="other", reserve=0):
    """Charge `units` if the day has room for them (keeping `reserve` free).
    Returns True when charged - the call may go ahead - else False."""
    with _lock, _file_lock.hold(timeout=10):
        data = _read()
        if DAILY - data["used"] - units < reserve:
            return False
        data["used"] += units
        data["by"][what] = data["by"].get(what, 0) + units
        save_json(FILE, data)
    return True


def spend_on(url, method="GET", reserve=0):
    """spend() for one call to a YouTube API address."""
    name = url.split("/youtube/v3/", 1)[-1].split("?")[0].strip("/") or "other"
    return spend(cost(url, method), name, reserve)


def exhausted():
    """YouTube said the quota is used up: nothing more today."""
    with _lock, _file_lock.hold(timeout=10):
        data = _read()
        data["used"] = max(data["used"], DAILY)
        save_json(FILE, data)


def breakdown():
    """{"search": 400, "videos": 12, ...} for today."""
    return dict(_read().get("by") or {})
