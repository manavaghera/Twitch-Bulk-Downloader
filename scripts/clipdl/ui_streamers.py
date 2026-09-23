"""The Streamers page: the channels you clip, their permission, their camera spot."""

from datetime import date, datetime, timezone
from pathlib import Path

import requests
import streamlit as st

from . import captions, facecams, jobs, permissions, ui_login, ui_theme, watchlist
from .api import TwitchError
from .folders import saved_folder
from .session import download_clips
from .ui_common import busy_elsewhere, progress_panel, start_job
from .ui_downloader import STYLE_LABELS
from .util import sanitize

STATUS_CHIPS = {"allowed": ("✅ Allowed", "cs-good"), "ask": ("❔ Not sure", "cs-move"),
                "blocked": ("⛔ Don't use", "cs-warn")}


def _fmt(value):
    value = float(value or 0)
    for size, suffix in ((1e6, "M"), (1e3, "K")):
        if value >= size:
            return ("%.1f" % (value / size)).rstrip("0").rstrip(".") + suffix
    return "%d" % value


# -- Twitch reads, cached ------------------------------------------------------------
@st.cache_data(ttl=120, show_spinner=False)
def _live(client_id, secret, ids):
    from .ui_common import twitch_client
    api = twitch_client(client_id, secret)
    return watchlist.live_now(api, ids), watchlist.last_played(api, ids)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def _usual(client_id, secret, user_id):
    from .ui_common import twitch_client
    return watchlist.usual_hours(twitch_client(client_id, secret), user_id)


@st.cache_data(ttl=3600, show_spinner=False)
def _schedule(client_id, secret, user_id):
    from .ui_common import twitch_client
    return watchlist.schedule(twitch_client(client_id, secret), user_id)


@st.cache_data(ttl=600, show_spinner=False)
def _clips(client_id, secret, user_id):
    from .ui_common import twitch_client
    return watchlist.best_clips(twitch_client(client_id, secret), user_id)


@st.cache_data(ttl=3600, show_spinner=False)
def _image(url):
    return requests.get(url, timeout=15).content


def render(api):
    if api is None:
        ui_theme.empty_state("🔌", "Connect to Twitch first",
                             "Paste your Twitch app credentials in the sidebar to start.")
        return
    with st.container(border=True, key="card_follow"):
        ui_theme.step("👀", "Streamers you clip",
                      "Follow the channels whose clips pull views: see when they go live, "
                      "grab their best clips, and keep their clip permission on record.")
        cols = st.columns([3, 1], vertical_alignment="bottom")
        name = cols[0].text_input("Twitch channel name or link", key="st_new",
                                  placeholder="e.g. xqc or twitch.tv/xqc")
        if cols[1].button("Follow", type="primary", width="stretch", icon=":material/add:"):
            try:
                entry = watchlist.follow(api, name)
                st.toast("Following %s" % entry["name"])
                st.rerun()
            except (ValueError, TwitchError) as error:
                st.error(str(error))
    followed = watchlist.entries()
    if not followed:
        ui_theme.empty_state("👀", "No streamers yet", "Follow a channel above - the clip "
                                                        "radar and every game page show whose "
                                                        "clips get watched.")
    else:
        ids = tuple(e["id"] for e in followed)
        live, last = _live(api.client_id, api.client_secret, ids)
        on_air = sum(1 for i in ids if i in live)
        ui_theme.insights([("🔴", "<b>%d</b> of your %d streamers are live right now."
                                  % (on_air, len(followed)))], title="Right now")
        for entry in sorted(followed, key=lambda e: (e["id"] not in live, e["name"].lower())):
            streamer_card(api, entry, live.get(entry["id"]), last.get(entry["id"]))
    permission_list()


def streamer_card(api, entry, stream, last):
    user_id, name = entry["id"], entry["name"]
    status = permissions.status_of(name, user_id)
    box = facecams.region(name, user_id)
    with st.container(border=True, key="card_st_%s" % user_id):
        chips = [("🔴 LIVE", "cs-warn") if stream else ("offline", "cs-move")]
        chips.append(STATUS_CHIPS.get(status, ("permission not set", "cs-move")))
        chips.append(("🎥 camera marked", "cs-good") if box else ("camera not marked", "cs-move"))
        if stream:
            story = "Live now in <b>%s</b> with <b>%s</b> viewers - %s" % (
                ui_theme.escape(stream.get("game_name") or "?"),
                _fmt(stream.get("viewer_count")), ui_theme.escape(stream.get("title") or ""))
        else:
            story = "Last streamed <b>%s</b>." % ui_theme.escape(last[0]) if last and last[0] \
                else "Not live."
        pattern = _usual(api.client_id, api.client_secret, user_id)
        usual = watchlist.usual_text(pattern)
        if usual:
            story += "<br>🕒 %s." % ui_theme.escape(usual.capitalize())
        upcoming = _schedule(api.client_id, api.client_secret, user_id)
        if upcoming:
            start, title, game = upcoming[0]
            story += "<br>📅 Next on their schedule: <b>%s</b> %s%s" % (
                start.strftime("%a %d %b %H:%M"), ui_theme.escape(title),
                (" (%s)" % ui_theme.escape(game)) if game else "")
        ui_theme.game_hero(entry.get("avatar"), name, story, chips)
        cols = st.columns([2.2, 1, 1], vertical_alignment="bottom")
        choice = cols[0].segmented_control(
            "Clip permission", list(permissions.STATUSES), format_func=permissions.STATUSES.get,
            default=status, key="perm_%s" % user_id,
            help="Check their channel page / panels: many say whether clip channels are "
                 "welcome. Downloads skip streamers marked Don't use.")
        if choice and choice != status:
            permissions.set_status(name, choice, user_id)
            st.rerun()
        editing = cols[1].toggle("Mark camera", key="cam_edit_%s" % user_id)
        if cols[2].button("Unfollow", key="unf_%s" % user_id, width="stretch"):
            watchlist.unfollow(user_id)
            st.rerun()
        clips = _clips(api.client_id, api.client_secret, user_id)
        if editing:
            camera_editor(entry, clips, box)
        with st.expander("Best clips this week (%d)" % len(clips), icon=":material/movie:"):
            clip_list(api, entry, clips)


def camera_editor(entry, clips, box):
    user_id, name = entry["id"], entry["name"]
    thumbs = [c.get("thumbnail_url") for c in clips if c.get("thumbnail_url")]
    if not thumbs:
        st.info("No clips this week to take a still from - try again after they stream.")
        return
    key = "cam_%s" % user_id
    parts = ("_x", "_y", "_w", "_h")
    if key + "_x" not in st.session_state:          # start from the saved spot or a preset
        for part, value in zip(parts, box or facecams.PRESETS[0][1:]):
            st.session_state[key + part] = int(round(value * 100))
    presets = st.columns(len(facecams.PRESETS))
    for col, (label, *preset) in zip(presets, facecams.PRESETS):
        if col.button(label, key="%s_%s" % (key, label), width="stretch"):
            for part, value in zip(parts, preset):     # before the sliders are drawn
                st.session_state[key + part] = int(round(value * 100))
    cols = st.columns(4)
    x = cols[0].slider("Left %", 0, 95, key=key + "_x") / 100
    y = cols[1].slider("Top %", 0, 95, key=key + "_y") / 100
    w = cols[2].slider("Width %", 5, 100, key=key + "_w") / 100
    h = cols[3].slider("Height %", 5, 100, key=key + "_h") / 100
    marked, short = facecams.preview(_image(thumbs[0]), (x, y, w, h))
    left, right = st.columns([2.2, 1])
    left.image(marked, caption="Purple box = the camera. Move it over the webcam.")
    right.image(short, caption="The Short it makes")
    if st.button("Save camera spot", type="primary", key=key + "_save"):
        facecams.save(name, (x, y, w, h), user_id)
        st.toast("Saved - %s's Shorts can now use the facecam look." % name)
        st.rerun()


def clip_list(api, entry, clips):
    if not clips:
        st.caption("No clips in the last 7 days.")
        return
    best = clips[0].get("view_count") or 1
    ui_theme.ranked([{
        "pos": i, "icon": c.get("thumbnail_url") or "", "name": c.get("title") or "untitled",
        "url": c.get("url"), "sub": "%s · %s views · %s" % (
            c.get("game_name") or "?", _fmt(c.get("view_count")),
            _age(c.get("created_at"))),
        "value": _fmt(c.get("view_count")), "value_sub": "views",
        "bar": (c.get("view_count") or 0) / best * 100}
        for i, c in enumerate(clips[:10], 1)], wide=True)
    cols = st.columns([1, 1.4, 1, 1], vertical_alignment="bottom")
    count = cols[0].segmented_control("Grab the top", (3, 5, 10), default=5, required=True,
                                      key="grab_n_%s" % entry["id"])
    style = cols[1].selectbox("As Shorts, look", list(STYLE_LABELS), format_func=STYLE_LABELS.get,
                              index=list(STYLE_LABELS).index("split") if facecams.region(
                                  entry["name"], entry["id"]) else 0,
                              key="grab_style_%s" % entry["id"])
    with_captions = cols[2].toggle("Captions", key="grab_cap_%s" % entry["id"],
                                   disabled=not captions.available())
    gameplay = cols[3].toggle("Gameplay only", value=True, key="grab_play_%s" % entry["id"],
                              help="Skips clips of chatting or reacting and takes the next "
                                   "best instead. Turn off for Just Chatting streamers.")
    job = jobs.latest("download")
    busy = bool(busy_elsewhere("download")) or bool(job and job.running)
    if ui_login.may_download("grab_sign_%s" % entry["id"]) and st.button(
            "Download %s's top %d as Shorts" % (entry["name"], count), key="grab_%s" % entry["id"],
            icon=":material/download:", disabled=busy, width="stretch"):
        folder = Path(saved_folder()[0]) / sanitize(entry["name"]) / date.today().isoformat()
        start_job("download", "%s, top %d clips" % (entry["name"], count),
                  lambda: download_clips(list(clips), folder, "short", style, with_captions,
                                         label=entry["name"], api=api, gameplay_only=gameplay,
                                         limit=count))
    if job and job.running and job.label.startswith(entry["name"]):
        progress_panel("download", where="_st_%s" % entry["id"])


def _age(stamp):
    try:
        made = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return ""
    hours = (datetime.now(timezone.utc) - made).total_seconds() / 3600
    return "%dh ago" % hours if hours < 48 else "%dd ago" % (hours / 24)


def permission_list():
    with st.expander("Permission list - every streamer you have marked",
                     icon=":material/verified_user:"):
        st.caption("Also for streamers you don't follow. Downloads can skip anyone marked "
                   "Don't use, or use only the ones marked Allowed.")
        cols = st.columns([2, 1.6, 0.8], vertical_alignment="bottom")
        name = cols[0].text_input("Streamer name", key="perm_new_name")
        status = cols[1].selectbox("Status", list(permissions.STATUSES),
                                   format_func=permissions.STATUSES.get, key="perm_new_status")
        if cols[2].button("Save", key="perm_new_save", width="stretch") and name.strip():
            permissions.set_status(name.strip(), status)
            st.rerun()
        rows = permissions.entries()
        if rows:
            st.dataframe([{"Streamer": r.get("name"), "Status": permissions.STATUSES.get(
                r.get("status"), r.get("status")), "Marked": datetime.fromtimestamp(
                r.get("updated") or 0).strftime("%d %b %Y")} for r in rows],
                hide_index=True, width="stretch")
            drop = st.selectbox("Remove from the list", [""] + [r.get("name") for r in rows],
                                key="perm_drop")
            if drop and st.button("Remove %s" % drop, key="perm_drop_go"):
                entry = next(r for r in rows if r.get("name") == drop)
                permissions.remove(drop, entry.get("id"))
                st.rerun()
