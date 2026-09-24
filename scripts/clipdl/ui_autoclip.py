"""YouTube > Auto clips: a link and a number in, that many ready Shorts out."""

import os
from pathlib import Path

import streamlit as st

from . import autoclip, branding, captions, jobs, timing, ui_login, ui_theme
from .ui_common import finished_log, is_hosted, progress_panel, start_job, usual_time


def render():
    job = jobs.latest("youtube")
    with st.container(border=True, key="card_autoclip"):
        ui_theme.step("✂️", "Auto clips",
                      "Paste a YouTube video or a finished live stream and say how many clips. "
                      "The app finds the best moments - what viewers rewatched, where chat "
                      "exploded, the loudest reactions - and makes each one a Short with "
                      "captions.")
        url = st.text_input("YouTube link", key="ac_url",
                            placeholder="https://www.youtube.com/watch?v=...").strip()
        cols = st.columns(4, vertical_alignment="bottom")
        count = cols[0].number_input("How many clips", 1, 20, 5, key="ac_count")
        length = cols[1].segmented_control("Each", (15, 30, 45, 60), default=30,
                                           required=True, key="ac_len",
                                           format_func="{} s".format)
        style = cols[2].selectbox("Look", ("blur", "crop"), key="ac_style",
                                  format_func={"blur": "Blurred background",
                                               "crop": "Centre crop"}.get)
        with_captions = cols[3].toggle("Captions", value=captions.available(), key="ac_cap",
                                       disabled=not captions.available())
        cols = st.columns(3)
        use_chat = cols[0].toggle("Use the live chat (streams)", value=True, key="ac_chat",
                                  help="For past live streams: chat bursts mark big moments. "
                                       "Reading a long stream's chat takes a few minutes.")
        use_sound = cols[1].toggle("Use loud moments", value=True, key="ac_sound",
                                   help="Listens to the whole video for hype and reactions.")
        brand = cols[2].toggle("Branding", value=branding.settings()["enabled"], key="ac_brand")
        estimate = count * timing.per("autoclip_clip") + 60
        busy = bool(job and job.running)
        if ui_login.may_download("ac_sign_in") and st.button(
                "Make %d clip%s" % (count, "" if count == 1 else "s"), type="primary",
                icon=":material/auto_awesome:", width="stretch", key="ac_go",
                disabled=busy or "youtu" not in url):
            start_job("youtube", "Auto clips: %d from %s" % (count, url[-20:]),
                      lambda: autoclip.make_clips(url, int(count), int(length), style,
                                                  with_captions, brand, use_chat, use_sound),
                      estimate)
        if not busy:
            usual_time(estimate)
        st.caption("Works on videos and finished live streams (a stream still running: "
                   "wait until it ends). Make clips only of videos you own or may reuse.")
    if job and job.running:
        progress_panel("youtube", where="_ac")
    elif job and isinstance(job.result, dict) and "clips" in job.result:
        results(job)
    elif job and job.error and job.label.startswith("Auto clips"):
        st.error(job.error.strip().splitlines()[-1])
        finished_log(job)


def results(job):
    result = job.result
    clips = result["clips"]
    with st.container(border=True, key="card_autoclip_done"):
        ui_theme.step("✅", "%d clip%s from %s" % (len(clips), "" if len(clips) == 1 else "s",
                                                  result["video"]["title"][:60]),
                      result["folder"])
        if hasattr(os, "startfile") and not is_hosted() and Path(result["folder"]).exists():
            if st.button("Open the folder", icon=":material/folder_open:", key="ac_open"):
                os.startfile(result["folder"])
        columns = st.columns(3)
        for i, clip in enumerate(clips):
            with columns[i % 3]:
                if Path(clip["file"]).exists():
                    st.video(clip["file"])
                st.caption("#%d · %s · %s" % (i + 1, timing.clock(clip["start"]),
                                              ", ".join(clip["why"]) or "blend of signals"))
    finished_log(job)
