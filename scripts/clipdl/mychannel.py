"""Your channel's results: which games and streamers do best on YOUR channel.

Reads your uploads and their views through the YouTube Data API (about one
quota unit per 50 videos), then works out the game and streamer of each from
what Clip Studio knows: the Twitch clip link in its description (the title
files put it there), the uploads the upload helper made, or the names in the
title. Saved in data/my_channel.json, refreshed when you ask.
"""

import re
import time
from datetime import datetime, timezone
from statistics import median

import requests

from . import ytauth, yt_quota
from .config import DATA_DIR, HTTP_TIMEOUT, MANIFEST_FILE
from .util import load_json, save_json

CACHE = DATA_DIR / "my_channel.json"
UPLOADS = DATA_DIR / "uploads.json"
API = "https://www.googleapis.com/youtube/v3/"
MAX_VIDEOS = 500
CLIP_LINK = re.compile(r"(?:clips\.twitch\.tv/|twitch\.tv/[A-Za-z0-9_]+/clip/)([A-Za-z0-9_-]+)")


def _get(path, params):
    if not yt_quota.spend_on(API + path):
        raise ValueError("Today's YouTube units are used up - they reset at midnight Pacific.")
    try:
        response = requests.get(API + path, params=params, headers=ytauth.headers(),
                                timeout=HTTP_TIMEOUT)
    except requests.RequestException as error:
        raise ValueError("Could not reach YouTube: %s" % error)
    if response.status_code != 200:
        try:
            message = response.json()["error"]["message"]
        except (ValueError, KeyError, TypeError):
            message = "HTTP %s" % response.status_code
        raise ValueError("YouTube said: %s" % message)
    return response.json()


def _seconds(duration):
    parts = dict((unit, int(n)) for n, unit in re.findall(r"(\d+)([HMS])", duration or ""))
    return parts.get("H", 0) * 3600 + parts.get("M", 0) * 60 + parts.get("S", 0)


def fetch():
    """Read the channel and its uploads from YouTube, attribute them, and save."""
    items = _get("channels", {"part": "snippet,statistics,contentDetails", "mine": "true"})
    items = items.get("items") or []
    if not items:
        raise ValueError("This Google account has no YouTube channel.")
    me = items[0]
    uploads_list = me["contentDetails"]["relatedPlaylists"]["uploads"]
    ids, token = [], None
    while len(ids) < MAX_VIDEOS:
        params = {"part": "contentDetails", "playlistId": uploads_list, "maxResults": 50}
        if token:
            params["pageToken"] = token
        page = _get("playlistItems", params)
        ids += [i["contentDetails"]["videoId"] for i in page.get("items") or []]
        token = page.get("nextPageToken")
        if not token:
            break
    videos = []
    for start in range(0, len(ids), 50):
        page = _get("videos", {"part": "snippet,statistics,contentDetails",
                               "id": ",".join(ids[start:start + 50])})
        for video in page.get("items") or []:
            snippet, stats = video.get("snippet") or {}, video.get("statistics") or {}
            seconds = _seconds((video.get("contentDetails") or {}).get("duration"))
            videos.append({"id": video["id"], "title": snippet.get("title") or "",
                           "description": (snippet.get("description") or "")[:1500],
                           "published": snippet.get("publishedAt") or "",
                           "thumb": ((snippet.get("thumbnails") or {}).get("medium") or {})
                           .get("url") or "",
                           "views": int(stats.get("viewCount") or 0),
                           "likes": int(stats.get("likeCount") or 0),
                           "comments": int(stats.get("commentCount") or 0),
                           "seconds": seconds, "short": 0 < seconds <= 180})
    attribute(videos)
    stats = me.get("statistics") or {}
    data = {"fetched_at": time.time(), "channel": {
        "id": me["id"], "title": (me.get("snippet") or {}).get("title") or "",
        "thumb": (((me.get("snippet") or {}).get("thumbnails") or {}).get("default") or {})
        .get("url") or "", "subscribers": int(stats.get("subscriberCount") or 0),
        "views": int(stats.get("viewCount") or 0), "videos": int(stats.get("videoCount") or 0)},
        "videos": videos}
    save_json(CACHE, data)
    return data


def load():
    data = load_json(CACHE, {})
    return data if isinstance(data, dict) and data.get("channel") else None


# -- which game and streamer -----------------------------------------------------------
def attribute(videos):
    """Fill in "game" and "streamer" of each video ("" when unknown), best evidence first."""
    clips = ((load_json(MANIFEST_FILE, {}) or {}).get("clips") or {}).values()
    by_slug, names, games = {}, set(), set()
    for entry in clips:
        match = CLIP_LINK.search(entry.get("url") or "")
        if match:
            by_slug[match.group(1).lower()] = entry
        if entry.get("streamer"):
            names.add(entry["streamer"])
        if entry.get("game"):
            games.add(entry["game"])
    uploaded = load_json(UPLOADS, {})
    uploaded = uploaded if isinstance(uploaded, dict) else {}
    for video in videos:
        known = uploaded.get(video["id"])
        match = CLIP_LINK.search(video["description"])
        entry = by_slug.get(match.group(1).lower()) if match else None
        if known:
            video["game"], video["streamer"] = known.get("game", ""), known.get("streamer", "")
        elif entry:
            video["game"], video["streamer"] = entry.get("game", ""), entry.get("streamer", "")
        else:
            text = (video["title"] + " " + video["description"][:300]).lower()
            video["streamer"] = next((n for n in sorted(names, key=len, reverse=True)
                                      if len(n) >= 3 and re.search(
                                          r"\b%s\b" % re.escape(n.lower()), text)), "")
            video["game"] = next((g for g in sorted(games, key=len, reverse=True)
                                  if len(g) >= 3 and g.lower() in text), "")


def _age_days(stamp):
    try:
        made = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return 1.0
    return max((datetime.now(timezone.utc) - made).total_seconds() / 86400, 1.0)


def results(data, by="game", shorts_only=True, min_videos=1):
    """[{name, videos, views, median, per_day, best}] - how each game (or streamer)
    does on your channel, best median first."""
    groups = {}
    for video in data["videos"]:
        if shorts_only and not video["short"]:
            continue
        key = video.get(by) or ""
        if key:
            groups.setdefault(key, []).append(video)
    rows = []
    for name, videos in groups.items():
        if len(videos) < min_videos:
            continue
        views = [v["views"] for v in videos]
        best = max(videos, key=lambda v: v["views"])
        rows.append({"name": name, "videos": len(videos), "views": sum(views),
                     "median": median(views),
                     "per_day": sum(v["views"] / _age_days(v["published"]) for v in videos)
                     / len(videos), "best": best["title"], "best_id": best["id"]})
    return sorted(rows, key=lambda r: r["median"], reverse=True)


def unknown_share(data, shorts_only=True):
    videos = [v for v in data["videos"] if v["short"] or not shorts_only]
    return (sum(1 for v in videos if not v.get("game")) / len(videos)) if videos else 0.0


def best_games(count=3, min_videos=2):
    """Game names that do best on your channel - for the Autopilot's "mine" source."""
    data = load()
    if not data:
        return []
    return [r["name"] for r in results(data, "game", True, min_videos)[:count]]
