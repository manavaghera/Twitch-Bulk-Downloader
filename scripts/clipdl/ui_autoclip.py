"""YouTube > Auto clips: a link and a number in, that many ready Shorts out."""

import os
from pathlib import Path

import streamlit as st

from . import autoclip, branding, captions, jobs, judge, timing, ui_login, ui_theme
from .ui_common import finished_log, is_hosted, progress_panel, start_job, usual_time


@st.cache_data(ttl=60, show_spinner=False)
def _ai_model():
    """The local AI model's name, or None - asked once a minute, not on every redraw."""
    return judge.model()


def render():
    job = jobs.latest("youtube")
    from . import ytdl
    problem = ytdl.cookie_problem()
    if problem:
        st.warning("Going on without cookies: " + problem + " (Change it on the Download "
                   "page, under cookies.)", icon=":material/cookie:")
    with st.container(border=True, key="card_autoclip"):
        ui_theme.step("✂️", "Auto clips",
                      "Paste a YouTube video or a finished live stream and say how many clips. "
                      "The app finds plays and laughs - kills, clutches and aces (VALORANT), "
                      "hype and jokes in what is said, chat going wild, what viewers "
                      "rewatched - not just loud talking, and makes each one a Short with "
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
        cols = st.columns(5)
        listen = cols[0].toggle("Listen for highlight words", value=captions.available(),
                                key="ac_listen", disabled=not captions.available(),
                                help="Transcribes the video and looks for \"ace\", "
                                     "\"clutch\", \"let's go\", \"clip it\"... - what "
                                     "tells a real play from loud talking. On the graphics "
                                     "card: a few minutes for hours of stream.")
        watch = cols[1].toggle("Watch the screen", value=True, key="ac_watch",
                               help="A small copy of the video shows where the picture "
                                    "moves (menus, lobbies and talking count for less) and, "
                                    "in VALORANT, every kill you get - so 3Ks, 4Ks, aces and "
                                    "clutches come first.")
        use_chat = cols[2].toggle("Live chat (streams)", value=True, key="ac_chat",
                                  help="Where the chat went off - fast, hyped or laughing "
                                       "(\"KEKW\", \"W\", \"clip it\"). Read while the rest "
                                       "is worked out; a few minutes for a long stream.")
        ai = _ai_model()
        ask_ai = cols[3].toggle("AI second opinion", value=bool(ai), key="ac_ai",
                                disabled=not ai,
                                help=("%s (on this computer, through Ollama) reads what was "
                                      "said in each moment and rates it: a joke or a big play "
                                      "beats callouts and small talk." % ai) if ai else
                                     "Install Ollama (ollama.com), run \"ollama pull "
                                     "llama3\" and keep it running: a free AI on this "
                                     "computer then rates each moment.")
        brand = cols[4].toggle("Branding", value=branding.settings()["enabled"], key="ac_brand")
        words = st.text_input("Your own highlight words (optional, commas)", key="ac_words",
                              placeholder="e.g. ace, spike, vandal, op, flawless")
        extra_words = [w.strip() for w in words.split(",") if w.strip()]
        estimate = count * timing.per("autoclip_clip") + 60 + (count * 12 if ask_ai else 0)
        busy = bool(job and job.running)
        if ui_login.may_download("ac_sign_in") and st.button(
                "Make %d clip%s" % (count, "" if count == 1 else "s"), type="primary",
                icon=":material/auto_awesome:", width="stretch", key="ac_go",
                disabled=busy or "youtu" not in url):
            start_job("youtube", "Auto clips: %d from %s" % (count, url[-20:]),
                      lambda: autoclip.make_clips(url, int(count), int(length), style,
                                                  with_captions, brand, use_chat, listen, watch,
                                                  extra_words, ask_ai=ask_ai),
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
