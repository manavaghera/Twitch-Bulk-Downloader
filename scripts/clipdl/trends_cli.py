"""The research tool's menus and its two reports."""

import os

from . import wishlist
from .api import TwitchAPI, TwitchError
from .cli import load_credentials
from .config import CONFIG_FILE, DATA_DIR, STOP
from .regions import (AMBIGUOUS_LANGUAGES, BORDERLINE_LANGUAGES,
                      SHORTS_RPM_HIGH, SHORTS_RPM_LOW, best_rpm_countries,
                      language_name)
from .trend_gather import gather
from .trends import (HISTORY_FILE, MIN_WIKI_VIEWS, previous_snapshot, rank_trends,
                     save_snapshot, snapshot_before)
from .util import ask_int, ask_yes_no, load_json, say
from .web import CHART_COUNTRIES

LINE = "=" * 78


def choose_task():
    """The top menu: what kind of research to run."""
    say("")
    say("What do you want to look into?")
    say("  1) Trending games   - most popular + getting popular in the US and Europe")
    say("  2) RPM by country   - where a thousand views is worth the most")
    say("  3) Both")
    return ask_int("Pick a number [1-3, Enter for 1]: ", 1, 3, default=1)


def youtube_key():
    """The optional YouTube Data API key: YOUTUBE_API_KEY, else data/config.json."""
    key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if not key:
        config = load_json(CONFIG_FILE, None)
        if isinstance(config, dict):
            key = str(config.get("youtube_api_key") or "").strip()
    return key or None


def research(api, candidates=40, with_wishlist=False):
    """One full trend scan, no questions asked. Used by main() and the web page.

    Returns a dict: trends, popular, rising, sources, youtube (bool),
    `previous`, the snapshot from the run before this one (or None), and
    `wishlist`: the Steam wishlist scan (see wishlist.scan), None if it failed,
    False if it was not asked for.
    """
    key = youtube_key()
    trends, sources = gather(api, candidates, key)
    popular, rising = rank_trends(trends, youtube_enabled=bool(key)) if trends else ([], [])
    if popular:
        save_snapshot(popular)
    found = {"trends": trends, "popular": popular, "rising": rising, "sources": sources,
             "youtube": bool(key), "previous": snapshot_before(hours=20),
             "wishlist": None if with_wishlist else False}
    if with_wishlist and not STOP.is_set():
        # Its own try: a Steam hiccup here must not throw away the scan above.
        try:
            found["wishlist"] = wishlist.scan(api)
        except (TwitchError, ValueError, KeyError, TypeError) as error:
            say("  Steam wishlist scan failed (%s)." % str(error)[:60])
        else:
            sources.update(found["wishlist"]["sources"])
    return found


def _num(value):
    return "{:,}".format(int(value)) if value else "-"


# ---------------------------------------------------------------------------
# Report 1: trending games
# ---------------------------------------------------------------------------
def report_sources(sources, youtube_enabled):
    say("")
    say("  WHERE THIS COMES FROM (all measured over the last 7 days vs the 7 before)")
    for label, count in sources.items():
        say("    %-40s %s" % (label, "unavailable this run" if not count or count == "failed"
                              else "%s read" % _num(count)))
    if not youtube_enabled:
        say("    %-40s %s" % ("YouTube trending videos", "skipped - no API key"))
        say("      (Add \"youtube_api_key\": \"...\" to data\\config.json to include it.")
        say("       A free key: console.cloud.google.com -> YouTube Data API v3.)")


def report_popular(popular, top=10):
    say("")
    say(LINE)
    say("  MOST POPULAR THIS WEEK IN THE US AND EUROPE - TOP %d" % top)
    say(LINE)
    say("  Charts = in how many of the %d Steam best-seller charts (US, UK, DE, FR,"
        % len(CHART_COUNTRIES))
    say("  ES, IT, PL, SE) it sits. Live = US/EU Twitch viewers + Kick viewers now.")
    say("")
    say("   #  Game                         Charts  Steam peak     Live   Wiki/wk  Trend")
    say("  " + "-" * 76)
    for index, trend in enumerate(popular[:top], 1):
        charts = "%d/%d" % (len(trend.chart_ranks), len(CHART_COUNTRIES)) \
            if trend.steam_appid else "-"
        live = trend.live_viewers * trend.west_share + trend.kick_viewers
        say("  %2d  %-28s %6s %11s %8s %9s  %s"
            % (index, trend.name[:28], charts, _num(trend.steam_peak), _num(live),
               _num(trend.wiki_recent), trend.verdict))


def report_rising(rising, top=10):
    say("")
    say(LINE)
    say("  GETTING POPULAR - TOP %d NEW OR RISING THIS WEEK" % top)
    say(LINE)
    if not rising:
        say("  Nothing is clearly climbing this week on the sources that can see it.")
        return
    say("  Heat = how hard it is climbing, averaged over every source that can")
    say("  see it (-100 falling fast, +100 climbing fast).")
    say("")
    say("   #  Game                            Released    Heat   Popularity  Trend")
    say("  " + "-" * 76)
    for index, trend in enumerate(rising[:top], 1):
        say("  %2d  %-30s %9s   %+4.0f   %9.0f%%  %s"
            % (index, trend.name[:30], trend.released_text(), trend.momentum * 100,
               trend.popularity * 100, trend.verdict))
        say("      why: %s" % trend.why())


def report_cooling(popular, top=5):
    """Big games whose attention is dropping - the fad that is ending."""
    cooling = [t for t in popular[:30] if t.verdict == "cooling"][:top]
    if not cooling:
        return
    say("")
    say("  COOLING OFF - still big this week, but attention is falling. A game that")
    say("  blew up and is now fading shows here, not on the rising list:")
    for trend in cooling:
        parts = []
        if trend.wiki_growth is not None:
            parts.append("Wikipedia %s" % trend.growth_text(trend.wiki_growth, False).strip())
        if trend.growth is not None:
            parts.append("Twitch clips %s" % trend.growth_text().strip())
        if trend.steam_climb not in (None, float("inf")) and trend.steam_climb < 0:
            parts.append("Steam down %d places" % -trend.steam_climb)
        say("    - %-30s %s" % (trend.name[:30], ", ".join(parts)))


def report_upcoming(trends, top=5):
    # IGDB visits alone let through oddities nobody else has heard of; a real
    # upcoming release also has a busy Wikipedia article or Steam pre-sales.
    upcoming = sorted((t for t in trends if t.is_upcoming and t.igdb_visits
                       and (t.wiki_recent >= MIN_WIKI_VIEWS or t.chart_ranks)),
                      key=lambda t: t.igdb_visits, reverse=True)[:top]
    if not upcoming:
        return
    say("")
    say("  NOT OUT YET, BUT PEOPLE ARE ALREADY LOOKING IT UP (IGDB visits):")
    for trend in upcoming:
        say("    - %-30s releases %s" % (trend.name[:30], trend.released.strftime("%d %b %Y")))


def report_language_caveat(trends, top=10):
    """Say plainly what the region numbers are and are not."""
    say("")
    say("  HOW TO READ THE REGIONS")
    say("  The Steam charts are true per-country charts. Twitch, though, reports")
    say("  the LANGUAGE a stream is in, never where viewers are, so its part of")
    say("  'Live' counts English and European-language streams - the US, UK and")
    say("  Europe together, not a headcount of any one country. Kick gives no")
    say("  language at all, so it is counted at half weight in the score.")

    aside = {}
    for trend in trends[:top]:
        for code, viewers in trend.viewers_by_language.items():
            if code in AMBIGUOUS_LANGUAGES or code in BORDERLINE_LANGUAGES:
                aside[code] = aside.get(code, 0) + viewers
    if aside:
        say("")
        say("  Twitch viewers NOT counted as US/EU, and why:")
        for code, count in sorted(aside.items(), key=lambda kv: -kv[1]):
            say("    %-26s %12s viewers" % (language_name(code), "{:,}".format(count)))
        if any(code in AMBIGUOUS_LANGUAGES for code in aside):
            say("  Twitch uses one code for Portugal and Brazil, and one for Spain")
            say("  and Latin America, and on Twitch both are mostly the Americas.")


def report_history(trends):
    """Compare against what this script measured last time, if it ever has."""
    earlier = previous_snapshot()
    if not earlier:
        say("")
        say("  Snapshot saved to %s. Run this again next week and it will also" % HISTORY_FILE.name)
        say("  compare directly against today's numbers.")
        return

    was = {row.get("name"): row for row in earlier.get("games", [])}
    moved = []
    for trend in trends[:15]:
        before = was.get(trend.name)
        if before and before.get("popularity"):
            change = (trend.popularity - before["popularity"]) / float(before["popularity"])
            # A game that has not really moved is not news.
            if abs(change) >= 0.05:
                moved.append((change, trend.name))
    if not moved:
        return

    moved.sort(reverse=True)
    say("")
    say("  MEASURED AGAINST YOUR LAST RUN (%s), popularity score:"
        % earlier.get("taken_at", "?")[:10])
    for change, name in [row for row in moved if row[0] > 0][:3]:
        say("    up    %-30s %+.0f%%" % (name[:30], change * 100))
    for change, name in [row for row in moved if row[0] < 0][-3:]:
        say("    down  %-30s %+.0f%%" % (name[:30], change * 100))


# ---------------------------------------------------------------------------
# Report 2: RPM by country
# ---------------------------------------------------------------------------
def report_rpm(trends=None, top=15):
    """The country table, then what it means for the games just measured."""
    say("")
    say(LINE)
    say("  RPM BY COUNTRY - LONG-FORM GAMING CONTENT")
    say(LINE)
    say("  RPM is what reaches you per 1,000 views after YouTube's cut and")
    say("  after unmonetised views are counted in. It is not CPM.")
    say("")
    say("   # Country                 USD per 1,000 views   Main language")
    say("  " + "-" * 64)
    for index, (country, code, low, high) in enumerate(best_rpm_countries(top), 1):
        say("  %2d %-24s %5.2f - %5.2f        %s"
            % (index, country, low, high, language_name(code)))

    say("")
    say("  WHERE THESE NUMBERS COME FROM")
    say("  They are published industry estimates for gaming content, typed into")
    say("  scripts/clipdl/regions.py by hand. They are NOT live data, and")
    say("  nothing here can see your actual earnings - only YouTube Analytics")
    say("  can. Use them to rank countries against each other, not as a")
    say("  forecast. Edit that file when you find better figures.")

    say("")
    say("  SHORTS ARE A DIFFERENT GAME")
    say("  Everything above is long-form. Shorts pay from a separate pool, at")
    say("  roughly $%.2f-$%.2f per 1,000 views - one to two hundred times less."
        % (SHORTS_RPM_LOW, SHORTS_RPM_HIGH))
    say("  If these clips are going out as Shorts, judge them on how many")
    say("  viewers they pull to long-form, not on the Shorts revenue itself.")

    if trends:
        report_rpm_for_games(trends)


def report_rpm_for_games(trends, top=10):
    """Turn each game's audience mix into an estimated RPM for that audience."""
    say("")
    say("  WHAT THAT MEANS FOR THE GAMES ABOVE")
    say("  Each game's live audience mix, priced with the table above:")
    say("")
    say("   Game                            Est. RPM   Biggest audience")
    say("  " + "-" * 64)
    # Only Twitch reports an audience's language, so only games it measured
    # can be priced this way.
    measured = [t for t in trends if t.viewers_by_language][:top]
    ranked = sorted(measured, key=lambda t: t.rpm, reverse=True)
    for trend in ranked:
        mix = trend.viewers_by_language
        if mix:
            code, viewers = max(mix.items(), key=lambda kv: kv[1])
            total = sum(mix.values()) or 1
            biggest = "%s (%.0f%%)" % (language_name(code), viewers * 100.0 / total)
        else:
            biggest = "no live streams"
        say("   %-30s  $%5.2f   %s" % (trend.name[:30], trend.rpm, biggest))

    if ranked:
        best, worst = ranked[0], ranked[-1]
        if worst.rpm > 0:
            say("")
            say("  %s's audience is worth about %.1fx %s's per view."
                % (best.name, best.rpm / worst.rpm, worst.name))
            say("  Same effort editing, very different payout - which is the")
            say("  whole reason to look at this before picking a game.")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main(argv, help_text=""):
    """Run one research session."""
    if any(arg in ("-h", "--help", "/?") for arg in argv[1:]):
        print(help_text or __doc__)
        return 0

    say(LINE)
    say("  Game Trend Research  --  what to clip next, and what it is worth")
    say(LINE)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    task = choose_task()

    # The RPM table needs no Twitch account at all, so asking for credentials
    # would be a pointless hurdle for someone who only wants that.
    if task == 2:
        report_rpm()
        say("")
        return 0

    client_id, client_secret = load_credentials()
    api = TwitchAPI(client_id, client_secret)
    api.token()

    say("")
    say("Games come from Steam's charts, IGDB, Kick and Twitch together.")
    candidates = ask_int(
        "How many of Twitch's top categories to add to the pool? [10-100, Enter for 40]: ",
        10, 100, default=40)

    found = research(api, candidates)
    trends, popular = found["trends"], found["popular"]
    if not trends:
        say("")
        say("No source returned any games. Check the connection and try again.")
        return 1

    report_sources(found["sources"], found["youtube"])
    report_popular(popular)
    report_rising(found["rising"])
    report_cooling(popular)
    report_upcoming(trends)
    report_language_caveat(popular)
    report_history(popular)

    if task == 3:
        report_rpm(popular)
    elif ask_yes_no("\nShow what these audiences are worth per 1,000 views?",
                    default=True):
        report_rpm(popular)

    say("")
    return 0
