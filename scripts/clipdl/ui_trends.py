"""The Trend Research page: the same scan as twitch_trends.py, shown as tables."""

import streamlit as st

from . import jobs, ui_theme, ui_wishlist
from .regions import best_rpm_countries, language_name
from .trends import MIN_WIKI_VIEWS
from .trends_cli import research
from .ui_common import box_arts, busy_elsewhere, finished_log, progress_panel, start_job
from .ui_downloader import trend_label
from .web import CHART_COUNTRIES

DOWNLOAD_TAB = "⬇️  Clip downloader"     # must match the tab label in web_app.py

VERDICT_ICONS = {"NEW": "🆕 NEW", "RISING": "🚀 RISING", "steady": "➖ steady",
                 "cooling": "🧊 cooling", "quiet": "· quiet"}


def _charts(trend):
    return "%d/%d" % (len(trend.chart_ranks), len(CHART_COUNTRIES)) if trend.steam_appid else "–"


def _live(trend):
    return int(trend.live_viewers * trend.west_share + trend.kick_viewers)


def _pct(change):
    if change is None:
        return None
    return 999.0 if change == float("inf") else round(change * 100)


def _confidence(trend):
    """How much of the evidence measured this game: most, some, or little of it."""
    share = getattr(trend, "evidence", 0.0)
    return "●●● high" if share >= 0.75 else "●●○ medium" if share >= 0.5 else "●○○ low"


def popular_rows(popular, previous, top):
    before = {row.get("name"): row.get("popularity") for row in (previous or {}).get("games", [])}
    rows = []
    for rank, trend in enumerate(popular[:top], 1):
        was = before.get(trend.name)
        rows.append({
            "#": rank, "Game": trend.name, "Popularity": round(trend.popularity * 100),
            "Trend": VERDICT_ICONS.get(trend.verdict, trend.verdict),
            "Steam charts (US/EU)": _charts(trend), "Steam peak players": trend.steam_peak or None,
            "Live US/EU viewers": _live(trend) or None, "Wikipedia views / wk": trend.wiki_recent or None,
            "vs last run": ("%+.0f%%" % ((trend.popularity - was) / was * 100)) if was else "",
            "Released": trend.released_text(), "Confidence": _confidence(trend),
        })
    return rows


def rising_rows(rising, top):
    return [{
        "#": rank, "Game": trend.name, "Heat": round(trend.momentum * 100),
        "Popularity": round(trend.popularity * 100), "Released": trend.released_text(),
        "Trend": VERDICT_ICONS.get(trend.verdict, trend.verdict), "Why": trend.why(),
    } for rank, trend in enumerate(rising[:top], 1)]


def cooling_rows(popular):
    return [{
        "Game": trend.name, "Popularity": round(trend.popularity * 100),
        "Wikipedia wk/wk %": _pct(trend.wiki_growth), "Twitch clips wk/wk %": _pct(trend.growth),
        "Steam rank change": (trend.steam_climb if trend.steam_climb not in (None, float("inf"))
                              else None),
    } for trend in popular[:30] if trend.verdict == "cooling"]


def _compact(number):
    number = int(number or 0)
    for size, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if number >= size:
            return ("%.1f" % (number / size)).rstrip("0").rstrip(".") + suffix
    return str(number)


def _card(trend, rank, bar, left, right, why, art):
    return {"rank": rank, "name": trend.name, "art": ui_theme.box_art(art, 285, 380),
            "tag": VERDICT_ICONS.get(trend.verdict, trend.verdict),
            "tag_class": trend.verdict.lower(), "bar": bar, "left": left, "right": right,
            "why": why}


def _reach(trend):
    parts = []
    if _live(trend):
        parts.append("%s live US/EU viewers" % _compact(_live(trend)))
    if trend.steam_peak:
        parts.append("%s Steam peak" % _compact(trend.steam_peak))
    if trend.wiki_recent:
        parts.append("%s Wikipedia views/wk" % _compact(trend.wiki_recent))
    return " · ".join(parts)


def _get_clips(label):
    """Open the downloader with this game already picked."""
    st.session_state["dl_game_mode"] = "Suggested"
    st.session_state["dl_pick"] = label
    st.session_state["main_tab"] = DOWNLOAD_TAB


def render_highlights(api, popular, rising):
    arts = box_arts(api, [t.id for t in rising[:5] + popular[:5]])
    if rising:
        st.markdown("#### 🚀 Getting popular")
        ui_theme.game_cards([
            _card(t, rank, max(0, (t.momentum or 0) * 100), "Heat %+d" % round((t.momentum or 0) * 100),
                  "Popularity %d" % round(t.popularity * 100), t.why() or _reach(t),
                  arts.get(t.id)) for rank, t in enumerate(rising[:5], 1)])
    st.markdown("#### 🔥 Most popular")
    ui_theme.game_cards([
        _card(t, rank, t.popularity * 100, "Popularity %d" % round(t.popularity * 100),
              t.released_text() or "", _reach(t), arts.get(t.id))
        for rank, t in enumerate(popular[:5], 1)])

    picks, seen = [], set()
    for trend in rising[:5] + popular[:5]:
        if trend.id and trend.id not in seen:
            seen.add(trend.id)
            picks.append(trend)
    if picks:
        st.caption("Grab clips of one of these")
        shortcut = st.container(horizontal=True)
        for trend in picks:
            shortcut.button(trend.name, key="clips_%s" % trend.id, icon=":material/download:",
                            on_click=_get_clips, args=(trend_label(trend, rising),))


def render_results(api, found, top):
    popular, rising = found["popular"], found["rising"]
    sources = found["sources"]
    ok = sum(1 for count in sources.values() if count and count != "failed")
    cols = st.columns(4)
    cols[0].metric("Games", len(found["trends"]), help="Games compared across every source")
    cols[1].metric("Sources", "%d / %d" % (ok, len(sources)), help="Sources that answered")
    cols[2].metric("Rising", len(rising), help="New or climbing this week")
    cols[3].metric("Cooling", sum(1 for t in popular[:30] if t.verdict == "cooling"),
                   help="Still big, but attention is falling")
    render_highlights(api, popular, rising)
    if found.get("wishlist") is not False:
        st.space("small")
        ui_wishlist.render(found["wishlist"])
    st.space("small")
    st.markdown("#### Full breakdown")

    score = st.column_config.ProgressColumn(min_value=0, max_value=100, format="%d")
    count = st.column_config.NumberColumn(format="localized")
    tabs = st.tabs(["🔥 Most popular", "🚀 Getting popular", "🧊 Cooling off",
                    "⏳ Coming soon", "💰 RPM", "ℹ️ Sources"])
    with tabs[0]:
        previous = found.get("previous")
        st.caption("This week, US and Europe. Popularity is relative to the #1 game (100). "
                   "Confidence: how many of the sources measured the game - Steam cannot see "
                   "a game that is not on Steam, and that is not held against it."
                   + (" 'vs last run' compares with your scan of %s."
                      % previous["taken_at"][:10] if previous else ""))
        st.dataframe(popular_rows(popular, previous, top), hide_index=True, width="stretch",
                     column_config={"Popularity": score, "Steam peak players": count,
                                    "Live US/EU viewers": count, "Wikipedia views / wk": count})
    with tabs[1]:
        if rising:
            st.caption("Heat: how hard it is climbing, averaged over every source that can "
                       "see it (−100 falling fast, +100 climbing fast).")
            st.dataframe(rising_rows(rising, top), hide_index=True, width="stretch",
                         column_config={"Popularity": score,
                                        "Heat": st.column_config.ProgressColumn(
                                            min_value=0, max_value=100, format="%+d"),
                                        "Why": st.column_config.TextColumn(width="large")})
        else:
            st.info("Nothing is clearly climbing this week on the sources that can see it.")
    with tabs[2]:
        st.caption("Still big, but attention is falling - the fad that is ending. These are "
                   "kept off the rising list however big they still are.")
        rows = cooling_rows(popular)
        if rows:
            st.dataframe(rows, hide_index=True, width="stretch",
                         column_config={"Popularity": score})
        else:
            st.info("Nothing in the top 30 is clearly fading this week.")
    with tabs[3]:
        upcoming = sorted((t for t in found["trends"] if t.is_upcoming and t.igdb_visits
                           and (t.wiki_recent >= MIN_WIKI_VIEWS or t.chart_ranks)),
                          key=lambda t: t.igdb_visits, reverse=True)[:10]
        if upcoming:
            st.caption("Not out yet, but people are already looking it up.")
            st.dataframe([{"Game": t.name, "Releases": t.released.strftime("%d %b %Y"),
                           "Wikipedia views / wk": t.wiki_recent or None} for t in upcoming],
                         hide_index=True, width="stretch",
                         column_config={"Wikipedia views / wk": count})
        else:
            st.info("No upcoming game is drawing real attention yet.")
    with tabs[4]:
        render_rpm(popular)
    with tabs[5]:
        st.dataframe([{"Source": label,
                       "Read": "unavailable this run" if not count or count == "failed" else count}
                      for label, count in sources.items()],
                     hide_index=True, width="stretch")
        if not found["youtube"]:
            st.info("YouTube is skipped: no API key. Add `\"youtube_api_key\": \"...\"` to "
                    "data/config.json (or a YOUTUBE_API_KEY secret when hosted). A free key "
                    "comes from console.cloud.google.com → YouTube Data API v3.")
        st.caption("Steam charts are true per-country charts. Twitch reports the language a "
                   "stream is in, not where viewers are, so 'US/EU viewers' means English and "
                   "European-language streams. Kick gives no language and counts at half "
                   "weight in the score.")


def render_rpm(popular):
    st.caption("Published industry estimates for long-form gaming, typed into "
               "scripts/clipdl/regions.py - not live data. Shorts pay roughly 100× less.")
    left, right = st.columns(2)
    left.markdown("**Best-paying countries**")
    left.dataframe([{"Country": country, "USD per 1,000 views": "%.2f – %.2f" % (low, high),
                     "Language": language_name(code)}
                    for country, code, low, high in best_rpm_countries(15)],
                   hide_index=True, width="stretch")
    right.markdown("**What each top game's Twitch audience is worth**")
    measured = sorted((t for t in popular[:10] if t.viewers_by_language),
                      key=lambda t: t.rpm, reverse=True)
    right.dataframe([{"Game": t.name, "Est. RPM ($)": round(t.rpm, 2)} for t in measured],
                    hide_index=True, width="stretch")


def render(api):
    job = jobs.latest("trends")
    other = busy_elsewhere("trends")
    with st.container(border=True, key="card_trend_form"):
        ui_theme.step("📈", "What's hot in the US & Europe",
                      "Last 7 days vs the week before - Steam, IGDB, Wikipedia, Twitch, "
                      "Kick and (optionally) YouTube.")
        cols = st.columns([2.4, 1.3, 1.3], gap="medium", vertical_alignment="bottom")
        candidates = cols[0].slider("Twitch categories in the pool", 10, 100, 40, 10,
                                    help="How many of Twitch's top categories to add to "
                                         "the games the charts already name.")
        with cols[1]:
            top = st.segmented_control("Rows per list", (10, 20, 30), default=10,
                                       required=True, key="trend_top")
            with_wishlist = st.toggle("Steam wishlists", value=True, key="trend_wishlist",
                                      help="Also scan Steam's most wishlisted upcoming games. "
                                           "Adds about half a minute.")
        go = cols[2].button(
            "Run research", type="primary", width="stretch", icon=":material/insights:",
            disabled=api is None or bool(job and job.running) or bool(other))
    if api is None:
        ui_theme.empty_state("🔌", "Connect to Twitch first",
                             "Paste your Twitch app credentials in the sidebar to start.")
        return
    if other:
        st.caption("⏳ Waiting for the running download to finish (%s)." % other)
    if go:
        st.session_state["trend_rows"] = top
        start_job("trends", "Trend research",
                  lambda: research(api, candidates, with_wishlist=with_wishlist))

    if job is None:
        ui_theme.empty_state("📊", "No research yet",
                             "Hit Run research to rank this week's games. It takes about a "
                             "minute - the very first run a few minutes longer while it learns "
                             "which Wikipedia article belongs to which game.")
        return
    if job.running:
        progress_panel("trends")
        return
    if job.error:
        st.error("The research stopped: %s" % job.error, icon=":material/error:")
    elif job.result and job.result["trends"]:
        render_results(api, job.result, st.session_state.get("trend_rows", top))
    elif not job.cancelled:
        st.warning("No source returned any games. Check the connection and try again.")
    finished_log(job)
