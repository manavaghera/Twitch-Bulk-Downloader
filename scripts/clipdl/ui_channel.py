"""The My channel page: connect your YouTube channel, see which games and
streamers do best on it, and upload Shorts with their title files."""

import time
from datetime import datetime, timedelta
from datetime import time as clock

import pandas as pd
import streamlit as st

from . import (jobs, mychannel, studio, timing, ui_login, ui_theme, uploader, yt_quota,
               ytauth)
from .ui_common import finished_log, is_hosted, progress_panel, start_job, usual_time

GUIDE = "https://console.cloud.google.com/apis/credentials"
AUDIT = "https://support.google.com/youtube/contact/yt_api_form"


def render(api):
    if is_hosted():
        ui_theme.empty_state("💻", "On your own PC only",
                             "Connecting your channel signs in through this computer's browser, "
                             "so it works where the app runs locally.")
        return
    if not ytauth.connected():
        connect_box()
        return
    results_box()
    upload_box()
    with st.expander("Connection", icon=":material/link:"):
        st.caption("Connected to YouTube. Disconnect removes the permission from this PC and "
                   "asks Google to forget it.")
        if not ui_login.needs_login() and st.button("Disconnect", key="yt_disconnect"):
            ytauth.disconnect()
            st.rerun()


# -- connecting ------------------------------------------------------------------------
def connect_box():
    with st.container(border=True, key="card_yt_connect"):
        ui_theme.step("📺", "Connect your YouTube channel",
                      "See which games and streamers do best on your channel, feed that to the "
                      "Autopilot, and upload Shorts with their titles and hashtags.")
        if ui_login.needs_login():
            st.caption("🔒 Sign in to connect a channel.")
            return
        st.markdown(
            "1. In [Google Cloud](%s) - the project of your YouTube API key: **OAuth consent "
            "screen** → External → add your Google account as a **test user**.\n"
            "2. **Credentials → Create credentials → OAuth client ID → Desktop app**, then "
            "**Download JSON**.\n"
            "3. Drop that file here, then press **Connect** and allow access in the browser."
            % GUIDE)
        upload = st.file_uploader("OAuth client JSON", type=["json"], key="yt_client")
        if upload is not None and st.session_state.get("yt_client_seen") != upload.file_id:
            st.session_state["yt_client_seen"] = upload.file_id
            problem = ytauth.save_client(upload.getvalue().decode("utf-8", "replace"))
            (st.error(problem) if problem else st.success("Client saved."))
        if ytauth.client() is None:
            return
        if st.button("Connect", type="primary", icon=":material/login:", key="yt_connect"):
            try:
                ytauth.start_connect()
            except ValueError as error:
                st.error(str(error))
        sign_in_status()


@st.fragment(run_every=2.0)
def sign_in_status():
    state = ytauth.status()
    if state["status"] == "waiting":
        st.info("Waiting for you to allow access in the browser tab that opened…")
        st.link_button("Open Google's sign-in again", state["url"])
    elif state["status"] == "failed":
        st.error("Not connected: %s" % state["error"])
    elif state["status"] == "connected" or ytauth.connected():
        st.success("Connected.")
        st.rerun(scope="app")


# -- results ---------------------------------------------------------------------------
def results_box():
    data = mychannel.load()
    with st.container(border=True, key="card_yt_results"):
        ui_theme.step("🏆", "What works on your channel",
                      "Your Shorts grouped by game and by streamer - so you post more of what "
                      "your viewers watch.")
        cols = st.columns([1, 2], vertical_alignment="center")
        if cols[0].button("Refresh from YouTube", icon=":material/refresh:", key="yt_refresh",
                          width="stretch"):
            with st.spinner("Reading your uploads…"):
                try:
                    data = mychannel.fetch()
                except ValueError as error:
                    st.error(str(error))
        if not data:
            cols[1].caption("Press Refresh to read your channel (a few quota units).")
            return
        cols[1].caption("Read %s ago." % _ago(data["fetched_at"]))
        channel = data["channel"]
        shorts = [v for v in data["videos"] if v["short"]]
        ui_theme.kpis([("Channel", channel["title"], None, None),
                       ("Subscribers", "{:,}".format(channel["subscribers"]), None, None),
                       ("Shorts", "{:,}".format(len(shorts)), None,
                        "of %d uploads read" % len(data["videos"])),
                       ("Median Short views", "{:,}".format(int(pd.Series(
                           [v["views"] for v in shorts] or [0]).median())), None, None)])
        by = st.segmented_control("Group by", ("game", "streamer"), default="game",
                                  required=True, key="yt_by", format_func=str.title)
        rows = mychannel.results(data, by)
        if not rows:
            st.info("No Short could be matched to a %s yet. Shorts made here carry the clip "
                    "link in their description - upload those and they are recognised." % by)
            return
        best = rows[0]["median"] or 1
        ui_theme.ranked([{
            "pos": i, "icon": "", "name": r["name"],
            "sub": "%d Short%s · best: %s" % (r["videos"], "" if r["videos"] == 1 else "s",
                                              r["best"][:60]),
            "value": "{:,}".format(int(r["median"])), "value_sub": "median views",
            "bar": r["median"] / best * 100} for i, r in enumerate(rows[:25], 1)], wide=True)
        unknown = mychannel.unknown_share(data)
        if unknown:
            st.caption("%.0f%% of your Shorts could not be matched to a game (made elsewhere, or "
                       "no clip link in the description)." % (unknown * 100))
        st.caption("Tip: the Autopilot can pick \"Games that do best on my channel\".")


def _ago(stamp):
    minutes = (time.time() - stamp) / 60
    return "%d min" % minutes if minutes < 120 else "%.1f h" % (minutes / 60)


# -- uploads ---------------------------------------------------------------------------
def upload_box():
    job = jobs.latest("upload")
    done = uploader.uploaded_clips()
    ready = [r for r in studio.library() if r["has_short"] and r["id"] not in done][:40]
    with st.container(border=True, key="card_yt_upload"):
        ui_theme.step("⬆️", "Upload helper",
                      "Your Shorts with their title ideas, description and hashtags - now, or "
                      "one every few hours.")
        st.warning("Google keeps everything uploaded through a new API project **private** until "
                   "the project passes YouTube's [API audit](%s). Until then, uploads land as "
                   "private videos you can check in YouTube Studio." % AUDIT,
                   icon=":material/info:")
        if not ready:
            ui_theme.note("No Shorts waiting - every Short made here is uploaded already.")
        else:
            table = pd.DataFrame([{
                "Upload": False, "Title": (uploader.read_sidecar(r["short"])["titles"] or
                                           [r["title"]])[0],
                "Streamer": r["streamer"], "Game": r["game"], "Views on Twitch": r["views"],
                "id": r["id"]} for r in ready])
            edited = st.data_editor(table, hide_index=True, width="stretch", key="yt_queue",
                                    disabled=["Streamer", "Game", "Views on Twitch", "id"],
                                    column_config={"id": None, "Title": st.column_config
                                                   .TextColumn(max_chars=100)})
            chosen = edited[edited["Upload"]]
            cols = st.columns(4, vertical_alignment="bottom")
            privacy = cols[0].selectbox("Visibility", list(uploader.PRIVACY), key="yt_privacy",
                                        format_func=uploader.PRIVACY.get)
            every = cols[1].selectbox("One every", (0, 2, 4, 6, 12, 24), index=3,
                                      key="yt_every", format_func=lambda h: "all now" if not h
                                      else "%d hours" % h)
            day = cols[2].date_input("From", datetime.now().date(), key="yt_day",
                                     disabled=not every)
            at = cols[3].time_input("at", clock(18, 0), key="yt_time", step=1800,
                                    disabled=not every)
            need = len(chosen) * uploader.COST
            st.caption("%d chosen · needs %s units · %s left today (about %d uploads)." % (
                len(chosen), "{:,}".format(need), "{:,}".format(yt_quota.remaining()),
                yt_quota.remaining() // uploader.COST))
            first = datetime.combine(day, at).astimezone() if every else None
            if first and first < datetime.now().astimezone() + timedelta(minutes=15):
                st.caption("The first slot is too soon - YouTube needs it at least 15 minutes "
                           "ahead; it will be pushed back.")
                first = datetime.now().astimezone() + timedelta(minutes=20)
            if ui_login.may_download("yt_up_sign_in") and st.button(
                    "Upload %d Short%s" % (len(chosen), "" if len(chosen) == 1 else "s"),
                    type="primary", icon=":material/upload:", width="stretch",
                    disabled=chosen.empty or bool(job and job.running)
                    or not yt_quota.can_spend(uploader.COST)):
                by_id = {r["id"]: r for r in ready}
                items = []
                for _i, row in chosen.iterrows():
                    entry = by_id[row["id"]]
                    notes = uploader.read_sidecar(entry["short"])
                    items.append({"path": entry["short"], "title": row["Title"],
                                  "description": notes["description"],
                                  "tags": notes["hashtags"] + ["shorts"],
                                  "meta": {"clip_id": entry["id"], "game": entry["game"],
                                           "streamer": entry["streamer"]}})
                start_job("upload", "Uploading %d Shorts" % len(items),
                          lambda: uploader.upload_all(items, privacy, first, every),
                          timing.estimate("upload", len(items)))
            if not chosen.empty:
                usual_time(timing.estimate("upload", len(chosen)))
    if job and job.running:
        progress_panel("upload", where="_yt")
    elif job:
        finished_log(job)
