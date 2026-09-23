"""Collecting the candidate games from every source and merging them into one list.

The order matters. Every source is fetched raw first, then IGDB is asked about
every game any source mentioned, and only then is the list built - so Steam's
"Counter-Strike 2", Twitch's "Counter-Strike" and IGDB's entry end up as one
game with every source's numbers on it, instead of three half-measured ones.
"""

import re
from datetime import datetime, timezone
from difflib import SequenceMatcher

from . import igdb as igdb_source
from . import web as web_source
from .api import TwitchError
from .config import DATA_DIR
from .trends import GameTrend, measure_twitch
from .util import chunked, load_json, save_json, say, thread_pool
from .web import WIKI_LANGUAGES, WebClient, is_game, normalize_name

WIKI_TITLES_FILE = DATA_DIR / "wiki_titles.json"   # game -> {lang: article}
# Bumped whenever the way articles are matched gets stricter, so matches made
# the old way are checked again. 2: plain-name pages must describe a game.
# 3: IGDB's links are checked too - it sometimes links the developer's page.
WIKI_MATCH_VERSION = 3


class Registry:
    """One GameTrend per real game, findable by IGDB id, Steam app id or name."""

    def __init__(self):
        self.games = []
        self._igdb, self._steam, self._names = {}, {}, {}

    def find(self, igdb_id=None, steam=None, name=""):
        return (self._igdb.get(igdb_id) if igdb_id else None) \
            or (self._steam.get(steam) if steam else None) \
            or (self._names.get(normalize_name(name)) if name else None)

    def get(self, igdb_id=None, steam=None, name=""):
        """The existing entry for this game, or a new one."""
        trend = self.find(igdb_id, steam, name)
        if trend is None:
            trend = GameTrend(name)
            self.games.append(trend)
        if igdb_id and not trend.igdb_id:
            trend.igdb_id = igdb_id
        if steam and not trend.steam_appid:
            trend.steam_appid = steam
        if name and not trend.name:
            trend.name = name
        self.index(trend, name)
        return trend

    def index(self, trend, *names):
        if trend.igdb_id:
            self._igdb.setdefault(trend.igdb_id, trend)
        if trend.steam_appid:
            self._steam.setdefault(trend.steam_appid, trend)
        for name in (trend.name,) + names:
            key = normalize_name(name)
            if key:
                self._names.setdefault(key, trend)


def _source(label, fetch, sources):
    """Run one source; a failure costs that source, never the whole run."""
    say("  %-44s" % (label + "..."), end="")
    try:
        result = fetch()
    except (TwitchError, ValueError, KeyError, TypeError) as error:
        result = None
        say(" failed (%s)" % str(error)[:40])
        sources[label] = "failed"
        return None
    count = len(result) if result is not None else 0
    say(" %d" % count if count else " nothing came back")
    sources[label] = count
    return result


def twitch_lookup(api, trends):
    """Give Twitch category ids to games found elsewhere, by IGDB id then name."""
    missing = [t for t in trends if not t.id and t.igdb_id]
    for batch in chunked(missing, 100):
        rows = api.get("/games", {"igdb_id": [str(t.igdb_id) for t in batch]}).get("data") or []
        by_igdb = {str(row.get("igdb_id")): row for row in rows}
        for trend in batch:
            row = by_igdb.get(str(trend.igdb_id))
            if row:
                trend.id = row.get("id") or ""
    missing = [t for t in trends if not t.id and t.name]
    for batch in chunked(missing, 100):
        rows = api.get("/games", {"name": [t.name for t in batch]}).get("data") or []
        by_name = {normalize_name(row.get("name")): row for row in rows}
        for trend in batch:
            row = by_name.get(normalize_name(trend.name))
            if row:
                trend.id = row.get("id") or ""


def gather(api, twitch_count=40, youtube_key=None):
    """Every candidate game, measured on every source. Returns (trends, sources)."""
    web = WebClient()
    igdb = igdb_source.IGDBClient(api)
    sources = {}

    say("")
    say("Reading this week's charts from every source:")
    most_played = _source("Steam most played (worldwide)",
                          lambda: web_source.steam_most_played(web), sources) or []
    charts = {}
    for code, country in web_source.CHART_COUNTRIES:
        charts[code] = _source("Steam top sellers - %s" % country,
                               lambda: web_source.steam_top_sellers(web, code), sources) or []
    visited = _source("IGDB most visited game pages",
                      lambda: igdb_source.most_visited(igdb), sources) or []
    twitch_top = _source("Twitch top categories",
                         lambda: api.top_games_deep(twitch_count), sources) or []
    kick = _source("Kick top categories",
                   lambda: web_source.kick_categories(web), sources) or []

    steam_ids = {row["appid"] for row in most_played}
    steam_ids |= {appid for rows in charts.values() for appid, _ in rows}
    say("  Matching games across sites with IGDB...")
    steam_to_igdb = igdb_source.steam_to_igdb(igdb, steam_ids)
    igdb_ids = set(steam_to_igdb.values()) | {gid for gid, _ in visited}
    igdb_ids |= {int(g["igdb_id"]) for g in twitch_top if str(g.get("igdb_id") or "").isdigit()}
    details = igdb_source.game_details(igdb, igdb_ids)

    # --- build one entry per game ------------------------------------------
    reg = Registry()
    for gid, info in details.items():
        trend = reg.get(igdb_id=gid, steam=info["steam"] or None, name=info["name"])
        if info["released"]:
            trend.released = datetime.fromtimestamp(info["released"], timezone.utc)
        for url in info["wiki"]:
            parsed = web_source.wiki_title_from_url(url)
            if parsed and parsed[0] in WIKI_LANGUAGES:
                trend.wiki_titles.setdefault(parsed[0], parsed[1])

    unnamed = [row["appid"] for row in most_played
               if row["appid"] not in steam_to_igdb and not reg.find(steam=row["appid"])]
    steam_names = web_source.steam_app_names(web, unnamed) if unnamed else {}
    for row in most_played:
        appid = row["appid"]
        extra = steam_names.get(appid)
        if extra is not None and extra.get("type") != "game":
            continue                    # a tool or a video player, not a game
        name = extra["name"] if extra else ""
        trend = reg.get(steam_to_igdb.get(appid), appid, name)
        trend.steam_rank, trend.steam_last_rank = row["rank"], row["last_week_rank"]
        trend.steam_peak = row["peak"]
    for code, rows in charts.items():
        for rank, (appid, name) in enumerate(rows, 1):
            trend = reg.get(steam_to_igdb.get(appid), appid, name)
            trend.chart_ranks.setdefault(code, rank)
    for gid, value in visited:
        if gid in details:
            reg.get(igdb_id=gid).igdb_visits = value
    for game in twitch_top:
        if not is_game(game.get("name")):
            continue
        gid = int(game["igdb_id"]) if str(game.get("igdb_id") or "").isdigit() else None
        trend = reg.get(gid, None, game.get("name") or "")
        trend.id, trend.name = game.get("id") or "", game.get("name") or trend.name
        reg.index(trend)
    for name, viewers in kick:
        if is_game(name):
            existing = reg.find(name=name)
            if existing is not None or viewers >= 1000:
                (existing or reg.get(name=name)).kick_viewers += viewers

    trends = [t for t in reg.games if t.name and is_game(t.name)]
    say("  %d different games found across all sources." % len(trends))

    # --- measure what the lists alone do not say ----------------------------
    try:
        twitch_lookup(api, trends)
    except TwitchError as error:
        say("  Could not match every game to Twitch (%s)." % error)
    measure_wikipedia(web, trends, sources)
    if youtube_key:
        measure_youtube(web, youtube_key, trends, sources)
    measure_all_twitch(api, trends, sources)
    return trends, sources


def measure_wikipedia(web, trends, sources):
    """This week's and last week's page views, English + German + French."""
    say("  Wikipedia page views, this week vs last...", end="")
    link_wikipedia(web, trends)
    articles = {(lang, title) for t in trends for lang, title in t.wiki_titles.items()}
    views = web_source.wiki_weekly_views(web, articles)
    for trend in trends:
        for article in trend.wiki_titles.items():
            recent, previous = views.get(article, (0, 0))
            trend.wiki_recent += recent
            trend.wiki_previous += previous
    sources["Wikipedia page views"] = len(views)
    say(" %d articles" % len(views))


def _title_fits(name, title):
    """Does an article title plausibly name this game? "Chucklefish" for
    Witchbrook, or "Ark: Survival Evolved" for ARK 2, does not."""
    game, article = normalize_name(name), normalize_name(re.sub(r"\s*\([^)]*\)$", "", title))
    if not game or not article:
        return False
    return (game in article or article in game
            or SequenceMatcher(None, game, article).ratio() >= 0.75)


def drop_wrong_links(web, trends, upcoming=False):
    """Forget English articles (and the other languages found through them) that
    are not about the game: wrong name, not a game, or - for a game not out
    yet - a game from years ago, like the 2000 original of a remake."""
    descriptions = web_source.wiki_descriptions(web, [t.wiki_titles["en"] for t in trends])
    too_old = datetime.now(timezone.utc).year - 1
    for trend in trends:
        title = trend.wiki_titles["en"]
        text = descriptions.get(title)
        years = [int(y) for y in re.findall(r"\b(19\d\d|20\d\d)\b", text or "")]
        wrong = (text is None or not _title_fits(trend.name, title)
                 or not web_source.is_game_description(text)
                 or (upcoming and years and max(years) < too_old))
        if wrong:
            trend.wiki_titles = {}


def link_wikipedia(web, trends, upcoming=False):
    """Fill each game's wiki_titles {lang: article}, remembering the answers.

    `upcoming`: the games are not out yet, so an article about a game from
    years ago is the wrong one.
    """
    # Which article belongs to which game hardly ever changes, and Wikipedia
    # rate-limits scripts, so the lookups are remembered between runs. A game
    # with no article is looked up again after a week, in case one appears.
    cache = load_json(WIKI_TITLES_FILE, {})
    cache = cache if isinstance(cache, dict) else {}
    today = datetime.now(timezone.utc).date()
    todo = []
    for trend in trends:
        entry = cache.get(normalize_name(trend.name)) or {}
        checked = entry.get("checked", "")
        fresh = entry.get("v") == WIKI_MATCH_VERSION and (entry.get("titles") or (
            checked and (today - datetime.strptime(checked, "%Y-%m-%d").date()).days < 7))
        if fresh:
            # The cache was checked; whatever IGDB linked this time was not.
            trend.wiki_titles = dict(entry.get("titles") or {})
        else:
            todo.append(trend)

    drop_wrong_links(web, [t for t in todo if "en" in t.wiki_titles], upcoming)
    unlinked = [t for t in todo if "en" not in t.wiki_titles]
    for name, title in web_source.wiki_find_titles(web, [t.name for t in unlinked]).items():
        for trend in unlinked:
            if trend.name == name:
                trend.wiki_titles["en"] = title
    english = [t.wiki_titles["en"] for t in todo if "en" in t.wiki_titles]
    others = web_source.wiki_other_languages(web, english, [l for l in WIKI_LANGUAGES if l != "en"])
    for trend in todo:
        for lang, title in others.get(trend.wiki_titles.get("en"), {}).items():
            trend.wiki_titles.setdefault(lang, title)
        cache[normalize_name(trend.name)] = {"titles": trend.wiki_titles,
                                             "checked": today.isoformat(),
                                             "v": WIKI_MATCH_VERSION}
    if todo:
        save_json(WIKI_TITLES_FILE, cache)
    if upcoming:
        # Checked again every time, cached or not: the same game may have been
        # matched as part of the main list, where an older namesake - the 2000
        # original of a remake - was not ruled out.
        drop_wrong_links(web, [t for t in trends if "en" in t.wiki_titles], upcoming)


def measure_youtube(web, key, trends, sources):
    """Count each game in YouTube's most popular gaming videos, US/UK/DE/FR."""
    say("  YouTube trending gaming videos...", end="")
    texts = [text for _region, text in web_source.youtube_trending_gaming(web, key)]
    for trend in trends:
        trend.youtube_mentions = web_source.mentions(texts, trend.name)
    sources["YouTube trending videos"] = len(texts)
    say(" %d videos" % len(texts) if texts else " nothing came back (check the key)")


def measure_all_twitch(api, trends, sources):
    """Live viewers by language plus clip momentum, for every game Twitch has."""
    on_twitch = [t for t in trends if t.id]
    say("  Twitch live audience + clip momentum for %d games..." % len(on_twitch))

    def one(trend):
        try:
            measure_twitch(api, trend)
        except TwitchError:
            pass            # that game just goes without Twitch numbers
        return trend

    done = 0
    with thread_pool(4) as pool:
        for _trend in pool.map(one, on_twitch):
            done += 1
            if done % 25 == 0 or done == len(on_twitch):
                say("    %d/%d" % (done, len(on_twitch)))
    sources["Twitch games measured"] = len(on_twitch)
