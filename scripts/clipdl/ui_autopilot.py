"""The Autopilot page: a daily folder of ready-to-post Shorts, set up once."""

import os
import time
from datetime import datetime
from datetime import time as clock

import streamlit as st

from . import autopilot, captions, jobs, permissions, ui_login, ui_theme
from .ui_common import busy_elsewhere, is_hosted, progress_panel, start_job
from .ui_downloader import STYLE_LABELS, STREAMER_LABELS

OUTPUTS = {"short": "Shorts only", "both": "Shorts + the 16:9 videos", "video": "Videos only"}


def render(api):
    if api is None:
        ui_theme.empty_state("🔌", "Connect to Twitch first",
                             "Paste your Twitch app credentials in the sidebar to start.")
        return
    config = autopilot.settings()
    with st.container(border=True, key="card_ap_settings"):
        ui_theme.step("🤖", "Autopilot",
                      "Set it up once: every day it picks games, downloads their best new "
                      "clips, turns them into Shorts and writes title ideas - a folder ready "
                      "to post.")
        source = st.radio("Which clips", list(autopilot.SOURCES),
                          index=list(autopilot.SOURCES).index(config["source"]),
                          format_func=autopilot.SOURCES.get, key="ap_source", horizontal=True)
        cols = st.columns(3, vertical_alignment="bottom")
        games, game_list, clips_per_game, radar_clips = (config["games"], config["game_list"],
                                                         config["clips_per_game"],
                                                         config["radar_clips"])
        if source in ("rising", "mine"):
            games = cols[0].number_input("How many games", 1, 10, config["games"], key="ap_games")
        elif source == "list":
            text = cols[0].text_input("Games (comma separated)", ", ".join(config["game_list"]),
                                      key="ap_list", placeholder="VALORANT, Minecraft")
            game_list = [g.strip() for g in text.split(",") if g.strip()]
        if source == "radar":
            radar_clips = cols[1].number_input("Clips in total", 1, 100, config["radar_clips"],
                                               key="ap_radar")
        else:
            clips_per_game = cols[1].number_input("Clips per game", 1, 50,
                                                  config["clips_per_game"], key="ap_per_game")
        hours = cols[2].segmented_control("From the last", (24, 48, 168), required=True,
                                          default=config["hours"], key="ap_hours",
                                          format_func=lambda h: "%d hours" % h if h < 168
                                          else "7 days")
        cols = st.columns(3, vertical_alignment="bottom")
        output = cols[0].selectbox("Make", list(OUTPUTS), index=list(OUTPUTS).index(
            config["output"]), format_func=OUTPUTS.get, key="ap_output")
        style = cols[1].selectbox("Shorts look", list(STYLE_LABELS), format_func=STYLE_LABELS.get,
                                  index=list(STYLE_LABELS).index(config["style"]),
                                  key="ap_style", disabled=output == "video")
        with_captions = cols[2].toggle("Captions", value=config["captions"] and
                                       captions.available(), key="ap_captions",
                                       disabled=output == "video" or not captions.available())
        streamer_mode = st.segmented_control(
            "Streamers", list(STREAMER_LABELS), format_func=STREAMER_LABELS.get,
            default=config["streamer_mode"], required=True, key="ap_streamers")
        allowed = sum(1 for e in permissions.entries() if e.get("status") == "allowed")
        if streamer_mode == "allowed_only" and not allowed:
            st.warning("No streamer is marked Allowed yet - mark some on the Streamers page, "
                       "or the autopilot will find nothing.")
        values = {"source": source, "games": int(games), "game_list": game_list,
                  "clips_per_game": int(clips_per_game), "radar_clips": int(radar_clips),
                  "hours": hours, "output": output, "style": style, "captions": with_captions,
                  "streamer_mode": streamer_mode}
        changed = any(config.get(k) != v for k, v in values.items())
        if st.button("Save settings", disabled=not changed, icon=":material/save:"):
            autopilot.save_settings(values)
            st.toast("Saved - the next run uses these.")
            st.rerun()
        if changed:
            st.caption("Unsaved changes - a scheduled run uses the saved settings.")

    run_now(api, values)
    if not is_hosted():
        schedule_box(config)
    last_box()


def run_now(api, values):
    job = jobs.latest("download")
    other = busy_elsewhere("download")
    busy = bool(other) or bool(job and job.running)
    if ui_login.may_download("ap_sign_in") and st.button(
            "Run the autopilot now", type="primary", icon=":material/play_arrow:",
            width="stretch", disabled=busy):
        start_job("download", "Autopilot run", lambda: autopilot.run(api, values))
    if other:
        st.caption("⏳ %s is downloading - this can start when it is done." % other)
    if job and job.running and job.label == "Autopilot run":
        progress_panel("download", where="_ap")


def schedule_box(config):
    with st.container(border=True, key="card_ap_schedule"):
        ui_theme.step("⏰", "Every day, by itself",
                      "Adds the autopilot to Windows Task Scheduler. The PC has to be on (and "
                      "you signed in) at that time; the web page does not need to be open.")
        current = autopilot.scheduled()
        hour, minute = (int(x) for x in config.get("time", "09:00").split(":"))
        cols = st.columns([1, 1, 1], vertical_alignment="bottom")
        at = cols[0].time_input("Run at", clock(hour, minute), key="ap_time", step=900)
        if cols[1].button("Schedule daily" if not current else "Change time",
                          icon=":material/schedule:", width="stretch"):
            ok, message = autopilot.schedule(at.strftime("%H:%M"))
            (st.success if ok else st.error)(message)
        if current and cols[2].button("Stop the daily run", width="stretch"):
            ok, message = autopilot.unschedule()
            (st.success if ok else st.error)(message)
            current = None
        if current:
            ui_theme.note("✅ Scheduled. Next run: <b>%s</b> · last run: %s · status: %s" % (
                ui_theme.escape(current["next"]), ui_theme.escape(current["last"]),
                ui_theme.escape(current["status"])))
        else:
            ui_theme.note("Not scheduled yet.")


def last_box():
    last = autopilot.last_run()
    if not last:
        return
    with st.container(border=True, key="card_ap_last"):
        ui_theme.step("📦", "Last run", time.strftime("%a %d %b %H:%M",
                                                     time.localtime(last["started"])))
        ui_theme.kpis([("New clips", str(autopilot.total(last)), None, None),
                       ("Took", "%d min" % (last.get("seconds", 0) // 60), None, None),
                       ("Games", str(len(last.get("games", []))) if last.get("games") else "-",
                        None, None)])
        if last.get("games"):
            ui_theme.mini_list([("", name, "%d new" % n) for name, n in last["games"]])
        for error in last.get("errors", []):
            st.warning(error)
        folder = last.get("folder")
        ui_theme.note("Saved in <b>%s</b> - each video has a .txt with title ideas." %
                      ui_theme.escape(folder or "?"))
        if folder and hasattr(os, "startfile") and not is_hosted() and os.path.isdir(folder):
            if st.button("Open the folder", icon=":material/folder_open:", key="ap_open"):
                os.startfile(folder)
        log = autopilot.DATA_DIR / "autopilot.log"
        if log.exists():
            with st.expander("Scheduled-run log", icon=":material/terminal:"):
                st.code(log.read_text(encoding="utf-8", errors="replace")[-4000:],
                        language=None)
                st.caption("Last written %s" % datetime.fromtimestamp(
                    log.stat().st_mtime).strftime("%d %b %H:%M"))
