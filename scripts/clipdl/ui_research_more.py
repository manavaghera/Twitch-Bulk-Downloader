"""Game Research: where would I rank, languages, compare, and the data collector."""

import json
import sys
import time
from datetime import datetime

import pandas as pd
import streamlit as st

from . import research as rs
from . import research_more as rm
from . import stats_collect, stats_db, ui_charts, ui_theme
from .folders import load_prefs, save_prefs
from .ui_common import is_hosted
from .trends_cli import youtube_key

PLATFORM_LABELS = {"twitch": "Twitch", "kick": "Kick", "youtube": "YouTube"}


def _ur():
    from . import ui_research
    return ui_research


# ---------------------------------------------------------------------------
# Where would I rank?
# ---------------------------------------------------------------------------
def where_would_i_rank(platforms, games_only):
    ur = _ur()
    st.markdown("#### 🎯 Where would I rank?")
    ui_theme.note("Directories list streams by viewers, biggest first. Tell it how many "
                  "viewers you usually have, and it finds the games where you'd sit near the "
                  "top of the list <b>right now</b> - where browsing viewers actually see you.")
    choices = [p for p in platforms if p in ("twitch", "kick")] or ["twitch"]
    cols = st.columns([1.2, 1, 1.4], vertical_alignment="bottom")
    platform = cols[0].segmented_control("I stream on", choices, default=choices[0],
                                         required=True, format_func=PLATFORM_LABELS.get,
                                         key="fit_platform")
    mine = cols[1].number_input("My usual viewers", 1, 50000, 10, step=5, key="fit_viewers")
    audience = cols[2].select_slider("Only games with at least this many viewers",
                                     [100, 250, 500, 1000, 2500, 5000], value=500,
                                     key="fit_min")
    df = rm.streamer_fit(platform, int(mine), audience, games_only)
    if df.empty:
        st.info("No snapshot of %s yet, or no game this size." % PLATFORM_LABELS[platform])
        return
    first = df[df["position"] <= 12]
    ui_theme.insights([
        ("🎯", "With <b>%d</b> viewers you'd be on the <b>first screen</b> (top 12) of "
               "<b>%d</b> games on %s right now." % (mine, len(first), PLATFORM_LABELS[platform])),
        ("👀", "Best pick: <b>%s</b> - you'd be about <b>#%d</b> of %s streamers (counting "
               "you), with %s people browsing it." % (
                   ui_theme.escape(df.iloc[0]["name"]), df.iloc[0]["position"],
                   ur.fmt(df.iloc[0]["channels"] + 1), ur.fmt(df.iloc[0]["open"])))],
        title="Right now")
    size = ur.show_top("fit_top")
    rows = []
    for i, (_n, r) in enumerate(df.head(size).iterrows(), 1):
        spot = r["position"]
        chips = [("First screen", "cs-good") if spot <= 12 else
                 ("First page", "cs-info") if spot <= 30 else ("Scrolled past", "cs-warn")]
        if not r["exact"]:
            chips.append(("estimate - small channels not counted", "cs-move"))
        rows.append({"pos": i, "icon": ur.icon(r["key"]), "name": r["name"],
                     "sub": "%s people browsing · you'd be #%d of %s streamers" % (
                         ur.fmt(r["open"]), spot, ur.fmt(r["channels"] + 1)),
                     "chips": chips, "value": ("#%d" if r["exact"] else "~#%d") % spot,
                     "value_sub": "your spot", "bar": max(0, 100 - spot * 3)})
    ui_theme.ranked(rows)
    st.caption("People browsing = the game's viewers outside its single biggest channel. "
               "Games owned by one streamer, or with under 5 channels, are left out. Positions "
               "change through the day - check again at the hour you plan to stream.")


# ---------------------------------------------------------------------------
# Languages
# ---------------------------------------------------------------------------
def languages(platforms, period, games_only):
    ur = _ur()
    st.markdown("#### 🌍 Your language's gap")
    ui_theme.note("Pick the language you stream in. These games have many viewers in it but "
                  "few streamers - the audience is there, the competition is not.")
    seen = rm.languages_seen(list(platforms), period)
    if not seen:
        st.info("Languages are recorded once an hour - check back after the next hour.")
        return
    default = "en" if "en" in seen else seen[0]
    lang = st.selectbox("I stream in", seen, index=seen.index(default), format_func=rs.lang_name,
                        key="gap_lang")
    gap = rm.language_gap(list(platforms), period, lang, games_only=games_only)
    name = rs.lang_name(lang)
    if gap.empty:
        st.info("No game with enough %s viewers in this period yet." % name)
    else:
        size = ur.show_top("gap_top")
        ui_theme.ranked([{
            "pos": i, "icon": ur.icon(r["key"]), "name": r["name"],
            "sub": "%d%% of its viewers watch in %s, but only %d%% of its streamers stream in it"
                   % (round(r["viewer_share"]), name, round(r["channel_share"])),
            "chips": [("~%s %s viewers" % (ur.fmt(r["viewers"]), name), "cs-info"),
                      ("~%s %s streamers" % (ur.fmt(r["channels"]), name), "cs-move")],
            "value": "%.1fx" % r["gap"], "value_sub": "viewers per streamer",
            "bar": min(100, r["gap"] * 20)}
            for i, (_n, r) in enumerate(gap.head(size).iterrows(), 1)])
        st.caption("2.0x means each %s streamer of that game has twice the audience of an "
                   "average streamer of it." % name)

    st.markdown("#### Top games in each language")
    rows = rs.by_language(list(platforms), period, games_only)
    total = sum(t for _l, t, _g in rows) or 1
    labels = {v: k for k, v in rs.names().items()}
    cols = st.columns(3)
    for i, (code, viewers, games) in enumerate(rows[:9]):
        with cols[i % 3]:
            with st.container(border=True, key="card_lang_%s" % code):
                st.markdown("**%s** · %.1f%% of viewers" % (rs.lang_name(code),
                                                            viewers / total * 100))
                st.caption(rs.LANGUAGE_REGIONS.get(code, ""))
                ui_theme.mini_list([(ur.icon(labels.get(game, ""), 44, 58), game,
                                     "%d%%" % round(share)) for game, share in games])


# ---------------------------------------------------------------------------
# Compare
# ---------------------------------------------------------------------------
def compare(platforms, period, games_only):
    ur = _ur()
    df = ur.board(platforms, period, games_only)
    if df.empty:
        st.info("Nothing recorded yet.")
        return
    options = list(df["key"])
    label = dict(zip(df["key"], df["name"]))
    picked = st.multiselect("Games to compare (up to 5)", options, default=options[:3],
                            max_selections=5, format_func=label.get, key="cmp_games")
    if not picked:
        return
    colors = ui_charts.SERIES[:len(picked)]
    ui_theme.legend([(c, ur.icon(k, 40, 54), label[k]) for c, k in zip(colors, picked)])
    frames = []
    for key in picked:
        series = rs.game_series(key, list(platforms), period)
        if not series.empty:
            frames.append(series.groupby("time")["viewers"].sum().reset_index()
                          .assign(Game=label[key]))
    if frames and pd.concat(frames)["time"].nunique() >= 2:
        names = [label[k] for k in picked]
        ui_charts.show(ui_charts.lines(pd.concat(frames), "time", "viewers", "Game", names,
                                       colors, "People watching", legend=False), height=320)
    else:
        st.caption("📈 The chart appears after the next snapshot.")
    live = period == "live"
    rows = df.set_index("key").loc[picked].reset_index()
    ui_theme.ranked([{
        "pos": int(r["rank"]), "icon": ur.icon(r["key"]), "name": r["name"],
        "sub": ("%s streamers · %s viewers each" % (ur.fmt(r["channels"]),
                                                     ur.fmt(r["viewers"] / max(r["channels"], 1))))
        if live else ("peak %s · %s streamers · %s hours watched" % (
            ur.fmt(r["peak_viewers"]), ur.fmt(r["avg_channels"]), ur.fmt(r["watch_h"]))),
        "chips": ur.change_chip(r.get("change")) if not live else [],
        "value": ur.fmt(r["viewers"] if live else r["avg_viewers"]),
        "value_sub": "watching" if live else "average viewers"}
        for _i, r in rows.iterrows()])


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------
def collection_toggle():
    enabled = bool(load_prefs().get("stats_collector", True))
    wanted = st.toggle("Record stats in the background", value=enabled, key="gr_collect",
                       help="Every %d minutes while this app runs." % stats_collect.INTERVAL_MIN)
    if wanted != enabled:
        save_prefs(stats_collector=wanted)
        stats_collect.BACKGROUND.configure(enabled=wanted)
        st.rerun()
    return wanted


def collection(api):
    st.markdown("#### ⚙️ Data collection")
    enabled = collection_toggle()
    last = float(stats_db.get_meta("last_cycle", 0) or 0)
    size = stats_db.DB_FILE.stat().st_size if stats_db.DB_FILE.exists() else 0
    ui_theme.kpis([
        ("Last snapshot", _ago(last), None, None),
        ("Next due", "running…" if stats_collect.BACKGROUND.busy else (
            _in(stats_collect.next_due()) if enabled else "off"), None, None),
        ("History", _span(rs.history_span()), None,
         "Kept for %d days" % stats_db.RETENTION_DAYS),
        ("Database", "%.1f MB" % (size / 1e6), None, str(stats_db.DB_FILE))])
    if st.button("Record a snapshot now", icon=":material/radio_button_checked:",
                 disabled=stats_collect.BACKGROUND.busy):
        stats_db.set_meta("last_cycle", 0)
        stats_collect.BACKGROUND.configure(enabled=True)
        st.toast("Recording - about two minutes.")

    rows, label = [], rs.names()
    for platform in ("twitch", "kick", "youtube"):
        status = json.loads(stats_db.get_meta("status_" + platform, "{}") or "{}")
        rows.append({"Platform": PLATFORM_LABELS[platform],
                     "Last result": status.get("error") or status.get("paused") or (
                         "%s streams, %s games" % (status.get("streams", "–"),
                                                   status.get("games", "–"))
                         if status else ("needs an API key" if platform == "youtube"
                                         and not youtube_key() else "not run yet")),
                     "When": _ago(status.get("at")) if status else "–",
                     "Counted in full": ", ".join(label.get(k, k) for k in status.get("full")
                                                  or []) or "–"})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    tracked = stats_db.tracked()
    st.markdown("##### Tracked games (%d of %d)" % (len(tracked), stats_collect.MAX_TRACKED))
    if not tracked:
        st.caption("Track a game from its game page to count every one of its streamers, "
                   "every cycle.")
    for key, name, since in tracked:
        c = st.columns([4, 1])
        c[0].markdown("**%s** · since %s" % (name, datetime.fromtimestamp(since)
                                               .strftime("%d %b %H:%M")))
        if c[1].button("Stop", key="untrack_%s" % key):
            stats_db.set_tracked(key, name, False)
            st.rerun()

    background_box()


def health_banner(platforms):
    """Say so when a platform's data stopped coming in, instead of showing old numbers."""
    for platform, problem in stats_collect.health(platforms, bool(youtube_key())):
        extra = (" Kick has no official API, so a change on its site can break this - "
                 "the other platforms keep working." if platform == "kick" else "")
        st.warning("**%s:** %s%s" % (PLATFORM_LABELS[platform], problem, extra),
                   icon=":material/warning:")


def background_box(where=""):
    """Keep recording with the page closed: the windowless recorder, now and at sign-in."""
    from . import recorder
    st.markdown("##### 🌙 Record with this page closed")
    st.caption("7- and 30-day numbers are only complete if something records around the "
               "clock. The background recorder is a small program with no window; it can "
               "start whenever you sign in to Windows.")
    live = recorder.running()
    started = recorder.since()
    cols = st.columns([1.3, 1, 1], vertical_alignment="center")
    cols[0].markdown("**Background recorder:** %s" % (
        "🟢 recording (since %s)" % datetime.fromtimestamp(started).strftime("%d %b %H:%M")
        if live and started else "🟢 recording" if live else "⚪ not running"))
    if not live and cols[1].button("Start it now", icon=":material/play_arrow:",
                                   key="rec_start" + where, width="stretch",
                                   disabled=is_hosted()):
        ok, message = recorder.start_now()
        (st.toast if ok else st.error)(message)
        st.rerun()
    if live and cols[1].button("Stop it", icon=":material/stop:", key="rec_stop" + where,
                               width="stretch"):
        ok, message = recorder.stop_now()
        (st.toast if ok else st.error)(message)
        st.rerun()
    if sys.platform == "win32" and not is_hosted():
        on = recorder.at_startup()
        wanted = cols[2].toggle("Start with Windows", value=on, key="rec_boot" + where,
                                help="Adds it to your sign-in apps (Task Manager > Startup "
                                     "apps). No admin rights needed; this switch removes it.")
        if wanted != on:
            ok, message = recorder.set_startup(wanted)
            (st.toast if ok else st.error)(message)
            if ok and wanted and not live:
                recorder.start_now()
            st.rerun()
    tail = recorder.log_tail()
    if tail:
        with st.expander("Recorder log", icon=":material/terminal:"):
            st.code(tail, language=None)


def _span(seconds):
    return "%.1f days" % (seconds / 86400) if seconds >= 86400 else "%.1f hours" % (seconds / 3600)


def _ago(stamp):
    if not stamp:
        return "never"
    minutes = (time.time() - float(stamp)) / 60
    return "%d min ago" % minutes if minutes < 120 else "%.1f h ago" % (minutes / 60)


def _in(stamp):
    minutes = (stamp - time.time()) / 60
    return "now" if minutes <= 0 else "in %d min" % max(1, minutes)
