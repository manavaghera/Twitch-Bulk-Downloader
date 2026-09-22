"""Upcoming games on Steam wishlists: what is coming, when, and how big it could be.

Steam never publishes how many people wishlisted a game. What it does publish
is its "Most wishlisted upcoming" chart - the top 100 unreleased games, in
order - and that order is the most direct public read on wishlists there is.
So each game is measured on:

  WISHLIST RANK   its place on that chart today, and - from the snapshots this
                  file saves on every run - its place 7 or 30 days ago. The
                  first runs have no past to compare with; the history fills
                  in as research is run over the following days and weeks.
  RELEASE         Steam's own release date, as exact as the store page gives
                  it: a day, a month, a quarter, a year, or "to be announced".
  ATTENTION       Wikipedia page views (English, German, French) over the last
                  7 or 30 days against the 7 or 30 before. Wikipedia keeps this
                  history itself, so it works from the very first run.
  HYPE            IGDB "hypes": people following the game before release.

The boom verdict is a rule of thumb built from those signals, not a forecast
anyone can guarantee: a top-10 wishlist game that is climbing and drawing more
readers every week is far more likely to land big than one sliding down.
"""

import json
import math
import re
from datetime import datetime, timedelta, timezone
from html import unescape

from . import igdb as igdb_source
from .config import DATA_DIR
from .trend_gather import link_wikipedia
from .util import chunked, load_json, save_json, say
from .web import WIKI_LANGUAGES, WebClient, wiki_title_from_url, wiki_views

HISTORY_FILE = DATA_DIR / "wishlist_history.json"   # one rank snapshot per day
TAGS_FILE = DATA_DIR / "steam_tags.json"            # Steam tag id -> name
STORE_ASSETS = "https://shared.fastly.steamstatic.com/store_item_assets/"

CHART_DEPTH = 100           # Steam's chart holds the top 100
WINDOWS = (7, 30)           # the two comparisons offered on the page
HISTORY_DAYS = 120          # snapshots older than this are dropped
# Views in the window before a Wikipedia change counts; 30 to 60 views is noise.
MIN_WIKI_VIEWS = {7: 700, 30: 3000}
SINGLE_SIGNAL_DISCOUNT = 0.6    # movement only one signal can see counts for less

# Boom verdicts, strongest first: (key, label).
VERDICTS = {"boom": "🔥 Boom likely", "heating": "🚀 Heating up", "strong": "📈 Strong",
            "cooling": "🧊 Losing steam", "watch": "👀 Watch"}


class UpcomingGame:
    """One game on the wishlist chart, with every signal measured about it."""

    def __init__(self, appid, name, rank):
        self.appid, self.name, self.rank = appid, name, rank
        self.release = None             # datetime (UTC) or None
        self.precision = "tba"          # full / month / quarter / year / soon / tba
        self.publisher = ""
        self.tags = []
        self.art = ""                   # Steam header image
        self.igdb_id = None
        self.hypes = 0
        self.wiki_titles = {}
        self.wiki = {}                  # days -> (recent views, views before)
        self.rank_then = {}             # days -> (rank then, 0 = not on chart; age in days)
        self.hype_score = 0.0           # 0..1, how big it looks, whatever the window

    @property
    def store_url(self):
        return "https://store.steampowered.com/app/%s/" % self.appid

    # -- release --------------------------------------------------------------
    @property
    def days_until(self):
        """Days to release, only when Steam gives an exact day."""
        if self.release is None or self.precision != "full":
            return None
        return (self.release.date() - datetime.now(timezone.utc).date()).days

    def release_text(self):
        when = self.release
        if when is None:
            return "Coming soon" if self.precision == "soon" else "TBA"
        if self.precision == "full":
            return when.strftime("%d %b %Y").lstrip("0")
        if self.precision == "month":
            return when.strftime("%b %Y")
        if self.precision == "quarter":
            return "Q%d %d" % ((when.month - 1) // 3 + 1, when.year)
        return str(when.year)

    def release_bucket(self):
        """'30' within a month, '90' within three, 'later', or 'tba'."""
        days = self.days_until
        if days is None:
            return "tba" if self.release is None else "later"
        return "30" if days <= 30 else "90" if days <= 90 else "later"

    # -- movement -------------------------------------------------------------
    def rank_change(self, days):
        """Places climbed over the window: +5 = up five, inf = new on the chart."""
        if days not in self.rank_then:
            return None
        then, _age = self.rank_then[days]
        return float("inf") if not then else then - self.rank

    def wiki_views(self, days):
        return self.wiki.get(days, (0, 0))[0]

    def wiki_growth(self, days):
        recent, before = self.wiki.get(days, (0, 0))
        if recent < MIN_WIKI_VIEWS.get(days, 700):
            return None
        if before <= 0:
            return float("inf")
        return (recent - before) / float(before)

    def momentum(self, days):
        """-1..1 averaged over the signals that can see this window, or None."""
        parts = []
        growth = self.wiki_growth(days)
        if growth is not None:
            parts.append(1.0 if growth == float("inf") else
                         max(-1.0, min(1.0, growth / 1.5 if growth > 0 else growth * 2)))
        change = self.rank_change(days)
        if change is not None:
            parts.append(0.8 if change == float("inf") else max(-1.0, min(1.0, change / 15.0)))
        if not parts:
            return None
        # One signal alone - usually Wikipedia, before any rank history exists -
        # is easily fooled: views always sag in the weeks after a trailer.
        return sum(parts) / len(parts) * (SINGLE_SIGNAL_DISCOUNT if len(parts) == 1 else 1)

    def score(self, days):
        """0..100: size of the hype, nudged by which way it is moving."""
        move = self.momentum(days)
        blended = self.hype_score * 0.8 + 0.2 * ((move if move is not None else 0) + 1) / 2
        return round(100 * blended)

    def verdict(self, days):
        move = self.momentum(days)
        if self.hype_score >= 0.7 and (move is None or move >= -0.1):
            return "boom"
        if move is not None and move >= 0.25 and self.hype_score >= 0.35:
            return "heating"
        if move is not None and move <= -0.25:
            return "cooling"
        return "strong" if self.hype_score >= 0.5 else "watch"

    def why(self, days):
        reasons = ["#%d most wishlisted" % self.rank]
        change = self.rank_change(days)
        if change == float("inf"):
            reasons.append("new on the chart")
        elif change:
            reasons.append("%s %d places" % ("up" if change > 0 else "down", abs(change)))
        growth = self.wiki_growth(days)
        if growth == float("inf"):
            reasons.append("Wikipedia article is new")
        elif growth is not None:
            reasons.append("Wikipedia views %+.0f%%" % (growth * 100))
        if self.hypes:
            reasons.append("%d IGDB hypes" % self.hypes)
        if self.days_until is not None and self.days_until >= 0:
            reasons.append("out in %d days" % self.days_until if self.days_until else "out today")
        return ", ".join(reasons)


# ---------------------------------------------------------------------------
# Steam
# ---------------------------------------------------------------------------
_APP_ID = re.compile(r"/apps/(\d+)/")


def steam_wishlist_chart(web, depth=CHART_DEPTH):
    """Steam's "most wishlisted upcoming" games, in order: [(appid, name)]."""
    payload = web.get_json("https://store.steampowered.com/search/results/", {
        "filter": "popularwishlist", "cc": "US", "l": "english", "json": 1,
        "count": depth, "category1": 998,       # 998 = games
    })
    rows = []
    for item in (payload or {}).get("items") or []:
        match = _APP_ID.search(item.get("logo") or "")
        if match:                               # bundles and packages have no app id
            # The chart sends names HTML-escaped: "Mice &amp; Magic".
            rows.append((match.group(1), unescape(item.get("name") or "").strip()))
    return rows


def steam_store_items(web, appids):
    """{appid: store item} with release, publisher, tags and artwork, 50 per request."""
    items = {}
    for batch in chunked(list(appids), 50):
        request = {"ids": [{"appid": int(appid)} for appid in batch],
                   "context": {"language": "english", "country_code": "US"},
                   "data_request": {"include_release": True, "include_basic_info": True,
                                    "include_tag_count": 4, "include_assets": True}}
        payload = web.get_json("https://api.steampowered.com/IStoreBrowseService/GetItems/v1/",
                               {"input_json": json.dumps(request)})
        for item in ((payload or {}).get("response") or {}).get("store_items") or []:
            if item.get("appid"):
                items[str(item["appid"])] = item
    return items


def steam_tag_names(web):
    """{tag id: name}, cached on disk because Steam's tag list hardly changes."""
    cache = load_json(TAGS_FILE, {})
    if isinstance(cache, dict) and cache.get("tags"):
        checked = cache.get("checked", "")
        if checked >= (datetime.now(timezone.utc).date() - timedelta(days=30)).isoformat():
            return cache["tags"]
    payload = web.get_json("https://api.steampowered.com/IStoreService/GetTagList/v1/",
                           {"language": "english"})
    tags = {str(t["tagid"]): t.get("name") or "" for t in
            ((payload or {}).get("response") or {}).get("tags") or [] if t.get("tagid")}
    if tags:
        save_json(TAGS_FILE, {"checked": datetime.now(timezone.utc).date().isoformat(),
                              "tags": tags})
        return tags
    return (cache or {}).get("tags") or {} if isinstance(cache, dict) else {}


PRECISION = {"date_full": "full", "date_month": "month", "date_quarter": "quarter",
             "date_year": "year", "text_comingsoon": "soon", "text_tba": "tba"}


def apply_store_item(game, item, tag_names):
    release = item.get("release") or {}
    game.precision = PRECISION.get(release.get("coming_soon_display"), "tba")
    stamp = release.get("steam_release_date")
    if stamp and game.precision in ("full", "month", "quarter", "year"):
        game.release = datetime.fromtimestamp(int(stamp), timezone.utc)
    publishers = (item.get("basic_info") or {}).get("publishers") or []
    game.publisher = ", ".join(p.get("name") or "" for p in publishers[:2])
    game.tags = [tag_names.get(str(t.get("tagid")), "") for t in item.get("tags") or []]
    game.tags = [t for t in game.tags if t]
    assets = item.get("assets") or {}
    if assets.get("asset_url_format") and assets.get("header"):
        game.art = STORE_ASSETS + assets["asset_url_format"].replace("${FILENAME}",
                                                                     assets["header"])


# ---------------------------------------------------------------------------
# History: one snapshot of the chart per day
# ---------------------------------------------------------------------------
def _load_history():
    history = load_json(HISTORY_FILE, {})
    snaps = history.get("snapshots") if isinstance(history, dict) else None
    return [s for s in snaps or [] if isinstance(s, dict) and s.get("taken_at")]


def compare_with_history(games, snapshots, now):
    """Fill rank_then for each window from the snapshot nearest that many days ago.

    A snapshot only counts for a window if it is at least half that old, so a
    run from yesterday is not passed off as "30 days ago".
    """
    dated = []
    for snap in snapshots:
        try:
            taken = datetime.fromisoformat(snap["taken_at"])
        except (TypeError, ValueError):
            continue
        dated.append(((now - taken).total_seconds() / 86400.0, snap.get("ranks") or {}))
    for days in WINDOWS:
        usable = [(age, ranks) for age, ranks in dated if age >= days / 2.0]
        if not usable:
            continue
        age, ranks = min(usable, key=lambda pair: abs(pair[0] - days))
        for game in games:
            game.rank_then[days] = (int(ranks.get(game.appid) or 0), round(age))
    return max((age for age, _ in dated), default=0)


def save_snapshot(games, snapshots, now):
    today = now.date().isoformat()
    keep = [s for s in snapshots if not s["taken_at"].startswith(today)
            and s["taken_at"] >= (now - timedelta(days=HISTORY_DAYS)).isoformat()]
    keep.append({"taken_at": now.isoformat(timespec="seconds"),
                 "ranks": {g.appid: g.rank for g in games}})
    save_json(HISTORY_FILE, {"snapshots": keep})


# ---------------------------------------------------------------------------
# The whole scan
# ---------------------------------------------------------------------------
def _log_share(value, biggest):
    return math.log1p(value) / math.log1p(biggest) if value > 0 and biggest > 0 else 0.0


def rate(games):
    """Set each game's hype_score from its rank, IGDB hypes and Wikipedia reach.

    The wishlist rank carries half the weight: it is the one signal every game
    has, and it is Steam's own count of wishlists, only put in order.
    """
    top_hypes = max((g.hypes for g in games), default=0)
    top_wiki = max((g.wiki_views(30) for g in games), default=0)
    depth = max(len(games), 1)
    for game in games:
        parts = [(0.5, 1.0 - math.log(game.rank) / math.log(depth + 1))]
        if top_hypes:
            parts.append((0.25, _log_share(game.hypes, top_hypes)))
        # No article yet is common before release and says nothing about hype,
        # so it is left out rather than scored as zero readers.
        if top_wiki and game.wiki_titles:
            parts.append((0.25, _log_share(game.wiki_views(30), top_wiki)))
        game.hype_score = sum(w * v for w, v in parts) / sum(w for w, _ in parts)


def scan(api, web=None):
    """Read the wishlist chart and measure every game on it.

    Returns {"games": [UpcomingGame, ...], "history_days": age of the oldest
    snapshot in days, "sources": {label: count}}.
    """
    web = web or WebClient()
    sources = {}
    say("")
    say("Steam wishlists - the most wishlisted upcoming games:")
    say("  %-44s" % "Steam most wishlisted chart...", end="")
    chart = steam_wishlist_chart(web)
    sources["Steam wishlist chart"] = len(chart)
    say(" %d" % len(chart) if chart else " nothing came back")
    if not chart:
        return {"games": [], "history_days": 0, "sources": sources}
    games = [UpcomingGame(appid, name, rank) for rank, (appid, name) in enumerate(chart, 1)]
    by_appid = {g.appid: g for g in games}

    say("  %-44s" % "Release dates and details...", end="")
    items = steam_store_items(web, by_appid)
    tag_names = steam_tag_names(web) if items else {}
    for appid, item in items.items():
        if appid in by_appid:
            apply_store_item(by_appid[appid], item, tag_names)
    sources["Steam store details"] = len(items)
    say(" %d" % len(items))

    say("  %-44s" % "IGDB hypes...", end="")
    igdb = igdb_source.IGDBClient(api)
    to_igdb = igdb_source.steam_to_igdb(igdb, list(by_appid))
    details = igdb_source.game_details(igdb, set(to_igdb.values()))
    for appid, gid in to_igdb.items():
        info = details.get(gid)
        if info and appid in by_appid:
            game = by_appid[appid]
            game.igdb_id, game.hypes = gid, info["hypes"]
            for url in info["wiki"]:
                parsed = wiki_title_from_url(url)
                if parsed and parsed[0] in WIKI_LANGUAGES:
                    game.wiki_titles.setdefault(parsed[0], parsed[1])
    sources["IGDB hypes"] = sum(1 for g in games if g.igdb_id)
    say(" %d matched" % sources["IGDB hypes"])

    say("  %-44s" % "Wikipedia views, last 7 and 30 days...", end="")
    link_wikipedia(web, games, upcoming=True)
    articles = {(lang, title) for g in games for lang, title in g.wiki_titles.items()}
    views = wiki_views(web, articles, WINDOWS)
    for game in games:
        for article in game.wiki_titles.items():
            for days, (recent, before) in views.get(article, {}).items():
                was = game.wiki.get(days, (0, 0))
                game.wiki[days] = (was[0] + recent, was[1] + before)
    sources["Wikipedia (upcoming games)"] = len(views)
    say(" %d articles" % len(views))

    now = datetime.now(timezone.utc)
    snapshots = _load_history()
    history_days = compare_with_history(games, snapshots, now)
    save_snapshot(games, snapshots, now)
    rate(games)
    return {"games": games, "history_days": history_days, "sources": sources}
