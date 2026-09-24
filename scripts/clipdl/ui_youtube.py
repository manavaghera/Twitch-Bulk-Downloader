"""The YouTube page: paste links - videos, Shorts, live streams - check what
they are, and download up to 4K (or record a live stream)."""

import os
import sys
from datetime import datetime
from pathlib import Path

import streamlit as st

from . import jobs, timing, ui_login, ui_theme, ytdl
from .folders import saved_folder
from .ui_common import finished_log, is_hosted, progress_panel, start_job, usual_time
from .util import human_size

QUALITY = {2160: "Best, up to 4K", 1440: "1440p", 1080: "1080p", 720: "720p", 480: "480p",
           360: "360p", 0: "Audio only (MP3)"}
LIVE_MODES = {"now": "Record from now - stop any time, the recording is kept",
              "start": "From the very start (only while YouTube still has it; runs to the end)"}
LIVE_BADGES = {"is_live": ("🔴 LIVE", "cs-warn"), "is_upcoming": ("⏰ not started", "cs-move"),
               "was_live": ("recorded stream", "cs-move"), "post_live": ("stream ended - "
                                                                           "processing", "cs-move")}


@st.cache_data(ttl=600, show_spinner=False)
def _inspect(url, cookies):
    try:
        return ytdl.inspect(url), None
    except ValueError as error:
        return None, str(error)


def _clock(seconds):
    return timing.clock(seconds) if seconds else "?"


PARTS = ["⬇️ Download", "✂️ Auto clips"]


def render(api=None):
    part = st.segmented_control("YouTube", PARTS, default=PARTS[0], required=True,
                                key="ytd_part", label_visibility="collapsed")
    if part == PARTS[1]:
        from . import ui_autoclip
        ui_autoclip.render()
        return
    job = jobs.latest("youtube")
    with st.container(border=True, key="card_yt_links"):
        ui_theme.step("▶️", "YouTube videos & live streams",
                      "Paste links - videos, Shorts or live streams. Check them, pick the "
                      "quality (up to 4K), and download. A live stream is recorded.")
        text = st.text_area("YouTube links (one per line)", key="ytd_links", height=100,
                            placeholder="https://www.youtube.com/watch?v=...\n"
                                        "https://youtu.be/...  or a /live/ or /shorts/ link")
        links = [line.strip() for line in text.splitlines() if line.strip()][:20]
        if st.button("Check the links", icon=":material/search:", key="ytd_check",
                     disabled=not links):
            with st.spinner("Asking YouTube about %d link%s…" % (len(links),
                                                                "" if len(links) == 1 else "s")):
                st.session_state["ytd_items"] = [
                    (link,) + _inspect(link, ytdl.settings()["cookies"]) for link in links]
        if not ytdl.js_runtime():
            st.warning("Install Node.js (nodejs.org) - YouTube's 4K links need it to be "
                       "unlocked, and without it downloads can fail or crawl.",
                       icon=":material/warning:")
    problem = ytdl.cookie_problem()
    if problem:
        st.warning("Going on without cookies: " + problem, icon=":material/cookie:")
    checked = st.session_state.get("ytd_items") or []
    items = [item for _link, item, _problem in checked if item]
    for link, item, problem in checked:
        if problem:
            st.error("%s - %s" % (link[:80], problem), icon=":material/error:")
    if items:
        choose_and_download(items, job)
    if job and job.running:
        progress_panel("youtube", where="_ytd")
    elif job and isinstance(job.result, list):
        finished(job)
    cookies_box()
    recent()


def choose_and_download(items, job):
    with st.container(border=True, key="card_yt_items"):
        best = max((item["best"] for item in items), default=0)
        ui_theme.ranked([{
            "pos": i, "icon": item["thumbnail"], "name": item["title"],
            "sub": "%s · %s%s" % (item["channel"] or "?",
                                  "live" if item["live"] == "is_live" else _clock(item["duration"]),
                                  " · also has 8K" if item["above_4k"] else ""),
            "chips": [LIVE_BADGES[item["live"]]] if item["live"] in LIVE_BADGES else [],
            "value": ("%dp" % item["best"]) if item["best"] else "-",
            "value_sub": "best available"} for i, item in enumerate(items, 1)], wide=True)
        cols = st.columns([1.2, 1], vertical_alignment="bottom")
        # A live or scheduled stream does not tell its qualities yet: offer them all.
        live_any = any(item["live"] in ("is_live", "is_upcoming") for item in items)
        cap = ytdl.MAX_HEIGHT if live_any or not best else best
        options = [h for h in QUALITY if h == 0 or h <= max(cap, 360)]
        height = cols[0].selectbox("Quality", options, format_func=QUALITY.get, key="ytd_q",
                                   help="The best each video has, up to this. 4K on YouTube "
                                        "is VP9 video: it plays in browsers, VLC and the "
                                        "Windows player.")
        h264 = cols[1].toggle("Make it play everywhere (H.264)", key="ytd_h264",
                              disabled=height == 0,
                              help="Converts VP9/AV1 to H.264 after downloading, on your "
                                   "graphics card - for old players, phones and editors.")
        live_mode = "now"
        if live_any:
            live_mode = st.radio("Live streams", list(LIVE_MODES), format_func=LIVE_MODES.get,
                                 key="ytd_live")
            if any(item["live"] == "is_upcoming" for item in items):
                st.caption("Streams that have not started are waited for, then recorded.")
        folder = Path(saved_folder()[0]) / "YouTube"
        total = sum(ytdl.size_at(item, height or 360) if height else item["audio_bytes"]
                    for item in items)
        known = all(item["live"] not in ("is_live", "is_upcoming") for item in items)
        ui_theme.note("%d video%s · %s · saved in <b>%s</b>" % (
            len(items), "" if len(items) == 1 else "s",
            ("about %s" % human_size(total)) if total and known else "size unknown (live)",
            ui_theme.escape(str(folder))))
        estimate = timing.estimate("youtube_mb", total / 1e6) if total and known else None
        busy = bool(job and job.running)
        if ui_login.may_download("ytd_sign_in") and st.button(
                "Download %d video%s" % (len(items), "" if len(items) == 1 else "s")
                if known else "Download / record", type="primary", width="stretch",
                icon=":material/download:", disabled=busy, key="ytd_go"):
            start_job("youtube", "YouTube: %s" % (items[0]["title"][:40] if len(items) == 1
                                                   else "%d videos" % len(items)),
                      lambda: ytdl.download_all(items, height or ytdl.MAX_HEIGHT, folder,
                                                audio_only=height == 0, h264=h264,
                                                live_mode=live_mode), estimate)
        if estimate and not busy:
            usual_time(estimate)
        st.caption("Download only videos you own or have permission to use - YouTube's "
                   "terms do not allow downloading other people's videos.")


def finished(job):
    results = job.result if isinstance(job.result, list) else []
    for item, path, problem in results:
        if path and Path(path).exists():
            with st.container(border=True, key="card_ytd_%s" % item["id"]):
                ui_theme.step("✅", item["title"], "%s · %s" % (
                    Path(path).name, human_size(Path(path).stat().st_size)))
                cols = st.columns(2)
                if hasattr(os, "startfile") and not is_hosted():
                    if cols[0].button("Open the folder", icon=":material/folder_open:",
                                      key="ytd_open_%s" % item["id"], width="stretch"):
                        os.startfile(str(Path(path).parent))
                elif Path(path).stat().st_size < 500e6:
                    cols[0].download_button("Download", Path(path).read_bytes(),
                                            file_name=Path(path).name,
                                            key="ytd_dl_%s" % item["id"], width="stretch")
        elif problem:
            st.warning("%s - %s" % (item["title"], problem), icon=":material/warning:")
    if job.error:
        st.error(job.error.strip().splitlines()[-1])
    finished_log(job)


def cookies_box():
    config = ytdl.settings()
    with st.expander("YouTube says \"confirm you're not a bot\" or \"sign in\"?",
                     icon=":material/cookie:"):
        st.markdown(
            "YouTube sometimes blocks downloads from a PC it does not recognise, and members-"
            "only or age-restricted videos need an account. Cookies from a browser where you "
            "are **signed in to YouTube** fix both. They are your login session: they stay in "
            "`data/` on this PC, and a second Google account is the safer choice.")
        if ui_login.needs_login():
            st.caption("🔒 Sign in to change this.")
            return
        choices = ["none", "file"] + ["browser:%s" % b for b in ytdl.BROWSERS]
        labels = {"none": "No cookies", "file": "A cookies.txt file"}
        labels.update({"browser:%s" % b: "From %s" % b.title() for b in ytdl.BROWSERS})
        if config["cookies"] not in choices:
            old = config["cookies"].split(":", 1)[-1].title()
            ytdl.save_settings(cookies="none")
            config = ytdl.settings()
            st.info("Your cookie choice (%s) cannot work on this PC, so it is now \"No "
                    "cookies\". Pick Firefox or upload a cookies.txt if YouTube asks."
                    % old, icon=":material/info:")
        choice = st.radio("Cookies", choices, index=choices.index(config["cookies"])
                          if config["cookies"] in choices else 0, format_func=labels.get,
                          key="ytd_cookies", horizontal=True)
        if sys.platform == "win32":
            st.caption("Chrome, Edge and Brave keep their cookies locked on Windows - for "
                       "them, export a cookies.txt with the \"Get cookies.txt LOCALLY\" "
                       "extension on youtube.com and upload it here.")
        if choice == "file":
            upload = st.file_uploader("cookies.txt (Netscape format, e.g. from the \"Get "
                                      "cookies.txt LOCALLY\" browser extension)", type=["txt"],
                                      key="ytd_cookie_file")
            if upload is not None and st.session_state.get("ytd_cookie_seen") != upload.file_id:
                st.session_state["ytd_cookie_seen"] = upload.file_id
                problem = ytdl.save_cookie_file(upload.getvalue())
                (st.error(problem) if problem else st.success("Saved."))
            if ytdl.COOKIE_FILE.exists():
                st.caption("A cookies.txt is saved.")
                if st.button("Delete the saved cookies", key="ytd_cookie_rm"):
                    ytdl.remove_cookies()
                    st.rerun()
        elif choice != config["cookies"]:
            ytdl.save_settings(cookies=choice)
            _inspect.clear()
        if choice == "file" and config["cookies"] != "file" and ytdl.COOKIE_FILE.exists():
            ytdl.save_settings(cookies="file")
            _inspect.clear()


def recent():
    rows = ytdl.history(10)
    if not rows:
        return
    with st.expander("Downloaded before (%d)" % len(rows), icon=":material/history:"):
        st.dataframe([{"When": datetime.fromtimestamp(r["when"]).strftime("%d %b %H:%M"),
                       "Title": r["title"], "Quality": "%sp" % r["height"] if r.get("height")
                       else "audio", "File": r["file"]} for r in rows],
                     hide_index=True, width="stretch")
