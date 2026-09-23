"""Repost check: is this Twitch clip already on YouTube Shorts?

Posting a clip three other channels already posted wastes the upload. This
searches YouTube for the clip's title and streamer among videos published
since the clip was made, and keeps the ones that look like the same moment.

Each check is a YouTube search: 100 of the 10,000 free daily units (counted
in yt_quota). Results are kept for 12 hours.
"""

import re
import time
from difflib import SequenceMatcher

from . import yt_quota
from .config import DATA_DIR
from .util import load_json, save_json
from .web import WebClient

CACHE = DATA_DIR / "repost_cache.json"
KEEP_SECONDS = 12 * 3600
COST = yt_quota.COSTS["search"]


def _words(text):
    return re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower()).split()


def same_moment(clip, video):
    """Does a YouTube video look like a repost of this clip?"""
    clip_title = " ".join(_words(clip.get("title")))
    title = " ".join(_words(video.get("title")))
    streamer = (clip.get("broadcaster_name") or "").lower()
    about = title + " " + " ".join(_words(video.get("description")))
    named = bool(streamer) and (streamer in about or streamer in about.replace(" ", ""))
    if len(clip_title) >= 8 and (clip_title in title or
                                 SequenceMatcher(None, clip_title, title).ratio() >= 0.6):
        return True
    # A short clip title ("lol", "W") says little: then the streamer must be named
    # and most of its words present.
    words = set(_words(clip.get("title")))
    return named and bool(words) and len(words & set(title.split())) >= max(1, len(words) - 1)


def cached(clip_id):
    entry = (load_json(CACHE, {}) or {}).get(clip_id)
    if entry and time.time() - entry.get("checked_at", 0) < KEEP_SECONDS:
        return entry
    return None


def check(api_key, clip, web=None):
    """{"checked_at", "matches": [{title, channel, url, published}]} for one clip."""
    hit = cached(clip.get("id"))
    if hit:
        return hit
    if not yt_quota.can_spend(COST):
        raise ValueError("Today's YouTube units are used up - they reset at midnight Pacific.")
    web = web or WebClient()
    query = '%s %s' % (clip.get("title") or "", clip.get("broadcaster_name") or "")
    found = web.get_json("https://www.googleapis.com/youtube/v3/search", {
        "part": "snippet", "type": "video", "q": query.strip()[:200], "maxResults": 15,
        "videoDuration": "short", "order": "relevance", "key": api_key,
        "publishedAfter": clip.get("created_at") or None})
    if found is None:
        raise ValueError("YouTube did not answer - check the key, or today's quota.")
    matches = []
    for item in found.get("items") or []:
        snippet = item.get("snippet") or {}
        video = {"title": snippet.get("title") or "", "description": snippet.get("description")}
        if same_moment(clip, video):
            matches.append({"title": video["title"], "channel": snippet.get("channelTitle") or "",
                            "published": snippet.get("publishedAt") or "",
                            "url": "https://www.youtube.com/shorts/%s"
                                   % (item.get("id") or {}).get("videoId", "")})
    result = {"checked_at": time.time(), "matches": matches}
    data = load_json(CACHE, {})
    data = {k: v for k, v in (data if isinstance(data, dict) else {}).items()
            if time.time() - v.get("checked_at", 0) < KEEP_SECONDS}
    data[clip.get("id")] = result
    save_json(CACHE, data)
    return result
