"""Game Research: the tabs of a game page, the best games to stream, spikes and movers."""

import pandas as pd
import streamlit as st

from . import research as rs
from . import research_more as rm
from . import stats_db, ui_charts, ui_theme
from .trends_cli import youtube_key

PLATFORM_LABELS = {"twitch": "Twitch", "kick": "Kick", "youtube": "YouTube"}
CLIP_HOURS = {"live": 24, "24h": 24, "7d": 168, "30d": 720}


def _ur():
    """The main page module (icons, number format) - imported late, it imports us."""
    from . import ui_research
    return ui_research


# ---------------------------------------------------------------------------
# One game: the tabs under its charts
# ---------------------------------------------------------------------------
def game_tabs(api, key, name, platforms, period):
    tabs = st.tabs(["📺 Who's streaming it", "📊 Channel sizes", "🌍 Audience & countries",
                    "🕒 Best time to stream", "✂️ Clips", "🏷️ Tags & titles",
                    "▶️ YouTube Shorts"])
    with tabs[0]:
        live_channels(key, platforms)
    with tabs[1]:
        channel_sizes(key, name, platforms)
    with tabs[2]:
        audience(key, name, platforms, period)
    with tabs[3]:
        best_time(key, name, platforms)
    with tabs[4]:
        clip_sources(api, key, name, period)
    with tabs[5]:
        tags_and_titles(key, name, platforms)
    with tabs[6]:
        shorts_demand(name)


def live_channels(key, platforms):
    fmt = _ur().fmt
    rows = rs.top_channels(key, list(platforms))
    if not rows:
        st.info("No channel of this game in the latest snapshot.")
        return
    total = sum(r["viewers"] for r in rows) or 1
    ui_theme.ranked([{
        "pos": i, "icon": "", "name": r["channel"], "url": r["url"], "sub": r["title"],
        "chips": [(r["platform"], "cs-plat " + r["platform"].lower()),
                  (r["language"], "cs-info")] + ([("live " + r["live for"], "cs-move")]
                                                 if r["live for"] else []),
        "value": fmt(r["viewers"]), "value_sub": "watching", "bar": r["viewers"] / total * 100}
        for i, r in enumerate(rows[:12], 1)])
    st.caption("The biggest channels in the latest snapshot. Click a name to open the stream.")


def channel_sizes(key, name, platforms):
    fmt = _ur().fmt
    choices = [p for p in platforms if p != "youtube"] or list(platforms)
    platform = st.segmented_control("Platform", choices, default=choices[0], required=True,
                                    format_func=PLATFORM_LABELS.get, key="sizes_platform")
    df, complete, cutoff = rm.channel_sizes(key, platform)
    if df.empty:
        st.info("No %s snapshot of %s yet." % (PLATFORM_LABELS[platform], name))
        return
    st.markdown("##### How many viewers do %s streamers of %s get?"
                % (PLATFORM_LABELS[platform], name))
    ui_charts.show(ui_charts.sizes(df), height=240)
    total = df["channels"].sum()
    if complete and total:
        small = df[df["size"].isin(["0", "1", "2", "3–4"])]["channels"].sum()
        big = df[df["size"].isin(["100–249", "250–999", "1,000+"])]["channels"].sum()
        ui_theme.note("Every channel counted: <b>%d%%</b> of %s streamers have fewer than 5 "
                      "viewers, and only <b>%s</b> have 100 or more." % (
                          round(small / total * 100), fmt(total), fmt(big)))
    else:
        ui_theme.note("This snapshot counted channels with about <b>%d viewers or more</b>. "
                      "Turn on <b>Track</b> above to count every channel, including the "
                      "small ones." % cutoff)


def audience(key, name, platforms, period):
    langs = rs.game_languages(key, list(platforms), period)
    left, right = st.columns([1.1, 1])
    with left:
        st.markdown("##### Which languages watch it")
        if langs.empty:
            st.info("Languages are recorded once an hour - check back after the next hour.")
        else:
            ui_charts.show(ui_charts.bars(langs.head(8), "share", "language", "% of viewers",
                                          ",.1f"), height=230)
            lead = langs.iloc[0]
            ui_theme.note("<b>%d%%</b> of its viewers watch %s streams - mostly %s. Platforms "
                          "report a stream's language, not where viewers live." % (
                              round(lead["share"]), lead["language"], lead["watched_in"]))
    with right:
        st.markdown("##### Where it sells on Steam")
        if st.button("Check %d countries" % len(rs.STEAM_COUNTRIES), key="steam_%s" % key,
                     icon=":material/public:"):
            st.session_state["steam_for"] = key
        if st.session_state.get("steam_for") == key:
            ranks = cached_steam(name)
            found = sorted((rank, c) for c, _code, rank in ranks if rank)
            if found:
                ui_theme.ranked([{"pos": "", "icon": "", "name": country,
                                  "sub": "Steam best-sellers top 100", "value": "#%d" % rank,
                                  "value_sub": "rank", "bar": 101 - rank}
                                 for rank, country in found[:10]])
                missing = [c for c, _code, rank in ranks if not rank]
                if missing:
                    st.caption("Not in the top 100: " + ", ".join(missing))
            else:
                st.info("Not in the Steam top 100 of these countries (or not on Steam).")
        key_yt = youtube_key()
        if key_yt:
            st.markdown("##### Trending on YouTube, by country")
            if st.button("Check YouTube", key="yt_%s" % key, icon=":material/smart_display:"):
                st.session_state["yt_for"] = key
            if st.session_state.get("yt_for") == key:
                rows = [r for r in cached_youtube(name, key_yt) if r[1]]
                st.dataframe(pd.DataFrame(rows, columns=["Country", "Trending videos", "Of"]),
                             hide_index=True, width="stretch")


@st.cache_data(ttl=6 * 3600, show_spinner="Reading Steam's charts…")
def cached_steam(name):
    return rs.steam_countries(name)


@st.cache_data(ttl=6 * 3600, show_spinner="Reading YouTube's trending lists…")
def cached_youtube(name, api_key):
    return rs.youtube_countries(name, api_key)


def best_time(key, name, platforms):
    grid = rs.heatmap(key, [p for p in platforms if p != "youtube"] or list(platforms))
    if grid.empty:
        st.info("No history yet.")
        return
    best = grid.dropna(subset=["vpc"]).sort_values("vpc", ascending=False).head(3)
    if len(best):
        st.markdown("##### Best times to stream %s" % name)
        ui_theme.kpis([("%s %02d:00" % (d, h), "%.0f viewers" % v, None,
                        "Viewers per streamer at this hour (your time)")
                       for d, h, v in zip(best["day"], best["hour"], best["vpc"])])
        ui_theme.note("The hours with the most viewers for every streamer competing - "
                      "a smaller channel gets found more easily then.")
    metric = st.segmented_control("Map of", ["vpc", "viewers", "channels"], default="vpc",
                                  required=True, key="bt_metric", format_func={
                                      "vpc": "Viewers per streamer", "viewers": "Viewers",
                                      "channels": "Streamers (competition)"}.get)
    title = {"vpc": "Viewers/streamer", "viewers": "Viewers", "channels": "Streamers"}[metric]
    ui_charts.show(ui_charts.heatmap(grid, metric, title), height=250)
    days = rs.history_span() / 86400
    if days < 7:
        st.caption("Brighter = more. Built from %.1f days so far - a full week of recording "
                   "fills every hour of every day." % days)


def clip_sources(api, key, name, period):
    fmt = _ur().fmt
    info = stats_db.game_info().get(key) or (name, None)
    if not info[1]:
        st.info("Clip data comes from Twitch, and this game has no Twitch category.")
        return
    hours = CLIP_HOURS[period]
    st.markdown("##### Whose %s clips get watched (%s)" % (
        name, "last 24 hours" if hours <= 24 else "last %d days" % (hours // 24)))
    rows, totals = cached_clips(api.client_id, api.client_secret, info[1], hours)
    ui_theme.kpis([("Clip views", fmt(totals["views"]), None,
                    "Views of the game's most-viewed clips in the period (up to 300)"),
                   ("Clips", str(totals["clips"]), None, None),
                   ("Channels clipped", str(totals["channels"]), None, None)])
    if rows:
        best = rows[0]["views"] or 1
        ui_theme.ranked([{
            "pos": i, "icon": "", "name": r["channel"], "url": r["url"],
            "sub": "%d clips · %s views each on average" % (r["clips"], fmt(r["per_clip"])),
            "chips": [(r["language"], "cs-info")], "value": fmt(r["views"]),
            "value_sub": "clip views", "bar": r["views"] / best * 100}
            for i, r in enumerate(rows[:12], 1)])
        st.caption("For clip farmers: the channels whose clips of this game pull the most views "
                   "are the ones worth watching and clipping.")


@st.cache_data(ttl=1800, show_spinner="Reading the game's top clips…")
def cached_clips(client_id, client_secret, twitch_id, hours):
    from .ui_common import twitch_client
    return rs.clip_sources(twitch_client(client_id, client_secret), twitch_id, hours)


def tags_and_titles(key, name, platforms):
    tags, words, drops = rm.game_terms(key, list(platforms))
    if not tags and not words:
        st.info("Tags and titles are read for the 200 biggest games of each snapshot.")
        return
    if drops >= 20:
        ui_theme.note("🎁 <b>%d%%</b> of %s's viewers are on streams with <b>Drops</b> "
                      "enabled - a Drops campaign is running, which lifts viewers for now."
                      % (round(drops), ui_theme.escape(name)))
    left, right = st.columns(2)
    with left:
        st.markdown("##### Tags the streamers use")
        chip_list(tags, "#")
    with right:
        st.markdown("##### Words in their titles")
        chip_list(words, "")
    st.caption("From the streams in the latest snapshot; the number is how many use it. "
               "Language tags are left out.")


def chip_list(items, prefix):
    if not items:
        st.caption("Nothing common enough.")
        return
    ui_theme.html('<div class="cs-chips">%s</div>' % "".join(
        '<span class="cs-chip">%s%s <span style="color:#8C8C99">· %d</span></span>'
        % (prefix, ui_theme.escape(term), count) for term, count in items))


# ---------------------------------------------------------------------------
# Best games to stream
# ---------------------------------------------------------------------------
def best_games(platforms, period):
    ur = _ur()
    st.markdown("#### 💡 Best games to stream right now")
    ui_theme.note("Games with a real audience where an <b>ordinary</b> streamer - not one of "
                  "the top 5 - still gets viewers, and no single star owns the category.")
    min_viewers = st.select_slider("Only games with at least this many viewers",
                                   [50, 200, 500, 1000, 5000], value=200, key="op_min")
    df = rs.opportunities([p for p in platforms if p != "youtube"] or list(platforms), period,
                          min_viewers)
    if df.empty:
        st.info("Not enough recorded data yet for this filter.")
        return
    size = ur.show_top("op_top")
    rows = []
    for i, (_n, r) in enumerate(df.head(size).iterrows(), 1):
        chips = [("Room to grow" if r["top5"] < 50 else "A few stars dominate",
                  "cs-good" if r["top5"] < 50 else "cs-warn")]
        if r["counted"] != "full count":
            chips.append(("small channels not counted", "cs-move"))
        rows.append({"pos": i, "icon": ur.icon(r["key"]), "name": r["name"],
                     "sub": "~%s viewers per ordinary streamer · %s streamers · top 5 hold %d%%"
                     % (ur.fmt(r["typical"]), ur.fmt(r["avg_channels"]), round(r["top5"])),
                     "chips": chips, "value": "%d" % r["score"], "value_sub": "score",
                     "bar": r["score"]})
    ui_theme.ranked(rows)
    with st.expander("See it as a map", icon=":material/scatter_plot:"):
        df["label"] = [n if i < 5 else "" for i, n in enumerate(df["name"])]
        ui_charts.show(ui_charts.scatter(df.head(80)), height=380)
        st.caption("Right = more competition. Up = more viewers per ordinary streamer. "
                   "Bigger dot = bigger audience. Up and to the left is the sweet spot.")


# ---------------------------------------------------------------------------
# Spikes and movers
# ---------------------------------------------------------------------------
def spikes_and_movers(platforms):
    ur = _ur()
    st.markdown("#### ⚡ Spiking right now")
    ui_theme.note("Games far above their own normal for this time of day - a new update, a "
                  "big streamer, an event. Catch these early.")
    df = rs.breakouts(list(platforms))
    if df.empty:
        span = rs.history_span() / 3600
        st.info("Nothing is spiking against its usual level." if span >= 6 else
                "Spikes need a few hours of history to compare against (%.1f h so far)." % span)
    else:
        ui_theme.ranked([{
            "pos": i, "icon": ur.icon(r["key"]), "name": r["name"],
            "sub": "usually ~%s viewers at this hour, now %s" % (ur.fmt(r["usual"]),
                                                                 ur.fmt(r["viewers"])),
            "chips": [(s.strip(), "cs-warn") for s in r["signals"].split(",")],
            "value": ("%.1fx" % r["jump"]) if r["jump"] and not pd.isna(r["jump"]) else "new",
            "value_sub": "vs usual"}
            for i, (_n, r) in enumerate(df.head(50).iterrows(), 1)])
        st.caption("Compared with: %s." % df.attrs.get("baseline", "recent history"))

    gainers, losers, label = rm.movers(list(platforms))
    st.markdown("#### 📈 Biggest movers%s" % (" since %s" % label if label else ""))
    if not label:
        st.info("Movers compare with a few hours ago - they appear once 3 hours are recorded.")
        return
    left, right = st.columns(2)
    with left:
        st.caption("Gaining")
        ur.mover_rows(gainers)
    with right:
        st.caption("Losing")
        ur.mover_rows(losers)


# ---------------------------------------------------------------------------
# YouTube Shorts demand for one game
# ---------------------------------------------------------------------------
def shorts_demand(name):
    from . import yt_demand
    fmt = _ur().fmt
    st.markdown("##### Are Shorts of %s getting watched?" % name)
    key = youtube_key()
    if not key:
        st.info("This needs a free YouTube API key - add it in the sidebar under YouTube.")
        return
    used = yt_demand.units_today()
    days = st.segmented_control("Shorts published in the last", (1, 7, 30), default=7,
                                required=True, format_func="{} days".format, key="yd_days")
    result = yt_demand.cached(name, days)
    if st.button("Check YouTube Shorts" if not result else "Check again",
                 icon=":material/smart_display:", key="yd_go_%s" % name,
                 disabled=used + yt_demand.CHECK_COST > yt_demand.DAILY_QUOTA):
        try:
            result = yt_demand.check(key, name, days, force=True)
        except ValueError as error:
            st.error(str(error))
    st.caption("Each check uses ~%d of your %s free daily YouTube units (%s used today). "
               "Results are kept for 12 hours." % (yt_demand.CHECK_COST,
                                                   "{:,}".format(yt_demand.DAILY_QUOTA),
                                                   "{:,}".format(yt_demand.units_today())))
    if not result:
        return
    if not result["shorts"]:
        st.info("No Shorts about %s found for that window - an open lane, or a game Shorts "
                "viewers don't search for." % name)
        return
    ui_theme.kpis([("Shorts found", str(result["shorts"]), None, "Of the 50 most viewed"),
                   ("Their total views", fmt(result["total_views"]), None, None),
                   ("A typical Short got", fmt(result["median_views"]), None,
                    "Median views - half did better, half worse"),
                   ("Channels posting", str(result["channels"]), None, None)])
    best = result["top"][0]["views"] or 1
    ui_theme.ranked([{
        "pos": i, "icon": s["thumb"], "name": s["title"], "url": s["url"],
        "sub": "%s · %s" % (s["channel"], s["published"][:10]),
        "value": fmt(s["views"]), "value_sub": "views", "bar": s["views"] / best * 100}
        for i, s in enumerate(result["top"], 1)], wide=True)
    st.caption("A high typical number means Shorts of this game get watched even from small "
               "channels - good for clip farming.")
