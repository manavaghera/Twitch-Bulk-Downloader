"""The Clip Radar page: clips blowing up right now across the top games, ready to grab."""

import time
from datetime import date
from pathlib import Path

import streamlit as st

from . import captions, jobs, radar, reposts, ui_login, ui_theme, yt_quota
from .folders import saved_folder
from .session import download_clips
from .ui_common import busy_elsewhere, progress_panel, start_job
from .trends_cli import youtube_key
from .ui_downloader import STYLE_LABELS
from .util import sanitize

PERMISSION_CHIPS = {"allowed": ("✅ allowed", "cs-good"), "ask": ("❔ not sure", "cs-move"),
                    "blocked": ("⛔ don't use", "cs-warn")}


def _fmt(value):
    value = float(value or 0)
    for size, suffix in ((1e6, "M"), (1e3, "K")):
        if value >= size:
            return ("%.1f" % (value / size)).rstrip("0").rstrip(".") + suffix
    return "%d" % value


@st.cache_data(ttl=600, show_spinner="Scanning the top games' clips - a few seconds…")
def cached_scan(client_id, client_secret, games, hours, nonce):
    return radar.scan(client_id, client_secret, games, hours)


def render(api):
    if api is None:
        ui_theme.empty_state("🔌", "Connect to Twitch first",
                             "Paste your Twitch app credentials in the sidebar to start.")
        return
    with st.container(border=True, key="card_radar"):
        ui_theme.step("📡", "Clip radar",
                      "The clips taking off right now across the biggest games, fastest "
                      "first - grab them before other clip channels repost them.")
        cols = st.columns(3, vertical_alignment="bottom")
        hours = cols[0].segmented_control("Clips from the last", (6, 24, 48), default=24,
                                          format_func="{} hours".format, required=True,
                                          key="rad_hours")
        games = cols[1].segmented_control("Across the top", (25, 50, 100), default=50,
                                          format_func="{} games".format, required=True,
                                          key="rad_games")
        language = cols[2].segmented_control("Language", ("en", ""), default="en",
                                             format_func=lambda v: "English" if v else "Any",
                                             required=True, key="rad_lang")
        cols = st.columns([1.4, 1, 1, 1, 1], vertical_alignment="bottom")
        min_views = cols[0].select_slider("At least this many views", [0, 100, 500, 1000, 5000],
                                          value=500, key="rad_min")
        hide_have = cols[1].toggle("Hide clips I have", value=True, key="rad_have")
        hide_spam = cols[2].toggle("Hide ads & spam", value=True, key="rad_spam",
                                   help="Clips whose titles advertise a website or cheats.")
        gameplay = cols[3].toggle("Gameplay only", value=True, key="rad_play",
                                  help="Hides clips of chatting, reacting or a caster desk - "
                                       "and skips them when downloading, like a normal run.")
        if cols[4].button("Scan again", icon=":material/refresh:", width="stretch"):
            st.session_state["rad_nonce"] = time.time()
    result = cached_scan(api.client_id, api.client_secret, games, hours,
                         st.session_state.get("rad_nonce", 0))
    clips = radar.filtered(result, language, min_views, hide_have, hide_blocked=True,
                           hide_spam=hide_spam, hide_talk=gameplay)
    age = (time.time() - result["scanned_at"]) / 60
    if not clips:
        ui_theme.empty_state("🔭", "Nothing matches", "Lower the minimum views or widen "
                                                     "the window.")
        return
    best = clips[0]
    ui_theme.insights([
        ("📡", "Scanned <b>%d</b> games and <b>%s</b> clips %s. <b>%d</b> match your filters."
               % (result["games"], _fmt(len(result["clips"])),
                  "just now" if age < 1 else "%d min ago" % age, len(clips))),
        ("🔥", "Fastest: <b>%s</b> by %s in %s - <b>%s views an hour</b>." % (
            ui_theme.escape(best.get("title") or "untitled"),
            ui_theme.escape(best.get("broadcaster_name") or "?"),
            ui_theme.escape(best["game_name"]), _fmt(best["per_hour"])))],
        title="Right now")

    size = st.segmented_control("Show", (10, 25, 50, 100), default=25, required=True,
                                format_func="Top {}".format, key="rad_top",
                                label_visibility="collapsed")
    shown = clips[:size]
    top_speed = shown[0]["per_hour"] or 1
    ui_theme.ranked([{
        "pos": i, "icon": clip.get("thumbnail_url") or "", "name": clip.get("title") or "untitled",
        "url": clip.get("url"),
        "sub": "%s · %s · %s views · %s old" % (
            clip.get("broadcaster_name"), clip["game_name"], _fmt(clip.get("view_count")),
            ("%.0fh" % clip["age_hours"]) if clip["age_hours"] >= 1 else
            "%d min" % (clip["age_hours"] * 60)),
        "chips": ([PERMISSION_CHIPS[clip["permission"]]] if clip.get("permission") in
                  PERMISSION_CHIPS else [("permission not checked", "cs-move")])
        + _repost_chip(clip),
        "value": _fmt(clip["per_hour"]), "value_sub": "views / hour",
        "bar": clip["per_hour"] / top_speed * 100}
        for i, clip in enumerate(shown, 1)], wide=True)
    st.caption("Click a title to watch it on Twitch. Mark streamers Allowed or Don't use on "
               "the Streamers page - reposting without permission risks copyright strikes.")
    download_box(api, shown, gameplay)


def download_box(api, shown, gameplay=True):
    job = jobs.latest("download")
    with st.container(border=True, key="card_radar_dl"):
        ui_theme.step("⬇️", "Grab clips", "Downloads land in a 'Clip radar' folder, each "
                                           "with a .txt of title ideas and hashtags.")
        by_id = {c["id"]: c for c in shown}
        labels = {c["id"]: "#%d %s - %s" % (i, c.get("broadcaster_name"),
                                           (c.get("title") or "")[:50])
                  for i, c in enumerate(shown, 1)}
        quick = st.columns(3)
        for col, count in zip(quick, (5, 10, 25)):
            if col.button("Pick the top %d" % count, key="rad_pick_%d" % count,
                          width="stretch"):
                st.session_state["rad_selected"] = list(by_id)[:count]
        st.session_state["rad_selected"] = [c for c in st.session_state.get(
            "rad_selected", []) if c in by_id]
        picked = st.multiselect("Clips to download", list(by_id), key="rad_selected",
                                format_func=labels.get)
        cols = st.columns(3, vertical_alignment="bottom")
        output = cols[0].segmented_control("Make", ("video", "short", "both"), default="short",
                                           required=True, key="rad_output", format_func={
                                               "video": "Videos", "short": "Shorts",
                                               "both": "Both"}.get)
        style = cols[1].selectbox("Shorts look", list(STYLE_LABELS), format_func=STYLE_LABELS.get,
                                  key="rad_style", disabled=output == "video")
        with_captions = cols[2].toggle("Captions", value=False, key="rad_captions",
                                       disabled=output == "video" or not captions.available())
        repost_check(picked, by_id, labels)
        other = busy_elsewhere("download")
        go = ui_login.may_download("rad_sign_in") and st.button("Download %d clip%s" % (len(picked), "" if len(picked) == 1 else "s"),
                       type="primary", icon=":material/download:", width="stretch",
                       disabled=not picked or bool(other) or bool(job and job.running))
        if other:
            st.caption("⏳ %s is downloading - this can start when it is done." % other)
    if job and job.running:
        progress_panel("download", where="_radar")
    elif job and job.result and job.label.startswith("Clip radar"):
        st.success("Done: %s - see the full list on the Clip downloader tab." % job.label,
                   icon=":material/check_circle:")
    if go:
        clips = [by_id[c] for c in picked]
        folder = _folder()
        start_job("download", "Clip radar, %d clips" % len(clips),
                  lambda: download_clips(clips, folder, output, style, with_captions,
                                         label="Clip radar", api=api, gameplay_only=gameplay))


def _repost_chip(clip):
    hit = reposts.cached(clip.get("id"))
    if hit is None:
        return []
    n = len(hit["matches"])
    return [("on YouTube %dx" % n, "cs-warn")] if n else [("not on YouTube yet", "cs-good")]


def repost_check(picked, by_id, labels):
    """Before downloading: are the picked clips already on YouTube Shorts?"""
    key = youtube_key()
    if not key or not picked:
        return
    todo = [c for c in picked if reposts.cached(c) is None][:10]
    cost = len(todo) * reposts.COST
    if todo and st.button("Check %d on YouTube Shorts first (~%s of %s units left today)"
                          % (len(todo), "{:,}".format(cost), "{:,}".format(yt_quota.remaining())),
                          icon=":material/find_in_page:", key="rad_repost",
                          disabled=not yt_quota.can_spend(cost)):
        with st.spinner("Searching YouTube…"):
            for clip_id in todo:
                try:
                    reposts.check(key, by_id[clip_id])
                except ValueError as error:
                    st.error(str(error))
                    break
    done = [(c, reposts.cached(c)) for c in picked if reposts.cached(c) is not None]
    for clip_id, hit in done:
        if hit["matches"]:
            first = hit["matches"][0]
            st.warning("%s - already posted %d time%s, e.g. [%s](%s) by %s"
                       % (labels[clip_id], len(hit["matches"]),
                          "" if len(hit["matches"]) == 1 else "s",
                          first["title"].replace("[", "(").replace("]", ")"), first["url"],
                          first["channel"]), icon=":material/content_copy:")
    fresh = sum(1 for _c, hit in done if not hit["matches"])
    if fresh:
        st.caption("✅ %d of the checked clips are not on YouTube Shorts yet." % fresh)


def _folder():
    root = saved_folder()[0]
    return Path(root) / sanitize("Clip radar") / date.today().isoformat()
