"""The Studio page: trim a clip into a Short, make a weekly compilation, and set
up branding (logo, hook line, intro and outro)."""

import os
from pathlib import Path

import streamlit as st

from . import (branding, captions, jobs, media, studio, timing, titles, ui_captions,
               ui_login, ui_theme)
from .ui_common import finished_log, is_hosted, progress_panel, start_job, usual_time
from .ui_downloader import STYLE_LABELS

PARTS = ["✂️ Trim & preview", "🎞️ Weekly compilation", "🎨 Branding & hooks", "💬 Captions"]
MAX_DOWNLOAD_MB = 200


def render(api):
    part = st.segmented_control("Studio", PARTS, default=PARTS[0], required=True,
                                key="studio_part", label_visibility="collapsed")
    if part == PARTS[0]:
        trim_box()
    elif part == PARTS[1]:
        compilation_box()
    elif part == PARTS[2]:
        branding_box()
    else:
        ui_captions.render()


def _label(row):
    return "%s · %s · %s views%s" % (row["streamer"], (row["title"] or "untitled")[:60],
                                     "{:,}".format(row["views"]),
                                     " · %s" % row["game"] if row["game"] else "")


def _deliver(path, key):
    """Show a finished video, with a way to get at the file."""
    st.video(str(path))
    cols = st.columns(2)
    if hasattr(os, "startfile") and not is_hosted():
        if cols[0].button("Open the folder", icon=":material/folder_open:", key=key + "_open",
                          width="stretch"):
            os.startfile(str(Path(path).parent))
    elif Path(path).stat().st_size < MAX_DOWNLOAD_MB * 1e6:
        cols[0].download_button("Download", Path(path).read_bytes(), file_name=Path(path).name,
                                mime="video/mp4", key=key + "_dl", width="stretch")
    notes = titles.read_notes(path)
    if notes:
        with st.expander("Title ideas, description and chapters", icon=":material/notes:"):
            st.code(notes, language=None)


# -- trim & preview --------------------------------------------------------------------
def trim_box():
    rows = studio.library()
    with st.container(border=True, key="card_trim"):
        ui_theme.step("✂️", "Trim & preview",
                      "Pick a clip you downloaded, keep only the best seconds, and make it a "
                      "Short - with captions and your branding if you like.")
        if not rows:
            ui_theme.note("No downloaded clips yet - grab some on the Clip downloader or the "
                          "Clip radar first.")
            return
        games = sorted({r["game"] for r in rows if r["game"]})
        cols = st.columns([1, 2])
        game = cols[0].selectbox("Game", ["All games"] + games, key="tr_game")
        shown = [r for r in rows if game == "All games" or r["game"] == game][:300]
        by_id = {r["id"]: r for r in shown}
        pick = cols[1].selectbox("Clip", list(by_id), format_func=lambda i: _label(by_id[i]),
                                 key="tr_clip")
        entry = by_id.get(pick)
        if entry is None:
            return
        if entry["has_file"]:
            st.video(str(entry["file"]))
            length = media.probe(entry["file"])["duration"] or 60.0
        elif entry["has_short"]:
            st.video(str(entry["short"]))
            st.caption("This is its Short - the 16:9 file was deleted after a Shorts-only run, "
                       "so it is fetched again from Twitch when you trim.")
            length = media.probe(entry["short"])["duration"] or 60.0
        else:
            st.caption("The files of this clip are gone - it is fetched again from Twitch.")
            length = 60.0
        length = round(max(length, 1.0), 1)
        start, end = st.slider("Keep", 0.0, length, (0.0, length), 0.5, key="tr_range_%s" % pick,
                               format="%.1f s")
        cols = st.columns(3, vertical_alignment="bottom")
        style = cols[0].selectbox("Look", list(STYLE_LABELS), format_func=STYLE_LABELS.get,
                                  key="tr_style")
        with_captions = cols[1].toggle("Captions", key="tr_captions",
                                       disabled=not captions.available())
        brand = cols[2].toggle("Branding", value=branding.settings()["enabled"], key="tr_brand",
                               help="Your logo, hook line, intro and outro - set them under "
                                    "Branding & hooks.")
        st.caption("%.1f seconds kept." % (end - start))
        if ui_login.may_download("tr_sign_in") and st.button(
                "Make this Short", type="primary", icon=":material/content_cut:",
                width="stretch", disabled=end - start < 1):
            with st.spinner("Making the Short - %s%s…" % (
                    timing.text(timing.estimate("trim")),
                    ", plus the captions" if with_captions else "")):
                path, problem = studio.trim_short(entry, start, end, style, with_captions, brand)
            if problem:
                st.error("Could not make it: %s" % problem)
            else:
                st.session_state["tr_result"] = str(path)
    result = st.session_state.get("tr_result")
    if result and Path(result).exists():
        with st.container(border=True, key="card_trim_out"):
            ui_theme.step("✅", "Your Short", result)
            _deliver(result, "tr_out")


# -- weekly compilation ----------------------------------------------------------------
def compilation_box():
    job = jobs.latest("studio")
    with st.container(border=True, key="card_comp"):
        ui_theme.step("🎞️", "Weekly compilation",
                      "The week's most viewed clips as one video: an on-screen credit on "
                      "every clip, and YouTube chapters plus credits to copy.")
        cols = st.columns(4, vertical_alignment="bottom")
        days = cols[0].segmented_control("From the last", (7, 14, 30), default=7, required=True,
                                         format_func="{} days".format, key="cp_days")
        rows = studio.library(days)
        games = sorted({r["game"] for r in rows if r["game"]})
        game = cols[1].selectbox("Game", ["All games"] + games, key="cp_game")
        top = cols[2].segmented_control("Clips", (5, 10, 15, 20), default=10, required=True,
                                        key="cp_top")
        countdown = cols[3].toggle("Countdown (best last)", value=True, key="cp_countdown")
        picked = studio.weekly_pick(days, top, None if game == "All games" else game)
        if not picked:
            ui_theme.note("No clips downloaded in the last %d days%s." % (
                days, "" if game == "All games" else " for %s" % game))
            return
        ui_theme.ranked([{
            "pos": i, "icon": "", "name": r["title"] or "untitled",
            "sub": "%s · %s%s" % (r["streamer"], r["game"],
                                  "" if r["has_file"] else " · fetched again from Twitch"),
            "value": "{:,}".format(r["views"]), "value_sub": "views"}
            for i, r in enumerate(picked, 1)])
        leave_out = st.multiselect("Leave out", [r["id"] for r in picked], key="cp_skip",
                                   format_func=lambda i: next(_label(r) for r in picked
                                                              if r["id"] == i))
        chosen = [r for r in picked if r["id"] not in leave_out]
        default = "Top %d %s clips of the week" % (len(chosen), game if game != "All games"
                                                    else "Twitch")
        cols = st.columns([3, 1], vertical_alignment="bottom")
        title = cols[0].text_input("Title", default, key="cp_title_%d_%s" % (len(chosen), game))
        brand = cols[1].toggle("Branding", value=branding.settings()["enabled"], key="cp_brand",
                               help="Your logo, and the 16:9 intro and outro.")
        busy = bool(job and job.running)
        if ui_login.may_download("cp_sign_in") and st.button(
                "Make the compilation (%d clips)" % len(chosen), type="primary",
                icon=":material/movie:", width="stretch", disabled=busy or len(chosen) < 2):
            start_job("studio", "Compilation: %s" % title,
                      lambda: studio.compilation(chosen, title, countdown, brand),
                      timing.estimate("compilation", len(chosen)))
        if not busy:
            usual_time(timing.estimate("compilation", len(chosen)))
        st.caption("YouTube shows chapters when there are at least 3, each 10 seconds or "
                   "longer - clips of 10 s and up do it.")
    if job and job.running:
        progress_panel("studio", where="_comp")
    elif job:
        if job.error:
            st.error(job.error.strip().splitlines()[-1])
        elif job.result:
            with st.container(border=True, key="card_comp_out"):
                ui_theme.step("✅", "Compilation ready", str(job.result["video"]))
                for skipped in job.result["skipped"]:
                    st.caption("Left out (could not be fetched): %s" % skipped)
                if Path(job.result["video"]).exists():
                    _deliver(job.result["video"], "cp_out")
        finished_log(job)


# -- branding --------------------------------------------------------------------------
def _uploaded_once(file, key):
    """True the first time this uploaded file is seen (uploads stay put across reruns)."""
    if file is None or st.session_state.get(key) == file.file_id:
        return False
    st.session_state[key] = file.file_id
    return True


def branding_box():
    config = branding.settings()
    can_edit = not ui_login.needs_login()
    with st.container(border=True, key="card_brand"):
        ui_theme.step("🎨", "Branding & hooks",
                      "Your logo on every Short, a hook line for the first seconds, and an "
                      "intro and outro - so every upload looks like your channel.")
        if not can_edit:
            st.caption("🔒 Sign in to change the branding.")
            return
        enabled = st.toggle("Use on every Short the app makes (downloads, radar, streamers, "
                            "Autopilot)", value=config["enabled"], key="br_on")
        if enabled != config["enabled"]:
            branding.save_settings(enabled=enabled)

        st.markdown("**Logo or watermark**")
        cols = st.columns([1, 2])
        logo = branding.logo_path(config)
        if logo:
            cols[0].image(str(logo), width=140)
            if cols[0].button("Remove logo", key="br_logo_rm"):
                branding.remove_logo()
                st.rerun()
        upload = cols[1].file_uploader("PNG with a see-through background works best",
                                       type=["png", "jpg", "jpeg", "webp"], key="br_logo_up")
        if _uploaded_once(upload, "br_logo_seen"):
            branding.save_logo(upload.getvalue(), Path(upload.name).suffix)
            st.rerun()
        cols = st.columns(3)
        position = cols[0].selectbox("Where", list(branding.POSITIONS),
                                     index=list(branding.POSITIONS).index(config["logo_pos"]),
                                     key="br_pos")
        size = cols[1].slider("Size (% of the width)", 6, 40, int(config["logo_size"]),
                              key="br_size")
        opacity = cols[2].slider("Opacity", 0.3, 1.0, float(config["logo_opacity"]), 0.05,
                                 key="br_opacity")

        st.markdown("**Hook line**")
        cols = st.columns([1, 3, 1.4], vertical_alignment="bottom")
        hook_on = cols[0].toggle("On", value=config["hook_on"], key="br_hook_on")
        hook_text = cols[1].text_input("Shown at the top for the first seconds",
                                       config["hook_text"], max_chars=40, key="br_hook_text")
        seconds = cols[2].slider("Seconds", 1.0, 6.0, float(config["hook_seconds"]), 0.5,
                                 key="br_hook_s")
        values = {"logo_pos": position, "logo_size": size, "logo_opacity": opacity,
                  "hook_on": hook_on, "hook_text": hook_text.strip(), "hook_seconds": seconds}
        if any(config.get(k) != v for k, v in values.items()):
            branding.save_settings(**values)

        st.markdown("**Intro and outro** (15 seconds at most; fitted to Shorts and to 16:9)")
        cols = st.columns(2)
        for col, kind in zip(cols, ("intro", "outro")):
            with col:
                made = branding.extra_path(kind, "v")
                if made and config[kind]:
                    st.video(str(made))
                    if st.button("Remove the %s" % kind, key="br_%s_rm" % kind):
                        branding.remove_extra(kind)
                        st.rerun()
                upload = st.file_uploader("%s video" % kind.title(),
                                          type=["mp4", "mov", "webm", "mkv"],
                                          key="br_%s_up" % kind)
                if _uploaded_once(upload, "br_%s_seen" % kind):
                    with st.spinner("Fitting the %s to both shapes…" % kind):
                        problem = branding.save_extra(kind, upload.getvalue(),
                                                      Path(upload.name).suffix)
                    (st.error(problem) if problem else st.rerun())
    preview_box()


def preview_box():
    rows = [r for r in studio.library() if r["has_file"]]
    if not rows:
        return
    with st.container(border=True, key="card_brand_preview"):
        ui_theme.step("👀", "Preview", "Six seconds of your newest clip with the branding on.")
        if st.button("Make a preview", icon=":material/visibility:", key="br_preview"):
            with st.spinner("Making the preview…"):
                path, problem = studio.trim_short(
                    rows[0], 0.0, min(6.0, media.probe(rows[0]["file"])["duration"] or 6.0),
                    "blur", False, True, target=studio.CACHE / "branding_preview.mp4")
            if problem:
                st.error(problem)
            else:
                st.session_state["br_preview_path"] = str(path)
        shown = st.session_state.get("br_preview_path")
        if shown and Path(shown).exists():
            st.video(shown)
