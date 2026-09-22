"""Working out which games are popular in the US and Europe, and which are rising.

Everything is measured over the LAST 7 DAYS against the 7 DAYS BEFORE, so the
answer is about this week, not about which game has been big for years:

  POPULAR   how big a game is this week with a Western audience. Built from
            the Steam best-seller charts in eight US/European countries, Steam
            daily peak players, live viewers on Twitch (English and European
            streams only) and Kick, Wikipedia page views in English, German and
            French, IGDB page visits and - with a key - YouTube's trending
            gaming videos. No single site decides it.

  MOMENTUM  which way it is moving. Wikipedia views this week vs last, Twitch
            clip views this week vs last, the Steam most-played rank vs last
            week's, and how recently the game came out. A game has to be
            climbing on the sources that can see it, on average, to count.

  COOLING   the other half of momentum, and the reason it exists. A game can
            explode for a few weeks and then fade as people get bored. While it
            fades it can still be big, so a size-only list keeps recommending
            it. Here a falling game is labelled "cooling" and kept off the
            rising list however big it still is.

Runs also drop a snapshot into data/trend_history.json, so a run next week can
compare against numbers this script actually measured.
"""

import math
from datetime import datetime, timedelta, timezone

from .config import DATA_DIR, PAGE_SIZE
from .regions import blended_rpm, classify_language
from .util import load_json, save_json
from .web import CHART_COUNTRIES, CHART_DEPTH

HISTORY_FILE = DATA_DIR / "trend_history.json"

# A game needs this many clip views in the recent week before its Twitch growth
# figure is treated as meaningful. Without it, 2 views to 20 reads as +900%.
MIN_VIEWS_FOR_GROWTH = 2000
# The same idea for Wikipedia: weekly page views, all three languages together.
MIN_WIKI_VIEWS = 3000

# A game with less than this in the older week, but real activity now, is new.
NEW_GAME_CEILING = 500

# Released within this many days counts as a new release, and helps momentum
# the most on day one, fading to nothing by the last day.
NEW_RELEASE_DAYS = 45

# Momentum at or above this is rising; at or below the second, cooling.
RISING_AT = 0.15
COOLING_AT = -0.15

# A game must reach this share of the biggest game's popularity to be put on
# the rising list at all, so a game going from 40 page views to 90 is not news.
# (Log-scaled scores sit high: the median game scores about 0.6.)
RISING_MIN_POPULARITY = 0.45

# Momentum seen by just one source is scaled by this.
SINGLE_SOURCE_DISCOUNT = 0.6

# A game that only its release date says is rising - no chart climb, no growth
# measured anywhere yet - must be a best-seller in at least this many of the
# Western countries: a launch that is actually selling, not just a launch.
RELEASE_ONLY_MIN_CHARTS = 3

# How much each popularity signal counts. The Western store charts and the
# Western slice of Twitch are the most direct "US and Europe" readings; weekly
# clip views steady the live count, which swings with the time of day; Steam
# peak players and IGDB visits are worldwide, so they count for less.
WEIGHTS = {"charts": 1.0, "streams": 1.0, "wiki": 1.0, "clips": 0.75,
           "players": 0.75, "youtube": 0.75, "igdb": 0.35}

# Signals every game can be measured on. The others come from top-N lists
# (best-sellers, most played, most visited): being on one is evidence, being
# off it is not - Minecraft is not on Steam at all - so they count only for
# the games they name, and then from LIST_FLOOR up, however low on the list.
UNIVERSAL = ("streams", "clips", "wiki", "youtube")
LIST_FLOOR = 0.35

# How much being seen by fewer sources costs, at most: a game every source
# measured is better evidenced than one only Twitch can see.
BREADTH_PENALTY = 0.3


class GameTrend:
    """Everything measured about one game, from every source."""

    def __init__(self, name=""):
        self.name = name
        self.id = ""                # Twitch category id, if Twitch has it
        self.igdb_id = None
        self.steam_appid = None
        self.released = None        # datetime (UTC), or None
        # Twitch
        self.live_viewers = 0
        self.live_streams = 0
        self.viewers_by_language = {}
        self.recent_views = 0       # Twitch clip views, last 7 days
        self.previous_views = 0     # Twitch clip views, the 7 days before
        self.recent_clips = 0
        # Everything else
        self.kick_viewers = 0
        self.steam_rank = 0         # most-played chart; 0 = not on it
        self.steam_last_rank = 0
        self.steam_peak = 0
        self.chart_ranks = {}       # country code -> Steam best-seller rank
        self.igdb_visits = 0.0
        self.wiki_titles = {}       # lang -> article title
        self.wiki_recent = 0        # page views, last 7 days, all languages
        self.wiki_previous = 0
        self.youtube_mentions = 0
        # Filled in by rank_trends()
        self.popularity = 0.0
        self.momentum = None
        self.rising_score = 0.0
        self.evidence = 0.0         # share of all signal weight that measured it

    # -- audience shares (Twitch) ----------------------------------------------
    def share(self, kind):
        """Fraction of live Twitch viewers watching in a given class of language."""
        total = sum(self.viewers_by_language.values())
        if not total:
            return 0.0
        matched = sum(count for code, count in self.viewers_by_language.items()
                      if classify_language(code) == kind)
        return matched / total

    @property
    def english_share(self):
        return self.share("english")

    @property
    def europe_share(self):
        return self.share("europe")

    @property
    def west_share(self):
        """English plus European: the audience this project is aimed at."""
        return self.english_share + self.europe_share

    @property
    def rpm(self):
        return blended_rpm(self.viewers_by_language)

    # -- release ----------------------------------------------------------------
    @property
    def days_out(self):
        """Days since release, negative if still to come, None if unknown."""
        if not self.released:
            return None
        return (datetime.now(timezone.utc) - self.released).days

    @property
    def is_new_release(self):
        days = self.days_out
        return days is not None and 0 <= days <= NEW_RELEASE_DAYS

    @property
    def is_upcoming(self):
        days = self.days_out
        return days is not None and days < 0

    def released_text(self):
        days = self.days_out
        if days is None:
            return "-"
        if days < 0:
            return "in %dd" % -days
        if days == 0:
            return "today"
        if days <= 60:
            return "%dd ago" % days
        return self.released.strftime("%b %Y")

    # -- growth, one per source ---------------------------------------------
    @property
    def growth(self):
        """Week-over-week change in Twitch clip views, as a fraction. None if unclear."""
        if self.recent_views < MIN_VIEWS_FOR_GROWTH:
            return None
        if self.previous_views <= 0:
            return float("inf")
        return (self.recent_views - self.previous_views) / float(self.previous_views)

    @property
    def is_new(self):
        """Barely clipped on Twitch last week, clipped a lot this week."""
        return (self.previous_views < NEW_GAME_CEILING
                and self.recent_views >= MIN_VIEWS_FOR_GROWTH)

    @property
    def wiki_growth(self):
        if self.wiki_recent < MIN_WIKI_VIEWS:
            return None
        if self.wiki_previous <= 0:
            return float("inf")
        return (self.wiki_recent - self.wiki_previous) / float(self.wiki_previous)

    @property
    def steam_climb(self):
        """Places gained on the most-played chart since last week, or None."""
        if not self.steam_rank:
            return None
        if not self.steam_last_rank:
            return float("inf")         # a new entry on the chart
        return self.steam_last_rank - self.steam_rank

    def momentum_parts(self):
        """{source: -1..1} for each source that can say which way this is moving."""
        parts = {}
        for key, change in (("wiki", self.wiki_growth), ("twitch", self.growth)):
            if change is not None:
                parts[key] = _heat(change)
        climb = self.steam_climb
        if climb is not None:
            parts["steam"] = 1.0 if climb == float("inf") else max(-1.0, min(1.0, climb / 15.0))
        if self.is_new_release:
            parts["release"] = 1.0 - self.days_out / float(NEW_RELEASE_DAYS)
        return parts

    @property
    def verdict(self):
        days = self.days_out
        if days is not None and 0 <= days <= 14:
            return "NEW"
        if self.momentum is None:
            return "quiet"
        if self.momentum >= RISING_AT:
            return "RISING"
        if self.momentum <= COOLING_AT:
            return "cooling"
        return "steady"

    def growth_text(self, change=None, twitch=True):
        """Six characters wide, so report columns stay lined up."""
        if twitch:
            change = self.growth
        if change is None:
            return "   n/a"
        if change == float("inf"):
            return "   new"
        if change > 9.99:
            return " >999%"
        return "%+5.0f%%" % (change * 100)

    def why(self):
        """A short, plain reason list for the rising report."""
        reasons = []
        if self.is_new_release:
            reasons.append("released %s" % self.released_text())
        climb = self.steam_climb
        if climb == float("inf"):
            reasons.append("new on Steam's most-played (#%d)" % self.steam_rank)
        elif climb and climb > 0:
            reasons.append("Steam #%d, up %d places" % (self.steam_rank, climb))
        if self.wiki_growth is not None and self.wiki_growth > 0:
            reasons.append("Wikipedia views %s" % self.growth_text(self.wiki_growth, False).strip())
        if self.growth is not None and self.growth > 0:
            reasons.append("Twitch clip views %s" % self.growth_text().strip())
        if self.chart_ranks:
            reasons.append("top seller in %d/%d countries" % (len(self.chart_ranks),
                                                             len(CHART_COUNTRIES)))
        if self.youtube_mentions:
            reasons.append("%d trending YouTube videos" % self.youtube_mentions)
        return ", ".join(reasons)


def _heat(change):
    """Map a growth fraction onto -1..1: +200% or more is 1, -100% is -1."""
    if change == float("inf"):
        return 1.0
    return max(-1.0, min(1.0, change / 2.0 if change > 0 else change))


def _log_share(value, biggest):
    if value <= 0 or biggest <= 0:
        return 0.0
    return math.log1p(value) / math.log1p(biggest)


def _streams(trend):
    """Live US/EU viewers now: Twitch's Western share, plus Kick at half weight."""
    return trend.live_viewers * trend.west_share + trend.kick_viewers * 0.5


def _clips(trend):
    """Twitch clip views over the last 7 days, Western share."""
    return trend.recent_views * (trend.west_share if trend.viewers_by_language else 1.0)


def popularity_signals(trend, biggest, youtube_enabled=False):
    """{signal: 0..1} for every source that measured this game."""
    signals = {}
    if trend.id or trend.kick_viewers:
        signals["streams"] = _log_share(_streams(trend), biggest["streams"])
    if trend.id:
        signals["clips"] = _log_share(_clips(trend), biggest["clips"])
    if trend.wiki_titles:
        signals["wiki"] = _log_share(trend.wiki_recent, biggest["wiki"])
    if youtube_enabled:
        signals["youtube"] = _log_share(trend.youtube_mentions, biggest["youtube"])
    if trend.steam_peak:
        signals["players"] = _log_share(trend.steam_peak, biggest["players"])
    if trend.chart_ranks:
        strength = sum(1.0 - (rank - 1) / float(CHART_DEPTH)
                       for rank in trend.chart_ranks.values()) / len(CHART_COUNTRIES)
        signals["charts"] = LIST_FLOOR + (1 - LIST_FLOOR) * strength
    if trend.igdb_visits and biggest["igdb"]:
        signals["igdb"] = LIST_FLOOR + (1 - LIST_FLOOR) * math.sqrt(
            trend.igdb_visits / biggest["igdb"])
    return signals


def rank_trends(trends, youtube_enabled=False):
    """Score every game for popularity and momentum. Returns (popular, rising).

    Each signal is scaled against the biggest game on that signal, on a log
    scale, so one giant does not flatten everything else to zero. A source
    that cannot see a game (Steam, for a game not on Steam) is left out of that
    game's average rather than counted as a zero, and top-N lists only ever
    add evidence. Being measured by fewer sources costs a little, since one
    source alone is easier to fool.
    """
    biggest = {
        "players": max([t.steam_peak for t in trends] or [0]),
        "streams": max([_streams(t) for t in trends] or [0]),
        "clips": max([_clips(t) for t in trends] or [0]),
        "wiki": max([t.wiki_recent for t in trends] or [0]),
        "igdb": max([t.igdb_visits for t in trends] or [0]),
        "youtube": max([t.youtube_mentions for t in trends] or [0]),
    }
    universal = sum(WEIGHTS[key] for key in UNIVERSAL if key != "youtube" or youtube_enabled)

    for trend in trends:
        signals = popularity_signals(trend, biggest, youtube_enabled)
        if not signals:
            trend.popularity, trend.momentum = 0.0, None
            trend.evidence = 0.0
            continue
        weight = sum(WEIGHTS[key] for key in signals)
        average = sum(WEIGHTS[key] * value for key, value in signals.items()) / weight
        # Out of what could have measured it: Steam only counts for Steam games.
        possible = universal + WEIGHTS["igdb"] + (
            WEIGHTS["players"] + WEIGHTS["charts"] if trend.steam_appid else 0)
        trend.evidence = min(1.0, weight / possible)
        trend.popularity = average * (1 - BREADTH_PENALTY * (1 - trend.evidence))

        parts = trend.momentum_parts()
        trend.momentum = sum(parts.values()) / len(parts) if parts else None
        # One source alone is easily fooled - a single viral clip, one news
        # story - so a climb nobody else confirms counts for less. A release
        # date alone is not a climb at all, only a reason to expect one.
        if trend.momentum is not None and len(parts) == 1:
            trend.momentum *= SINGLE_SOURCE_DISCOUNT

    top = max([t.popularity for t in trends] or [0]) or 1.0
    for trend in trends:
        trend.popularity /= top
        trend.rising_score = 0.0
        release_only = set(trend.momentum_parts()) == {"release"}
        if release_only and len(trend.chart_ranks) < RELEASE_ONLY_MIN_CHARTS:
            continue
        if (trend.momentum is not None and trend.momentum >= RISING_AT
                and trend.popularity >= RISING_MIN_POPULARITY and not trend.is_upcoming):
            trend.rising_score = trend.momentum * (0.35 + 0.65 * trend.popularity)

    popular = _one_each(sorted((t for t in trends if not t.is_upcoming),
                               key=lambda t: t.popularity, reverse=True))
    rising = _one_each(sorted((t for t in trends if t.rising_score > 0),
                              key=lambda t: t.rising_score, reverse=True))
    return popular, rising


def _one_each(ranked):
    """Drop repeats: two entries on one Wikipedia article are one game listed
    twice (a game and its "Enhanced" re-release, say). The first - highest
    ranked - is kept."""
    seen, kept = set(), []
    for trend in ranked:
        article = trend.wiki_titles.get("en")
        if article and article in seen:
            continue
        seen.add(article)
        kept.append(trend)
    return kept


# ---------------------------------------------------------------------------
# Twitch measurement for one game
# ---------------------------------------------------------------------------
def _window(days_back, days_long=7):
    """RFC3339 start/end for a 7-day window ending `days_back` days ago."""
    end = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=days_back)
    start = end - timedelta(days=days_long)
    stamp = "%Y-%m-%dT%H:%M:%SZ"
    return start.strftime(stamp), end.strftime(stamp)


def _clip_views(api, game_id, started_at, ended_at):
    """Total views of a game's top clips in one window, plus how many there were.

    One page is enough and is the point: it is the same measure for every game
    and both windows, so the comparison between them is fair.
    """
    clips, _cursor = api.get_page("/clips", {
        "game_id": game_id, "first": PAGE_SIZE,
        "started_at": started_at, "ended_at": ended_at,
    })
    return sum(clip.get("view_count") or 0 for clip in clips), len(clips)


def measure_twitch(api, trend, sample=100):
    """Who is watching a game live on Twitch, and whether its clips are growing."""
    for stream in api.live_streams(trend.id, sample):
        viewers = stream.get("viewer_count") or 0
        code = (stream.get("language") or "unknown").lower()
        trend.live_viewers += viewers
        trend.live_streams += 1
        trend.viewers_by_language[code] = trend.viewers_by_language.get(code, 0) + viewers

    recent_start, recent_end = _window(0)
    older_start, older_end = _window(7)
    trend.recent_views, trend.recent_clips = _clip_views(api, trend.id,
                                                         recent_start, recent_end)
    trend.previous_views, _ = _clip_views(api, trend.id, older_start, older_end)
    return trend


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------
def save_snapshot(trends):
    """Append today's numbers, so later runs can compare like with like."""
    history = load_json(HISTORY_FILE, None)
    if not isinstance(history, dict) or not isinstance(history.get("runs"), list):
        history = {"version": 1, "runs": []}

    history["runs"].append({
        "taken_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "games": [{
            "id": t.id, "igdb_id": t.igdb_id, "steam_appid": t.steam_appid,
            "name": t.name, "live_viewers": t.live_viewers,
            "recent_views": t.recent_views, "english_share": round(t.english_share, 4),
            "europe_share": round(t.europe_share, 4), "rpm": round(t.rpm, 2),
            "popularity": round(t.popularity, 4), "wiki_recent": t.wiki_recent,
            "steam_peak": t.steam_peak, "kick_viewers": t.kick_viewers,
        } for t in trends],
    })
    history["runs"] = history["runs"][-52:]   # a year of weekly runs is plenty
    save_json(HISTORY_FILE, history)
    return len(history["runs"])


def snapshot_before(hours=20):
    """The newest saved run at least `hours` old, or None.

    Two runs an hour apart compare nothing but noise; "since yesterday or
    last week" is the comparison worth showing.
    """
    history = load_json(HISTORY_FILE, None)
    runs = history.get("runs") if isinstance(history, dict) else None
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    for run in reversed(runs if isinstance(runs, list) else []):
        try:
            taken = datetime.strptime(run.get("taken_at", ""), "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
        if taken.replace(tzinfo=timezone.utc) <= cutoff:
            return run
    return None


def previous_snapshot(before_latest=True):
    """The run before this one, for real measured week-over-week movement."""
    history = load_json(HISTORY_FILE, None)
    runs = history.get("runs") if isinstance(history, dict) else None
    if not isinstance(runs, list) or len(runs) < (2 if before_latest else 1):
        return None
    return runs[-2] if before_latest else runs[-1]
