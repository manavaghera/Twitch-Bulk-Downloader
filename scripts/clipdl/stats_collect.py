"""Recording who is live on Twitch, Kick and YouTube, every cycle, into stats_db.

ONE CYCLE (every INTERVAL_MIN minutes)
  Twitch   the top TWITCH_PAGES x 100 live streams, biggest first - about 95% of
           everyone watching - grouped by game. Then a few games are counted
           in full, every channel: tracked games every cycle, the other big
           games in rotation. The full count also gives the ratio between all
           channels and the channels seen in the broad pass, which corrects the
           channel counts of the games not counted in full this cycle.
  Kick     the same from the stream list kick.com's own pages use (32 a page),
           plus its category totals. Not a documented API: if it changes, the
           cycle goes on without Kick.
  YouTube  with an API key only, once an hour: the most-watched live gaming
           streams, games matched by name in their titles. Search costs 100 of
           the 10,000 free daily quota units, so an hourly pass uses about half.

Run it from the web page (it keeps going while the page's server runs) or on
its own with scripts/collect_stats.py, e.g. from Windows Task Scheduler.
"""

import json
import re
import statistics
import threading
import time

from . import stats_db as db
from . import yt_quota
from .stats_bucket import Bucket, group as _group
from .api import TwitchAPI, TwitchError
from .web import WebClient, normalize_name
from .util import thread_pool

INTERVAL_MIN = 15
TWITCH_PAGES = 100          # x100 streams: reaches channels with ~15 viewers
TWITCH_ROTATE = 5           # big games counted in full per cycle, besides tracked ones
TWITCH_DEEP_PAGES = 60      # up to 6,000 channels per fully counted game
KICK_PAGES = 30             # x32 streams
KICK_ROTATE = 2
KICK_DEEP_PAGES = 25
ROTATE_FROM_TOP = 100       # rotation walks the biggest this many games
MAX_TRACKED = 10
YOUTUBE_EVERY_S = 3600
YOUTUBE_PAGES = 2
YOUTUBE_QUERY = "game|gaming|gameplay|live"
MIN_STORE_VIEWERS = 100     # smaller than this in a cycle is not stored (tracked always is)
LANG_TOP_GAMES = 150        # languages are kept for the biggest games, hourly
LANG_MIN_VIEWERS = 30
KICK_LIST = "https://kick.com/stream/livestreams/all"

# Kick names languages; Twitch uses codes. One vocabulary for both.
KICK_LANGUAGES = {
    "english": "en", "spanish": "es", "portuguese": "pt", "arabic": "ar", "turkish": "tr",
    "russian": "ru", "german": "de", "french": "fr", "italian": "it", "polish": "pl",
    "dutch": "nl", "swedish": "sv", "japanese": "ja", "korean": "ko", "chinese": "zh",
    "hindi": "hi", "indonesian": "id", "vietnamese": "vi", "thai": "th", "greek": "el",
    "czech": "cs", "hungarian": "hu", "romanian": "ro", "ukrainian": "uk", "filipino": "tl",
}


# ---------------------------------------------------------------------------
# Twitch
# ---------------------------------------------------------------------------
def _twitch_pages(api, params, pages):
    cursor = None
    for _ in range(pages):
        page = dict(params, first=100)
        if cursor:
            page["after"] = cursor
        items, cursor = api.get_page("/streams", page)
        yield from items
        if not cursor or not items:
            return


def _twitch_key(stream):
    name = stream.get("game_name") or ""
    if not name:
        return None
    viewers = int(stream.get("viewer_count") or 0)
    row = (stream.get("user_login"), stream.get("user_name"), viewers,
           (stream.get("language") or "").lower(), (stream.get("title") or "")[:140],
           stream.get("started_at"))
    return (normalize_name(name), name, {"twitch_id": stream.get("game_id")}, viewers,
            (stream.get("language") or "other").lower(), row,
            (stream.get("tags") or (), stream.get("title") or ""))


def collect_twitch(api, ts, interval_s, with_langs):
    started = time.time()
    broad = _group(_twitch_pages(api, {}, TWITCH_PAGES), _twitch_key)
    deep = _deep(broad, "twitch", TWITCH_ROTATE, TWITCH_DEEP_PAGES * 100,
                 lambda key, bucket: _group(_twitch_pages(
                     api, {"game_id": bucket.ids.get("twitch_id")}, TWITCH_DEEP_PAGES),
                     _twitch_key).get(key),
                 lambda bucket: bucket.ids.get("twitch_id"))
    return _store("twitch", ts, interval_s, broad, deep, with_langs, started)


# ---------------------------------------------------------------------------
# Kick
# ---------------------------------------------------------------------------
def _kick_pages(web, pages, subcategory=None):
    for page in range(1, pages + 1):
        params = {"page": page, "limit": 32, "sort": "desc"}
        if subcategory:
            params["subcategory"] = subcategory
        payload = web.get_json(KICK_LIST, params)
        data = (payload or {}).get("data") or []
        yield from data
        if not data or not (payload or {}).get("next_page_url"):
            return


def _kick_key(stream):
    category = (stream.get("categories") or [{}])[0] or {}
    name = category.get("name") or ""
    if not name:
        return None
    viewers = int(stream.get("viewer_count") or 0)
    lang = KICK_LANGUAGES.get((stream.get("language") or "").lower(),
                              (stream.get("language") or "other").lower())
    channel = stream.get("channel") or {}
    row = (channel.get("slug"), channel.get("slug"), viewers, lang,
           (stream.get("session_title") or "")[:140], stream.get("start_time"))
    return (normalize_name(name), name, {"kick_slug": category.get("slug")}, viewers, lang, row,
            (stream.get("tags") or (), stream.get("session_title") or ""))


def collect_kick(web, ts, interval_s, with_langs):
    started = time.time()
    broad = _group(_kick_pages(web, KICK_PAGES), _kick_key)
    if not broad:
        raise ValueError("Kick's stream list did not answer")
    deep = _deep(broad, "kick", KICK_ROTATE, KICK_DEEP_PAGES * 32,
                 lambda key, bucket: _group(_kick_pages(web, KICK_DEEP_PAGES,
                                                        bucket.ids.get("kick_slug")),
                                            _kick_key).get(key),
                 lambda bucket: bucket.ids.get("kick_slug"))
    # Kick's category list has every category's full viewer count, including
    # the small streams the broad pass never reaches.
    totals = {}
    for page in range(1, 4):
        payload = web.get_json("https://kick.com/api/v1/subcategories",
                               {"limit": 100, "page": page})
        for item in (payload or {}).get("data") or []:
            if item.get("name"):
                totals[normalize_name(item["name"])] = int(item.get("viewers") or 0)
        if not (payload or {}).get("next_page_url"):
            break
    return _store("kick", ts, interval_s, broad, deep, with_langs, started, totals)


# ---------------------------------------------------------------------------
# Shared: full counts for a few games, then one stored sample
# ---------------------------------------------------------------------------
def _deep(broad, platform, rotate, cap, count_one, has_id):
    """Count every channel of the tracked games plus `rotate` big games.

    A count that reaches `cap` channels stopped early, so it is kept as a
    lower bound (stored as full = 2) and not used to correct other counts.
    """
    tracked = [key for key, _name, _since in db.tracked()][:MAX_TRACKED]
    biggest = sorted(broad, key=lambda k: broad[k].total, reverse=True)[:ROTATE_FROM_TOP]
    counted_at = db.last_full(platform)
    rotation = sorted((k for k in biggest if k not in tracked),
                      key=lambda k: counted_at.get(k, 0))[:rotate]
    # A tracked game too small for the broad pass is found by its saved id.
    info = db.game_info()
    for key in tracked:
        if key not in broad and key in info:
            name, twitch_id, kick_slug = info[key][:3]
            broad[key] = Bucket(name)
            broad[key].ids = {"twitch_id": twitch_id, "kick_slug": kick_slug}
    wanted = [k for k in tracked + rotation if k in broad and has_id(broad[k])]
    found = {}
    with thread_pool(3) as pool:
        for key, bucket in zip(wanted, pool.map(lambda k: _safe(count_one, k, broad[k]),
                                                wanted)):
            if bucket is not None and bucket.viewers:
                bucket.capped = len(bucket.viewers) >= cap
                found[key] = bucket
    return found


def _safe(count_one, key, bucket):
    try:
        return count_one(key, bucket)
    except (TwitchError, ValueError, KeyError, TypeError):
        return None


def _store(platform, ts, interval_s, broad, deep, with_langs, started, totals=None):
    ratios = db.tail_ratios(platform)
    # Every full count is remembered (for the rotation); only complete ones
    # give a ratio to correct other cycles' channel counts with.
    new_ratios = {key: (len(bucket.viewers) / float(len(broad[key].viewers))
                        if broad[key].viewers and not bucket.capped else None)
                  for key, bucket in deep.items()}
    first_time = {key: ratio for key, ratio in new_ratios.items() if ratio and key not in ratios}
    ratios.update({key: ratio for key, ratio in new_ratios.items() if ratio})
    db.save_tail(platform, new_ratios, ts)
    db.backfill_channels(platform, first_time, ts - 3 * 86400)
    tracked = {key for key, _n, _s in db.tracked()}

    games, names, channels, langs, terms = [], {}, {}, [], []
    ranked = sorted(broad, key=lambda k: broad[k].total, reverse=True)
    for rank, key in enumerate(ranked):
        seen = broad[key]
        full = deep.get(key)
        if full:
            source, count, median = full, len(full.viewers), statistics.median(full.viewers)
            is_full = 2 if full.capped else 1
        else:
            source, median, is_full = seen, None, 0
            count = max(len(seen.viewers), round(len(seen.viewers) * ratios.get(key, 1.0)))
        viewers = max(source.total, (totals or {}).get(key, 0))
        if (viewers < MIN_STORE_VIEWERS and key not in tracked) or not source.viewers:
            continue
        games.append((key, viewers, count, len(seen.viewers), source.top_n(1),
                      source.top_n(5), median, is_full, source.hist()))
        names[key] = dict(seen.ids, name=seen.name)
        if rank < 200:
            channels[key] = source.top
            own = set(re.findall(r"[a-z0-9]{3,}", seen.name.lower()))
            terms.extend((key, term, c, v) for term, c, v in source.top_terms(own))
            terms.append((key, "@drops", 0, source.drops))
        if with_langs and rank < LANG_TOP_GAMES:
            langs.extend((key, lang, v, c) for lang, (v, c) in source.langs.items()
                         if v >= LANG_MIN_VIEWERS)
    run = {"streams": sum(len(b.viewers) for b in broad.values()),
           "viewers": sum(b.total for b in broad.values()),
           "games": len(games), "seconds": round(time.time() - started, 1)}
    db.write_cycle(ts, platform, interval_s, run, games, langs, channels, names)
    db.write_terms(platform, terms)
    # The smallest channel the broad pass reached: below it, channels are counted
    # from full counts only, which "Where would I rank?" needs to know.
    smallest = min((min(b.viewers) for b in broad.values() if b.viewers), default=0)
    db.set_meta("cutoff_" + platform, smallest)
    return {"streams": run["streams"], "games": len(games), "full": sorted(deep),
            "seconds": run["seconds"]}


# ---------------------------------------------------------------------------
# YouTube (API key only)
# ---------------------------------------------------------------------------
def collect_youtube(web, key, ts):
    started = time.time()
    need = YOUTUBE_PAGES * (yt_quota.COSTS["search"] + 1)
    if not yt_quota.can_spend(need, web.youtube_reserve):
        return {"paused": "paused - %s of today's %s YouTube units used; %s kept for your own "
                          "checks (resets at midnight Pacific)" % (
                              "{:,}".format(yt_quota.used_today()), "{:,}".format(yt_quota.DAILY),
                              "{:,}".format(yt_quota.RESERVE))}
    ids, token = [], None
    for _ in range(YOUTUBE_PAGES):
        # YouTube's live search returns nothing without a query; this broad OR of
        # words reaches far more live gaming viewers than "gaming" alone.
        params = {"part": "id", "eventType": "live", "type": "video", "videoCategoryId": 20,
                  "order": "viewCount", "maxResults": 50, "q": YOUTUBE_QUERY, "key": key}
        if token:
            params["pageToken"] = token
        payload = web.get_json("https://www.googleapis.com/youtube/v3/search", params)
        if not payload:
            break
        ids += [(item.get("id") or {}).get("videoId") for item in payload.get("items") or []]
        token = payload.get("nextPageToken")
        if not token:
            break
    ids = [i for i in ids if i]
    if not ids:
        raise ValueError("YouTube search returned nothing - check the API key and quota")
    videos = []
    for start in range(0, len(ids), 50):
        payload = web.get_json("https://www.googleapis.com/youtube/v3/videos", {
            "part": "snippet,liveStreamingDetails", "id": ",".join(ids[start:start + 50]),
            "key": key})
        videos += (payload or {}).get("items") or []

    matchers = _youtube_matchers()
    streams = []
    for video in videos:
        snippet = video.get("snippet") or {}
        viewers = int((video.get("liveStreamingDetails") or {}).get("concurrentViewers") or 0)
        text = " ".join([snippet.get("title") or ""] + (snippet.get("tags") or [])).lower()
        match = next(((k, n) for pattern, k, n in matchers if pattern.search(text)), None)
        if match and viewers:
            lang = (snippet.get("defaultAudioLanguage") or "other").split("-")[0].lower()
            row = (video.get("id"), snippet.get("channelTitle"), viewers, lang,
                   (snippet.get("title") or "")[:140],
                   (video.get("liveStreamingDetails") or {}).get("actualStartTime"))
            streams.append((match[0], match[1], {}, viewers, lang, row))
    broad = _group(streams, lambda s: s)
    return _store("youtube", ts, YOUTUBE_EVERY_S, broad, {}, True, started)


# Short names YouTube titles use, per game key (only used when that game is known).
YOUTUBE_ALIASES = {
    "grandtheftauto5": ["gta 5", "gta v", "gta5", "gta rp", "gtarp", "fivem"],
    "counterstrike": ["cs2", "cs 2", "csgo", "cs:go"],
    "leagueoflegends": ["lol"], "worldofwarcraft": ["wow"], "rainbow6siege": ["r6", "r6s"],
    "apexlegends": ["apex"], "callofdutywarzone": ["warzone"], "warzone": ["warzone"],
    "minecraft": ["mc"], "eafc27": ["fc 27", "fc27", "fut"], "rocketleague": ["rl"],
    "deadbydaylight": ["dbd"], "pubg": ["pubg"], "marvelrivals": ["rivals"],
}


def _youtube_matchers():
    """Game names worth looking for in stream titles, longest first, so
    "Minecraft Dungeons" is not counted as "Minecraft"."""
    rows = db.query("SELECT g.game_key, i.name, MAX(g.viewers) FROM game_samples g"
                    " JOIN games i ON i.game_key = g.game_key WHERE g.ts > ?"
                    " GROUP BY g.game_key ORDER BY 3 DESC LIMIT 300",
                    (int(time.time()) - 86400,))
    names = [(key, name, name) for key, name, _v in rows if name and len(name) >= 4]
    known = {key: name for key, name, _v in rows}
    names += [(key, alias, known[key]) for key, aliases in YOUTUBE_ALIASES.items()
              if key in known for alias in aliases]
    names.sort(key=lambda row: len(row[1]), reverse=True)     # longest, most specific first
    return [(re.compile(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(text.lower())), key, name)
            for key, text, name in names]


# ---------------------------------------------------------------------------
# The cycle, and running it in the background
# ---------------------------------------------------------------------------
def run_cycle(client_id, client_secret, youtube_key=None, interval_min=INTERVAL_MIN,
              force=False, log=None):
    """One cycle for every platform. Returns a summary, or None when another
    collector already ran one moments ago."""
    allowed, previous = db.claim_cycle("last_cycle", 0 if force else interval_min * 60 - 60)
    if not allowed:
        return None
    # Each snapshot stands for the time since the one before - measured, not
    # assumed - so watch hours stay right if cycles run early or late. After a
    # long gap (the app was closed) it stands for one interval only: the gap
    # was not seen, and counting it would invent viewers.
    gap = time.time() - previous if previous else interval_min * 60
    interval_s = int(gap) if gap <= interval_min * 60 * 1.5 else interval_min * 60
    quiet = threading.Event()               # never set: a download's Cancel is not ours
    api = TwitchAPI(client_id, client_secret, stop=quiet, log=log or (lambda *a, **k: None))
    # The recorder leaves part of the day's YouTube units for what you do by hand.
    web = WebClient(stop=quiet, youtube_reserve=yt_quota.RESERVE)
    ts = int(time.time()) // 60 * 60
    hour = str(ts // 3600)
    with_langs = db.get_meta("lang_hour") != hour
    summary = {"ts": ts}
    jobs = {"twitch": lambda: collect_twitch(api, ts, interval_s, with_langs),
            "kick": lambda: collect_kick(web, ts, interval_s, with_langs)}
    with thread_pool(2) as pool:     # the two sites side by side
        futures = {name: pool.submit(_attempt, name, job) for name, job in jobs.items()}
    summary.update({name: future.result() for name, future in futures.items()})
    try:
        fill_box_art(api)
    except (TwitchError, ValueError, KeyError, TypeError):
        pass                                # pictures only; next cycle tries again
    last_yt = float(db.get_meta("last_youtube", 0))
    if youtube_key and time.time() - last_yt >= YOUTUBE_EVERY_S * 0.9:
        summary["youtube"] = _attempt("youtube", lambda: collect_youtube(web, youtube_key, ts))
        db.set_meta("last_youtube", time.time())
    if with_langs:
        db.set_meta("lang_hour", hour)
    if time.time() - float(db.get_meta("last_prune", 0)) > 86400:
        db.prune()
        db.set_meta("last_prune", time.time())
    db.set_meta("last_summary", json.dumps(summary))
    from . import alerts
    alerts.run_checks(api)
    return summary


def _attempt(platform, collect):
    try:
        result = collect()
    except Exception as error:              # one platform down must not stop the rest
        result = {"error": str(error)[:200]}
    db.set_meta("status_" + platform, json.dumps(dict(result, at=int(time.time()))))
    if result.get("streams"):
        db.set_meta("ok_" + platform, int(time.time()))
    return result


# Fewer live streams than this in a whole pass means the site answered, but not
# with what it used to - most likely Kick changed its (unofficial) pages.
FEW_STREAMS = {"twitch": 500, "kick": 100, "youtube": 5}


def health(platforms, youtube_on=True):
    """[(platform, problem)] for platforms whose data is not coming in right."""
    problems = []
    for platform in platforms:
        if platform == "youtube" and not youtube_on:
            continue
        status = json.loads(db.get_meta("status_" + platform, "{}") or "{}")
        if not status or status.get("paused"):
            continue
        ok_at = float(db.get_meta("ok_" + platform, 0) or 0)
        since = (" Its numbers stop at %s." % time.strftime("%d %b %H:%M", time.localtime(ok_at))
                 if ok_at else "")
        if status.get("error"):
            problems.append((platform, "the last snapshot failed (%s).%s"
                             % (status["error"][:140], since)))
        elif status.get("streams", 0) < FEW_STREAMS.get(platform, 1):
            problems.append((platform, "the last snapshot found only %d live streams - the "
                             "site may have changed.%s" % (status.get("streams", 0), since)))
    return problems


def fill_box_art(api, batch=1000):
    """Fetch the Twitch category art of games that have none yet - by Twitch id,
    or by name for games seen only on Kick or YouTube. Stored once, reused forever."""
    rows = db.query("SELECT game_key, name, twitch_id FROM games WHERE box_art IS NULL"
                    " ORDER BY last_seen DESC LIMIT ?", (batch,))
    if not rows:
        return
    found = {}
    by_id = [(k, i) for k, _n, i in rows if i]
    for start in range(0, len(by_id), 100):
        chunk = by_id[start:start + 100]
        data = api.get("/games", {"id": [i for _k, i in chunk]}).get("data") or []
        art = {g["id"]: g.get("box_art_url") or "" for g in data}
        found.update({k: (art.get(i, ""), i) for k, i in chunk})
    by_name = [(k, n) for k, n, i in rows if not i and n]
    for start in range(0, len(by_name), 100):
        chunk = by_name[start:start + 100]
        data = api.get("/games", {"name": [n for _k, n in chunk]}).get("data") or []
        art = {normalize_name(g.get("name")): (g.get("box_art_url") or "", g.get("id"))
               for g in data}
        found.update({k: art.get(k, ("", None)) for k, _n in chunk})
    db.set_box_art(found)


class Background:
    """A daemon thread that runs a cycle whenever one is due, while enabled."""

    def __init__(self):
        self.thread = None
        self.wake = threading.Event()
        self.settings = {}                  # client_id, client_secret, youtube_key, enabled
        self.busy = False

    def configure(self, **settings):
        self.settings.update(settings)
        if self.thread is None or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._loop, name="stats-collector",
                                           daemon=True)
            self.thread.start()
        self.wake.set()

    def _loop(self):
        while True:
            self.wake.clear()
            settings = dict(self.settings)
            if settings.get("enabled") and settings.get("client_id"):
                self.busy = True
                try:
                    run_cycle(settings["client_id"], settings["client_secret"],
                              settings.get("youtube_key"))
                except Exception as error:  # keep the thread alive whatever happens
                    db.set_meta("status_collector", json.dumps(
                        {"error": str(error)[:200], "at": int(time.time())}))
                finally:
                    self.busy = False
            self.wake.wait(60)              # re-check every minute; a cycle runs when due


BACKGROUND = Background()


def next_due(interval_min=INTERVAL_MIN):
    last = float(db.get_meta("last_cycle", 0) or 0)
    return last + interval_min * 60 - 60 if last else time.time()
