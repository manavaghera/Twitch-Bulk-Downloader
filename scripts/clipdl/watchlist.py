"""Streamers you follow for clips: who they are, whether they are live, when they
usually stream, and their best clips this week.

The list lives in data/watchlist.json. Everything else is read from Twitch
when asked for (and cached by the page for a few minutes):

  live now       /streams       game, viewers, title, how long
  usual hours    /videos        their last 40 broadcasts (VODs): start times and
                                lengths, turned into "usually live 18:00-23:00"
  schedule       /schedule      the official schedule, when they keep one
  best clips     /clips         the most viewed clips of the last 7 days
"""

import re
import threading
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

from .api import TwitchAPI, TwitchError
from .config import DATA_DIR
from .util import load_json, save_json

FILE = DATA_DIR / "watchlist.json"
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def client(api):
    """A client with its own Cancel switch, so a cancelled download cannot stop it."""
    return TwitchAPI(api.client_id, api.client_secret, stop=threading.Event(),
                     log=lambda *a, **k: None)


def entries():
    data = load_json(FILE, [])
    return data if isinstance(data, list) else []


def follow(api, name):
    """Add a streamer by channel name. Returns the entry; ValueError if unknown."""
    login = re.sub(r"^(https?://)?(www\.|m\.)?twitch\.tv/", "", (name or "").strip(),
                   flags=re.IGNORECASE).split("?")[0].strip("/@ ").lower()
    if not re.fullmatch(r"[a-z0-9_]{2,25}", login):
        raise ValueError("That does not look like a Twitch channel name.")
    data = client(api).get("/users", {"login": login}).get("data") or []
    if not data:
        raise ValueError("No Twitch channel called '%s'." % login)
    user = data[0]
    entry = {"id": user["id"], "login": user["login"], "name": user["display_name"],
             "avatar": user.get("profile_image_url") or "",
             "about": (user.get("description") or "")[:300], "added": int(time.time())}
    rows = [e for e in entries() if e.get("id") != entry["id"]] + [entry]
    save_json(FILE, rows)
    return entry


def unfollow(user_id):
    save_json(FILE, [e for e in entries() if e.get("id") != user_id])


def live_now(api, ids):
    """{user id: stream} for the ones live right now."""
    if not ids:
        return {}
    data = client(api).get("/streams", {"user_id": list(ids)[:100], "first": 100}).get("data")
    return {s["user_id"]: s for s in data or []}


def last_played(api, ids):
    """{user id: (game, title)} from the channel settings - what they streamed last."""
    if not ids:
        return {}
    data = client(api).get("/channels", {"broadcaster_id": list(ids)[:100]}).get("data") or []
    return {c["broadcaster_id"]: (c.get("game_name") or "", c.get("title") or "") for c in data}


def _duration(text):
    """Seconds from Twitch's "3h8m33s"."""
    parts = dict((unit, int(n)) for n, unit in re.findall(r"(\d+)([hms])", text or ""))
    return parts.get("h", 0) * 3600 + parts.get("m", 0) * 60 + parts.get("s", 0)


def usual_hours(api, user_id):
    """When they usually stream, from their last 40 broadcasts, in this PC's time:
    {"hours": [share of broadcasts live at each hour 0-23], "days": {day: count},
     "start": typical start hour, "length": typical hours, "count": broadcasts}."""
    videos = client(api).get("/videos", {"user_id": user_id, "type": "archive",
                                         "first": 40}).get("data") or []
    local = datetime.now(timezone.utc).astimezone().tzinfo
    hours, days, starts, lengths = [0] * 24, Counter(), [], []
    for video in videos:
        try:
            start = datetime.fromisoformat(video["created_at"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        seconds = _duration(video.get("duration"))
        start = start.astimezone(local)
        starts.append(start.hour)
        lengths.append(seconds / 3600)
        days[DAYS[start.weekday()]] += 1
        for step in range(0, max(seconds, 1), 3600):
            hours[(start + timedelta(seconds=step)).hour] += 1
    count = len(starts)
    return {"hours": [h / count for h in hours] if count else hours, "days": dict(days),
            "start": Counter(starts).most_common(1)[0][0] if starts else None,
            "length": sorted(lengths)[len(lengths) // 2] if lengths else None, "count": count}


def schedule(api, user_id, limit=5):
    """The next streams on their official Twitch schedule: [(start, title, game)]."""
    try:
        data = client(api).get("/schedule", {"broadcaster_id": user_id, "first": limit})
    except TwitchError:                     # 404 when they keep no schedule
        return []
    local = datetime.now(timezone.utc).astimezone().tzinfo
    out = []
    for segment in ((data.get("data") or {}).get("segments") or [])[:limit]:
        try:
            start = datetime.fromisoformat(segment["start_time"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        out.append((start.astimezone(local), segment.get("title") or "",
                    (segment.get("category") or {}).get("name") or ""))
    return out


def best_clips(api, user_id, days=7, first=20):
    """Their most viewed clips of the last `days` days, with game names filled in."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    stamp = "%Y-%m-%dT%H:%M:%SZ"
    api = client(api)
    clips = api.get("/clips", {"broadcaster_id": user_id, "first": first,
                               "started_at": (now - timedelta(days=days)).strftime(stamp),
                               "ended_at": now.strftime(stamp)}).get("data") or []
    game_ids = sorted({c.get("game_id") for c in clips if c.get("game_id")})
    names = {}
    if game_ids:
        names = {g["id"]: g["name"] for g in
                 api.get("/games", {"id": game_ids[:100]}).get("data") or []}
    for clip in clips:
        clip["game_name"] = names.get(clip.get("game_id"), "")
    return sorted(clips, key=lambda c: c.get("view_count") or 0, reverse=True)


def usual_text(pattern):
    """"usually live around 19:00 for ~5h, mostly Tue, Thu, Sat" - or ""."""
    if not pattern or not pattern["count"]:
        return ""
    days = [d for d, _n in Counter(pattern["days"]).most_common(3)]
    return "usually starts around %02d:00, ~%.0fh long, mostly %s (last %d streams)" % (
        pattern["start"], pattern["length"] or 0, ", ".join(days), pattern["count"])
