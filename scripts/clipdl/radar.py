"""Clip radar: the clips blowing up right now, across the top games.

For each of the biggest games it reads Twitch's 100 most-viewed clips of the
window, then ranks every clip by views per hour since it was made - so a clip
from this morning that is catching fire beats yesterday's bigger one. That is
the moment to grab it: before the other clip channels repost it.
"""

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from . import permissions, stats_db
from .api import TwitchAPI, TwitchError
from .config import MANIFEST_FILE, VELOCITY_FLOOR_HOURS
from .util import load_json
from .web import is_game, normalize_name


# Clips that advertise instead of entertain: a web address in the title, or cheats.
SPAM = re.compile(r"(\b[a-z0-9-]+\.(su|ru|com|net|gg|io|xyz|shop|cc|to|me|pro)\b|"
                  r"\bcheats?\b|\baimbot\b|\bwallhack\b|\bspoofer\b|👉)", re.IGNORECASE)


def is_spam(clip):
    return bool(SPAM.search(clip.get("title") or ""))


def top_games(api, count):
    """[{id, name, key}] - the biggest games right now: from the stats collector's
    latest Twitch snapshot when there is one, else from Twitch's own top list."""
    rows = []
    ts = stats_db.latest_ts("twitch")
    if ts:
        rows = stats_db.query(
            "SELECT g.game_key, i.name, i.twitch_id FROM game_samples g JOIN games i ON"
            " i.game_key = g.game_key WHERE g.platform = 'twitch' AND g.ts = ? AND"
            " i.twitch_id IS NOT NULL ORDER BY g.viewers DESC LIMIT ?", (ts, count * 2))
        rows = [{"key": k, "name": n, "id": i} for k, n, i in rows if is_game(n)]
    if len(rows) < count:
        seen = {r["id"] for r in rows}
        for game in api.top_games_deep(count * 2):
            if game.get("id") not in seen and is_game(game.get("name")):
                rows.append({"key": normalize_name(game["name"]), "name": game["name"],
                             "id": game["id"]})
    return rows[:count]


def _clips_of(api, game, started_at, ended_at):
    try:
        items, _cursor = api.get_page("/clips", {"game_id": game["id"], "first": 100,
                                                 "started_at": started_at,
                                                 "ended_at": ended_at})
    except TwitchError:
        return []
    for clip in items:
        clip["game_name"], clip["game_key"] = game["name"], game["key"]
    return items


def scan(client_id, client_secret, games=50, hours=24, workers=6):
    """Every top clip of the top `games` games in the last `hours`, fastest first.

    Each clip dict gets: game_name, game_key, age_hours, per_hour, permission,
    have (already downloaded before)."""
    # Its own client and Cancel switch: a cancelled download must not stop a scan.
    api = TwitchAPI(client_id, client_secret, stop=threading.Event(), log=lambda *a, **k: None)
    picked = top_games(api, games)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    stamp = "%Y-%m-%dT%H:%M:%SZ"
    started_at = (now - timedelta(hours=hours)).strftime(stamp)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        batches = list(pool.map(lambda g: _clips_of(api, g, started_at, now.strftime(stamp)),
                                picked))
    have = set(((load_json(MANIFEST_FILE, {}) or {}).get("clips") or {}))
    perms = permissions.load()
    by_id = {e.get("id"): e.get("status") for e in perms.values() if e.get("id")}
    by_name = {(e.get("name") or "").lower(): e.get("status") for e in perms.values()}
    clips = []
    for clip in (c for batch in batches for c in batch):
        try:
            made = datetime.strptime(clip.get("created_at", ""), stamp).replace(
                tzinfo=timezone.utc)
        except ValueError:
            continue
        age = max((now - made).total_seconds() / 3600, 0.0)
        clip["age_hours"] = age
        clip["per_hour"] = (clip.get("view_count") or 0) / max(age, VELOCITY_FLOOR_HOURS)
        clip["permission"] = by_id.get(clip.get("broadcaster_id")) or \
            by_name.get((clip.get("broadcaster_name") or "").lower())
        clip["have"] = clip.get("id") in have
        clip["spam"] = is_spam(clip)
        clips.append(clip)
    clips.sort(key=lambda c: c["per_hour"], reverse=True)
    return {"clips": clips, "games": len(picked), "scanned_at": time.time(), "hours": hours}


def filtered(result, language="en", min_views=0, hide_have=True, hide_blocked=True,
             hide_spam=True):
    out = []
    for clip in result["clips"]:
        if hide_spam and clip.get("spam"):
            continue
        if language and (clip.get("language") or "").split("-")[0] != language:
            continue
        if (clip.get("view_count") or 0) < min_views:
            continue
        if hide_have and clip["have"]:
            continue
        if hide_blocked and clip["permission"] == "blocked":
            continue
        out.append(clip)
    return out
