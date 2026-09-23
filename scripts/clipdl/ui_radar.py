"""The Clip Radar page: clips blowing up right now across the top games, ready to grab."""

import time
from datetime import date
from pathlib import Path

import streamlit as st

from . import captions, jobs, radar, ui_login, ui_theme
from .folders import saved_folder
from .session import download_clips
from .ui_common import busy_elsewhere, progress_panel, start_job
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
        cols = st.columns([1.4, 1, 1, 1], vertical_alignment="bottom")
        min_views = cols[0].select_slider("At least this many views", [0, 100, 500, 1000, 5000],
                                          value=500, key="rad_min")
        hide_have = cols[1].toggle("Hide clips I have", value=True, key="rad_have")
        hide_spam = cols[2].toggle("Hide ads & spam", value=True, key="rad_spam",
                                   help="Clips whose titles advertise a website or cheats.")
        if cols[3].button("Scan again", icon=":material/refresh:", width="stretch"):
            st.session_state["rad_nonce"] = time.time()
    result = cached_scan(api.client_id, api.client_secret, games, hours,
                         st.session_state.get("rad_nonce", 0))
    clips = radar.filtered(result, language, min_views, hide_have, hide_blocked=True,
                           hide_spam=hide_spam)
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
        "chips": [PERMISSION_CHIPS[clip["permission"]]] if clip.get("permission") in
        PERMISSION_CHIPS else [("permission not checked", "cs-move")],
        "value": _fmt(clip["per_hour"]), "value_sub": "views / hour",
        "bar": clip["per_hour"] / top_speed * 100}
        for i, clip in enumerate(shown, 1)], wide=True)
    st.caption("Click a title to watch it on Twitch. Mark streamers Allowed or Don't use on "
               "the Streamers page - reposting without permission risks copyright strikes.")
    download_box(shown)


def download_box(shown):
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
        other = busy_elsewhere("download")
        go = ui_login.may_download("rad_sign_in") and st.button("Download %d clip%s" % (len(picked), "" if len(picked) == 1 else "s"),
                       type="primary", icon=":material/download:", width="stretch",
                       disabled=not picked or bool(other) or bool(job and job.running))
        if other:
            st.caption("⏳ Waiting for the running %s to finish." % other)
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
                                         label="Clip radar"))


def _folder():
    root = saved_folder()[0]
    return Path(root) / sanitize("Clip radar") / date.today().isoformat()
