"""Where the Shorts audience is: how YouTube Shorts about a game are doing.

Twitch viewers are one audience; Shorts viewers are another. For a game this
reads the most-viewed short videos about it published in the last week and
sums up their views - a direct read on whether Shorts of that game get watched.

It costs quota: a YouTube search is 100 of the 10,000 free daily units, so a
check is ~101 units (about 90 a day at most, and the collector uses some too).
Results are kept for 12 hours so looking again costs nothing, and today's
usage is counted and shown.
"""

import re
import time
from datetime import datetime, timedelta, timezone
from statistics import median

from .config import DATA_DIR
from .util import load_json, save_json
from .web import WebClient

CACHE = DATA_DIR / "shorts_demand.json"
KEEP_SECONDS = 12 * 3600
DAILY_QUOTA = 10000
CHECK_COST = 101
MAX_SHORT_SECONDS = 180             # Shorts can be up to three minutes now


def _seconds(duration):
    """Seconds from YouTube's ISO 8601 "PT1M5S"."""
    parts = dict((unit, int(n)) for n, unit in re.findall(r"(\d+)([HMS])", duration or ""))
    return parts.get("H", 0) * 3600 + parts.get("M", 0) * 60 + parts.get("S", 0)


def units_today():
    data = load_json(CACHE, {})
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")      # quota resets on Pacific time;
    return (data.get("units") or {}).get(day, 0) if isinstance(data, dict) else 0   # UTC is close


def _count_units(data, units):
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    data.setdefault("units", {})
    data["units"] = {day: data["units"].get(day, 0) + units}


def cached(game_name, days=7):
    data = load_json(CACHE, {})
    entry = ((data or {}).get("games") or {}).get("%s|%d" % (game_name.lower(), days))
    if entry and time.time() - entry.get("checked_at", 0) < KEEP_SECONDS:
        return entry
    return None


def check(api_key, game_name, days=7, force=False, web=None):
    """{checked_at, shorts, total_views, median_views, channels, top: [...]} for a game."""
    if not force:
        hit = cached(game_name, days)
        if hit:
            return hit
    web = web or WebClient()
    after = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    found = web.get_json("https://www.googleapis.com/youtube/v3/search", {
        "part": "id", "type": "video", "q": '"%s" shorts' % game_name, "videoDuration": "short",
        "order": "viewCount", "publishedAfter": after, "maxResults": 50, "key": api_key})
    if found is None:
        raise ValueError("YouTube did not answer - check the key, or today's quota may be used up.")
    ids = [(item.get("id") or {}).get("videoId") for item in found.get("items") or []]
    ids = [i for i in ids if i]
    videos = []
    if ids:
        details = web.get_json("https://www.googleapis.com/youtube/v3/videos", {
            "part": "snippet,statistics,contentDetails", "id": ",".join(ids), "key": api_key})
        videos = (details or {}).get("items") or []
    shorts = []
    for video in videos:
        if _seconds((video.get("contentDetails") or {}).get("duration")) > MAX_SHORT_SECONDS:
            continue
        snippet, stats = video.get("snippet") or {}, video.get("statistics") or {}
        shorts.append({"id": video.get("id"), "title": snippet.get("title") or "",
                       "channel": snippet.get("channelTitle") or "",
                       "views": int(stats.get("viewCount") or 0),
                       "likes": int(stats.get("likeCount") or 0),
                       "published": snippet.get("publishedAt") or "",
                       "thumb": ((snippet.get("thumbnails") or {}).get("medium") or {}).get("url")
                       or "", "url": "https://www.youtube.com/shorts/%s" % video.get("id")})
    shorts.sort(key=lambda s: s["views"], reverse=True)
    views = [s["views"] for s in shorts]
    result = {"checked_at": time.time(), "game": game_name, "days": days, "shorts": len(shorts),
              "total_views": sum(views), "median_views": median(views) if views else 0,
              "channels": len({s["channel"] for s in shorts}), "top": shorts[:10]}
    data = load_json(CACHE, {})
    data = data if isinstance(data, dict) else {}
    data.setdefault("games", {})["%s|%d" % (game_name.lower(), days)] = result
    _count_units(data, CHECK_COST if ids else 100)
    save_json(CACHE, data)
    return result
