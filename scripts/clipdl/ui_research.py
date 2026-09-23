"""The Game Research page: StreamsCharts-style numbers across Twitch, Kick and YouTube,
said in plain words, with each game's icon."""

import os
import time

import pandas as pd
import streamlit as st

from . import research as rs
from . import research_more as rm
from . import (stats_collect, stats_db, ui_charts, ui_research_more, ui_research_tools,
               ui_theme)
from .folders import load_prefs
from .trends_cli import youtube_key

PERIOD_LABELS = {"live": "Live now", "24h": "24 hours", "7d": "7 days", "30d": "30 days"}
PLATFORM_LABELS = {"twitch": "Twitch", "kick": "Kick", "youtube": "YouTube"}
VIEWS = ["🏠 Overview", "🎮 Game page", "🎯 Where would I rank?", "💡 Best games to stream",
         "⚡ Spikes & movers", "🌍 Languages", "⚖️ Compare", "⚙️ Data"]
GAME_PAGE = VIEWS[1]
TOP_SIZES = (10, 25, 50, 100)

GLOSSARY = """
- **Watching / viewers** - people watching at the same moment. *Average viewers* is that,
  averaged over the period; *peak* is the highest moment.
- **Watch hours** - all the time people spent watching (100 viewers for 2 hours = 200).
- **Streamers / channels live** - how many channels were streaming the game at once.
- **Viewers per streamer** - average viewers ÷ channels. Stars pull this up, so also look at…
- **Typical streamer** - the middle channel: half of all channels streaming the game have
  this many viewers or fewer. The honest number for a small streamer.
- **Hours streamed** - channels × time: how much content the game gets.
- **Change** - average viewers compared with the period just before.
"""


def fmt(value, digits=0):
    """1234567 -> 1.2M; a dash for nothing."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "–"
    value = float(value)
    for size, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= size:
            return ("%.1f" % (value / size)).rstrip("0").rstrip(".") + suffix
    return ("%." + str(digits) + "f") % value


# -- cached reads (a minute is fresh enough: cycles are 15 minutes apart) ----------
@st.cache_data(ttl=60, show_spinner=False)
def board(platforms, period, games_only):
    return rs.leaderboard(list(platforms), period, games_only)


@st.cache_data(ttl=60, show_spinner=False)
def coverage(platforms, period):
    return rs.coverage(list(platforms), period)


@st.cache_data(ttl=60, show_spinner=False)
def cached_insights(platforms, period, games_only):
    return rm.insights(list(platforms), period, games_only)


@st.cache_data(ttl=300, show_spinner=False)
def icon_map():
    return rm.icons()


def icon(key, width=84, height=112):
    """A game's Twitch icon at twice the size shown, so it stays sharp."""
    template = icon_map().get(key)
    return ui_theme.box_art(template, width, height) if template else ""


def start_collector(api):
    """Keep the background recorder running while the app runs, if it is on.
    CLIPDL_NO_COLLECTOR=1 keeps it off (tests, or a host running several copies)."""
    if os.environ.get("CLIPDL_NO_COLLECTOR"):
        return False
    enabled = bool(load_prefs().get("stats_collector", True))
    stats_collect.BACKGROUND.configure(client_id=api.client_id, client_secret=api.client_secret,
                                       youtube_key=youtube_key(), enabled=enabled)
    return enabled


def render(api):
    if api is None:
        ui_theme.empty_state("🔌", "Connect to Twitch first",
                             "Paste your Twitch app credentials in the sidebar to start.")
        return
    enabled = start_collector(api)
    if not stats_db.latest_ts("twitch") and not stats_db.latest_ts("kick"):
        first_snapshot(enabled)
        return

    platforms, period, games_only = controls()
    if not platforms:
        st.info("Pick at least one platform.")
        return
    st.session_state.setdefault("gr_view", VIEWS[0])     # set by code too: no default=
    view = st.pills("View", VIEWS, required=True, key="gr_view", label_visibility="collapsed")
    coverage_note(platforms, period, view)
    st.space("small")
    if view == VIEWS[0]:
        overview(platforms, period, games_only)
    elif view == GAME_PAGE:
        game_page(api, platforms, period, games_only)
    elif view == VIEWS[2]:
        ui_research_more.where_would_i_rank(platforms, games_only)
    elif view == VIEWS[3]:
        ui_research_tools.best_games(platforms, period)
    elif view == VIEWS[4]:
        ui_research_tools.spikes_and_movers(platforms)
    elif view == VIEWS[5]:
        ui_research_more.languages(platforms, period, games_only)
    elif view == VIEWS[6]:
        ui_research_more.compare(platforms, period, games_only)
    else:
        ui_research_more.collection(api)


@st.fragment(run_every=10)
def first_snapshot(enabled):
    """Shown until the very first cycle lands; checks back every 10 seconds."""
    if stats_db.latest_ts("twitch") or stats_db.latest_ts("kick"):
        st.rerun(scope="app")
    if not enabled:
        ui_theme.empty_state("⏸️", "Stats collection is off",
                             "Turn it on to start recording.")
        ui_research_more.collection_toggle()
        return
    ui_theme.empty_state("📡", "Recording the first snapshot…",
                         "Reading every big live stream on Twitch and Kick - about two minutes. "
                         "This page fills in by itself when it lands.")


def controls():
    with st.container(border=True, key="card_gr_controls"):
        ui_theme.step("🔬", "Game research",
                      "Who is watching what, on which platform - and where a streamer or "
                      "clipper has the best shot.")
        cols = st.columns([1.6, 1.8, 1], vertical_alignment="bottom")
        options = ["twitch", "kick"] + (["youtube"] if youtube_key() else [])
        platforms = cols[0].pills("Platforms", options, selection_mode="multi",
                                  default=["twitch", "kick"], format_func=PLATFORM_LABELS.get,
                                  key="gr_platforms")
        if "gr_period" not in st.session_state:
            st.session_state["gr_period"] = "live" if rs.history_span() < 2 * 3600 else "24h"
        period = cols[1].segmented_control("Period", list(PERIOD_LABELS),
                                           format_func=PERIOD_LABELS.get, required=True,
                                           key="gr_period")
        games_only = cols[2].toggle("Games only", value=True, key="gr_games_only",
                                    help="Hide Just Chatting, IRL, Slots and other non-game "
                                         "categories.")
        with st.expander("What do these numbers mean?", icon=":material/help:"):
            st.markdown(GLOSSARY)
            if not youtube_key():
                st.caption("YouTube joins with a free API key (`youtube_api_key` in "
                           "data/config.json).")
    return tuple(p for p in options if p in (platforms or [])), period, games_only


def coverage_note(platforms, period, view):
    if view in (VIEWS[2], VIEWS[7]):            # always about the latest snapshot
        return
    if period == "live":
        stamps = [stats_db.latest_ts(p) for p in platforms if stats_db.latest_ts(p)]
        if stamps:
            st.caption("🟢 Live snapshot from %d min ago · a new one every %d minutes"
                       % ((time.time() - max(stamps)) // 60, stats_collect.INTERVAL_MIN))
        return
    cov = coverage(platforms, period)
    worst = min(share for share, _c in cov.values())
    st.caption("🗂️ Recorded so far: %s of the last %s%s" % (
        ", ".join("%s %d%%" % (PLATFORM_LABELS[p], share * 100) for p, (share, _c) in cov.items()),
        PERIOD_LABELS[period].lower(),
        " - watch hours look low until more is recorded; averages and peaks are fair already."
        if worst < 0.5 else ""))


def open_game(key):
    st.session_state["gr_game"] = key
    st.session_state["gr_view"] = GAME_PAGE


def _picked():
    """A game was clicked in "Open a game page": go to its page, clear the pick.
    A callback, because widgets may only be changed before they are drawn."""
    key = st.session_state.get("gr_open_pick")
    st.session_state["gr_open_pick"] = None
    if key:
        open_game(key)


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------
def show_top(key, default=25):
    """The "Top 10 / 25 / 50 / 100" switch every ranked list uses."""
    return st.segmented_control("Show", TOP_SIZES, format_func="Top {}".format, default=default,
                                required=True, key=key, label_visibility="collapsed")


def platform_chips(row, platforms):
    parts = [(p, row.get(p)) for p in platforms if p in row and not pd.isna(row.get(p))]
    total = sum(v for _p, v in parts) or 1
    return [("%s %d%%" % (PLATFORM_LABELS[p], round(v / total * 100)), "cs-plat " + p)
            for p, v in parts if v]


def change_chip(change):
    if change is None or pd.isna(change):
        return []
    return [("%s %d%%" % ("▲" if change >= 0 else "▼", abs(round(change))),
             "cs-move up" if change >= 0 else "cs-move down")]


def overview(platforms, period, games_only):
    live = period == "live"
    ui_theme.insights(cached_insights(platforms, period, games_only))
    df = board(platforms, period, games_only)
    if df.empty:
        ui_theme.empty_state("📭", "Nothing recorded for this period yet", "Try Live now.")
        return
    st.markdown("#### %s" % ("Most watched right now" if live
                             else "Most watched - last %s" % PERIOD_LABELS[period].lower()))
    size = show_top("gr_top")
    value = "viewers" if live else "watch_h"
    best = df[value].max() or 1
    rows = []
    for _i, row in df.head(size).iterrows():
        row = row.to_dict()
        if live:
            sub = "%s streamers live · %s viewers each on average" % (
                fmt(row["channels"]), fmt(row["viewers"] / max(row["channels"], 1)))
        else:
            sub = "%s average viewers · peak %s · %s streamers" % (
                fmt(row["avg_viewers"]), fmt(row["peak_viewers"]), fmt(row["avg_channels"]))
        rows.append({"pos": row["rank"], "icon": icon(row["key"]), "name": row["name"],
                     "sub": sub, "chips": platform_chips(row, platforms) +
                     change_chip(row.get("change")),
                     "value": fmt(row[value]), "value_sub": "watching" if live else "hours watched",
                     "bar": row[value] / best * 100})
    ui_theme.ranked(rows)
    names = dict(zip(df["key"], df["name"]))
    st.pills("Open a game page", list(df["key"].head(size)), format_func=names.get,
             key="gr_open_pick", on_change=_picked)

    gainers, losers, label = rm.movers(list(platforms))
    if label and (not gainers.empty or not losers.empty):
        st.markdown("#### Biggest changes since %s" % label)
        left, right = st.columns(2)
        with left:
            st.caption("📈 Gaining")
            mover_rows(gainers)
        with right:
            st.caption("📉 Losing")
            mover_rows(losers)

    with st.expander("Full table (every game, every number)", icon=":material/table:"):
        table, config = full_table(df, platforms, period)
        st.dataframe(table, hide_index=True, width="stretch", height=520, column_config=config)


def mover_rows(df):
    if df is None or df.empty:
        st.caption("Nothing big.")
        return
    ui_theme.ranked([{
        "pos": "", "icon": icon(r["key"]), "name": r["name"],
        "sub": "%s → %s viewers" % (fmt(r["before"]), fmt(r["now"])),
        "value": ("%+d%%" % r["pct"]) if r["pct"] is not None and not pd.isna(r["pct"])
        else "new", "value_sub": "%s%s" % ("+" if r["change"] > 0 else "", fmt(r["change"]))}
        for _i, r in df.head(5).iterrows()])


def full_table(df, platforms, period):
    num = st.column_config.NumberColumn
    by_platform = {PLATFORM_LABELS[p]: df[p] for p in platforms if p in df}
    icons = df["key"].map(lambda k: icon(k, 52, 70))
    if period == "live":
        table = pd.DataFrame({"#": df["rank"], " ": icons, "Game": df["name"],
                              "Watching": df["viewers"], "Streamers": df["channels"],
                              "Viewers per streamer": df["viewers"] / df["channels"].where(
                                  df["channels"] > 0),
                              "Biggest streamer": df["top1"], **by_platform})
    else:
        table = pd.DataFrame({
            "#": df["rank"], " ": icons, "Game": df["name"], "Watch hours": df["watch_h"],
            "Avg viewers": df["avg_viewers"], "Peak viewers": df["peak_viewers"],
            "Hours streamed": df["airtime_h"], "Avg streamers": df["avg_channels"],
            "Peak streamers": df["peak_channels"], "Viewers per streamer": df["vpc"],
            "Share %": df["share"], "Change %": df["change"], **by_platform})
    config = {name: num(format="localized") for name in table.columns
              if name not in ("#", " ", "Game", "Viewers per streamer", "Share %", "Change %")}
    config.update({" ": st.column_config.ImageColumn(" ", width="small"),
                   "Viewers per streamer": num(format="%.1f"), "Share %": num(format="%.2f%%"),
                   "Change %": num(format="%+.0f%%", help="Average viewers vs the period before")})
    return table, config


# ---------------------------------------------------------------------------
# One game
# ---------------------------------------------------------------------------
def game_page(api, platforms, period, games_only):
    df = board(platforms, "live", games_only)
    keys = list(df["key"]) if not df.empty else []
    names = rs.names()
    keys += [k for k, _n, _s in stats_db.tracked() if k not in keys]
    if not keys:
        st.info("No games recorded yet.")
        return
    if st.session_state.get("gr_game") not in keys:
        st.session_state["gr_game"] = keys[0]
    cols = st.columns([3, 1], vertical_alignment="bottom")
    key = cols[0].selectbox("Game", keys, format_func=lambda k: names.get(k, k), key="gr_game",
                            help="Everything live right now, biggest first, plus your tracked "
                                 "games. Type to search.")
    tracked = key in {k for k, _n, _s in stats_db.tracked()}
    if cols[1].toggle("Track", value=tracked, key="gr_track_%s" % key,
                      help="Count every streamer of this game every 15 minutes (up to %d games) "
                           "for exact numbers." % stats_collect.MAX_TRACKED) != tracked:
        stats_db.set_tracked(key, names.get(key, key), not tracked)
        st.rerun()

    name = names.get(key, key)
    card, _board = rs.game_card(key, list(platforms), period)
    if card is None:
        st.info("%s was not live on these platforms in this period." % name)
        return
    _tags, _words, drops = rm.game_terms(key, list(platforms))
    chips = [("#%d of %d" % (card["rank"], card["of"]), "cs-info")] + change_chip(
        card.get("change"))
    typical = card.get("typical")
    if typical is not None:
        chips.append(("Hard to be seen" if typical <= 2 else "Some room" if typical < 8
                      else "Room to grow", "cs-warn" if typical <= 2 else "cs-good"))
    if drops >= 40:
        chips.append(("🎁 Drops running", "cs-warn"))
    ui_theme.game_hero(icon(key, 168, 224), name,
                       rm.game_story(name, card, period, platforms, typical), chips)
    kpis(card, period)
    charts(key, name, platforms, period)
    ui_research_tools.game_tabs(api, key, name, platforms, period)


def kpis(card, period):
    live = period == "live"
    change = card.get("change")
    delta = None if change is None or pd.isna(change) else "%+.0f%% vs before" % change
    vpc = (card["viewers"] / card["channels"] if card.get("channels") else None) if live \
        else card.get("vpc")
    if live:
        items = [("Watching now", fmt(card.get("viewers")), None, "People watching right now"),
                 ("Streamers live", fmt(card.get("channels")), None, "Channels streaming it now")]
    else:
        items = [("Average viewers", fmt(card.get("avg_viewers")), delta,
                  "People watching at once, averaged over the period"),
                 ("Peak viewers", fmt(card.get("peak_viewers")), None, "The busiest moment"),
                 ("Watch hours", fmt(card.get("watch_h")), None, "Viewers x time"),
                 ("Streamers on average", fmt(card.get("avg_channels")), None,
                  "Channels live at once, averaged")]
    items += [("Viewers per streamer", fmt(vpc, 1), None, "Average viewers / streamers - "
               "pulled up by the big channels"),
              ("Typical streamer gets", fmt(card.get("typical"), 1), None,
               "Half of all streamers of this game have this many viewers or fewer"),
              ("Biggest streamer" if live else "Hours streamed",
               fmt(card.get("top1") if live else card.get("airtime_h")), None, None)]
    ui_theme.kpis(items)


def charts(key, name, platforms, period):
    series = rs.game_series(key, list(platforms), period)
    if series.empty or series["time"].nunique() < 2:
        st.caption("📈 Charts appear after the next snapshot (every %d minutes)."
                   % stats_collect.INTERVAL_MIN)
        return
    split = st.toggle("Split by platform", key="gr_split",
                      help="Show each platform as its own line instead of the total.")
    series["Platform"] = series["platform"].map(PLATFORM_LABELS)
    left, right = st.columns(2)
    for column, measure, title in ((left, "viewers", "People watching %s" % name),
                                   (right, "channels", "Streamers live in %s" % name)):
        with column:
            st.markdown("##### %s" % title)
            if split:
                present = [PLATFORM_LABELS[p] for p in platforms
                           if PLATFORM_LABELS[p] in set(series["Platform"])]
                ui_charts.show(ui_charts.lines(series, "time", measure, "Platform", present,
                                               [ui_charts.PLATFORM_COLORS[p] for p in present],
                                               measure.title()), height=230)
            else:
                total = series.groupby("time")[measure].sum().reset_index()
                ui_charts.show(ui_charts.total(total, "time", measure, measure.title()),
                               height=230)
