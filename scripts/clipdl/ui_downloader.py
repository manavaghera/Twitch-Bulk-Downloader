"""The Clip Downloader page: the same run as twitch_clip_downloader.py, as a form."""

import os

import streamlit as st

from . import captions, jobs, permissions, titles, ui_login, ui_theme
from .api import TwitchError
from .config import (DOWNLOAD_ROOT, MAX_CLIP_SECONDS, MAX_CLIPS, MIN_CLIP_SECONDS,
                     OUTPUT_FORMATS, QUALITIES, RANKINGS, SHORT_STYLES, STOP,
                     TIME_WINDOWS)
from .folders import (check_folder, game_folder, parse_folder, pick_folder, save_prefs,
                      saved_folder)
from .session import DownloadRequest, build_zip, known_clip_count, run_session
from .shorts import SHORTS_FOLDER, find_ffmpeg
from .ui_common import (box_arts, busy_elsewhere, finished_log, is_hosted, progress_panel,
                        start_job, twitch_client)
from .util import human_size

# The browser download hands the whole file over in one piece, so past this
# size the page points at the file on disk instead.
ZIP_LIMIT_BYTES = 2 * 1024 * 1024 * 1024

# Short button labels for the choices config.py spells out in full.
FORMAT_LABELS = {"video": "🎬 Video 16:9", "short": "📱 Short 9:16", "both": "✨ Both"}
FORMAT_NOTES = {
    "video": "Full frame, exactly as streamed. For YouTube videos and for editing.",
    "short": "1080×1920 vertical for YouTube Shorts, TikTok and Reels, saved in a Shorts "
             "folder. Converting takes about as long as the clip itself.",
    "both": "The 16:9 video plus a 1080×1920 vertical Short of it, in a Shorts sub-folder.",
}
STYLE_LABELS = {"blur": "Blurred background", "crop": "Centre crop",
                "split": "Facecam + gameplay"}
STREAMER_LABELS = {"not_blocked": "Skip 'Don't use'", "allowed_only": "Only 'Allowed'",
                   "any": "Anyone"}
STYLE_NOTES = {"split": "Streamer's camera on top, gameplay below - the classic clip look. "
                        "Uses the camera spot you mark per streamer on the Streamers page; "
                        "streamers without one get the blurred look.",
               "blur": "Whole frame kept, nothing cut off.",
               "crop": "Fills the screen; the sides are cut off."}
QUALITY_LABELS = {QUALITIES[0][1]: "Up to 1080p", QUALITIES[1][1]: "Maximum (up to 4K)"}
WINDOW_LABELS = {24: "24 hours", 24 * 7: "7 days", 24 * 30: "30 days"}
RANK_LABELS = {"views": "👁 Most views", "trending": "⚡ Trending now"}
LENGTH_DEFAULT = "%d–%d s" % (MIN_CLIP_SECONDS, MAX_CLIP_SECONDS)


@st.cache_data(ttl=300, show_spinner=False)
def top_categories(client_id, client_secret):
    """Twitch's top 100 right now, cached for five minutes."""
    api = twitch_client(client_id, client_secret)
    return [{"id": g["id"], "name": g["name"], "box_art_url": g.get("box_art_url", "")}
            for g in api.top_games(100)]


def trend_label(trend, rising):
    """How a trend research pick is named in the game list."""
    return "%s %s  (from trend research)" % ("🚀" if trend in rising else "🔥", trend.name)


def game_options(api):
    """[(label, game)]: trend research picks first, then Twitch's top categories."""
    options, seen = [], set()
    trend_job = jobs.latest("trends")
    if trend_job and trend_job.result:
        picks = trend_job.result["rising"][:25] + trend_job.result["popular"][:50]
        for trend in picks:
            if trend.id and trend.id not in seen:
                seen.add(trend.id)
                options.append((trend_label(trend, trend_job.result["rising"]),
                                {"id": trend.id, "name": trend.name}))
    try:
        for game in top_categories(api.client_id, api.client_secret):
            if game["id"] not in seen:
                seen.add(game["id"])
                options.append(("📺 %s" % game["name"], game))
    except TwitchError as error:
        st.warning("Could not load Twitch's top categories: %s" % error)
    return options


def choose_game(api):
    mode = st.segmented_control("Find a game", ("Suggested", "Search Twitch"),
                                default="Suggested", required=True, key="dl_game_mode",
                                label_visibility="collapsed")
    if mode == "Suggested":
        options = game_options(api)
        if not options:
            return None
        labels = [label for label, _ in options]
        if st.session_state.get("dl_pick") not in labels:
            st.session_state.pop("dl_pick", None)
        label = st.selectbox("Game / category", labels, key="dl_pick",
                             help="Picks from your trend research come first, then "
                                  "what is biggest on Twitch right now.")
        return dict(options)[label]

    query = st.text_input("Game / category name", placeholder="e.g. VALORANT",
                          icon=":material/search:")
    if not query.strip():
        return None
    try:
        exact = api.game_by_name(query.strip())
        matches = [exact] if exact else api.search_categories(query.strip(), 10)
    except TwitchError as error:
        st.error(str(error))
        return None
    if not matches:
        st.warning("Nothing on Twitch matches '%s'." % query)
        return None
    names = [m.get("name", "?") for m in matches]
    picked = st.selectbox("Closest categories on Twitch", names)
    return matches[names.index(picked)]


def _browse():
    """Browse button: open the system folder picker and put the answer in the box."""
    chosen = pick_folder(st.session_state.get("dl_folder", ""))
    if chosen:
        st.session_state["dl_folder"] = chosen


def _card(name):
    return st.container(border=True, key="card_%s" % name)


def form_game(api):
    with _card("game"):
        ui_theme.step(1, "Pick a game", "What should the clips be of?")
        game = choose_game(api)
        cols = st.columns([1, 1.3, 1.3])
        wanted = cols[0].number_input("How many clips", 1, MAX_CLIPS, 50, step=10)
        with cols[1]:
            hours = st.segmented_control("From the last", [h for _, h in TIME_WINDOWS],
                                         format_func=WINDOW_LABELS.get, default=24,
                                         required=True, key="dl_window")
        with cols[2]:
            ranking = st.segmented_control(
                "Rank by", [key for _, key in RANKINGS], format_func=RANK_LABELS.get,
                default="views", required=True, key="dl_rank",
                help="Trending = views per hour, so this morning's clip can beat a tired one "
                     "that has had all week. Reads far more clips first, so it is slower.")
    return game, int(wanted), hours, ranking


def form_filters(game):
    with _card("filters"):
        ui_theme.step(2, "Filter the clips", "Only English channels are ever kept.")
        cols = st.columns(2, gap="medium")
        with cols[0]:
            length = st.segmented_control("Clip length", (LENGTH_DEFAULT, "Any", "Custom"),
                                          default=LENGTH_DEFAULT, required=True,
                                          key="dl_length")
            if length == "Any":
                min_s, max_s = 0, 0
            elif length == "Custom":
                min_s, max_s = st.slider("Seconds", 1, 600, (MIN_CLIP_SECONDS, MAX_CLIP_SECONDS))
            else:
                min_s, max_s = MIN_CLIP_SECONDS, MAX_CLIP_SECONDS
            history_mode = "new"
            known = known_clip_count(game["name"]) if game else 0
            if known:
                choice = st.segmented_control(
                    "You already have %d clip(s) of %s" % (known, game["name"]),
                    ("New clips only", "Include them"), default="New clips only",
                    required=True, key="dl_history",
                    help="New clips only: all of them are clips you have never had. "
                         "Include them: re-downloads any whose file you have deleted.")
                history_mode = "new" if choice == "New clips only" else "include"
        with cols[1]:
            gameplay_only = st.toggle("Gameplay clips only", value=True,
                                      help="Skips caster desks, watch parties and chat clips.")
            top_up_lengths = st.toggle("Too few? Top up with other lengths")
            top_up_foreign = st.toggle("Still too few? Top up with non-English clips")
        marked = permissions.entries()
        streamer_mode = st.segmented_control(
            "Streamers (your permission list)", list(STREAMER_LABELS),
            format_func=STREAMER_LABELS.get, default="not_blocked", required=True,
            key="dl_streamers",
            help="Mark streamers Allowed / Don't use on the Streamers page. Reposting clips "
                 "without permission is how clip channels get copyright strikes.")
        ui_theme.note("Your list: <b>%d</b> allowed, <b>%d</b> marked don't use." % (
            sum(1 for e in marked if e.get("status") == "allowed"),
            sum(1 for e in marked if e.get("status") == "blocked")))
    return (min_s, max_s, gameplay_only, history_mode, top_up_lengths, top_up_foreign,
            streamer_mode)


def form_output():
    with _card("output"):
        ui_theme.step(3, "Format & quality", "Landscape videos, vertical Shorts, or both.")
        cols = st.columns(2, gap="medium")
        with cols[0]:
            output = st.segmented_control("Format", [key for _, key in OUTPUT_FORMATS],
                                          format_func=FORMAT_LABELS.get, default="video",
                                          required=True, key="dl_format")
            ui_theme.note(FORMAT_NOTES[output])
        with cols[1]:
            max_height = st.segmented_control(
                "Quality", [cap for _, cap in QUALITIES], format_func=QUALITY_LABELS.get,
                default=QUALITIES[0][1], required=True, key="dl_quality",
                help="Every clip comes down at the most it has, up to the cap. Twitch keeps a "
                     "clip at the resolution the stream was broadcast in, so clips above 1080p "
                     "only exist for streamers broadcasting in 1440p or 4K.")
        short_style, with_captions = "blur", False
        if output != "video":
            short_style = st.segmented_control(
                "How the 16:9 clip fills the 9:16 screen", [key for _, key in SHORT_STYLES],
                format_func=STYLE_LABELS.get, default="blur", required=True, key="dl_style")
            ui_theme.note(STYLE_NOTES[short_style])
            ready = captions.available()
            with_captions = st.toggle(
                "Burn in captions", value=False, key="dl_captions", disabled=not ready,
                help="Speech to text on this PC (free, no API): big word-by-word captions "
                     "in the lower third. About a second per clip.")
            if not ready:
                st.caption("Captions need one extra install: `.venv\\Scripts\\pip install "
                           "faster-whisper`, then restart the page.")
            if not find_ffmpeg():
                st.error("Shorts need ffmpeg, which is not installed. Run "
                         "`winget install Gyan.FFmpeg`, then restart this page.")
    return output, short_style, max_height, with_captions


def form_folder(game, output):
    hosted = is_hosted()
    if "dl_folder" not in st.session_state:
        st.session_state["dl_folder"], st.session_state["dl_per_game"] = saved_folder()
    with _card("folder"):
        ui_theme.step(4, "Where to save", "Your choice is remembered for next time.")
        if hosted:
            # On a server the folder is the server's, not yours: the .zip is the way out.
            root, per_game, problem = DOWNLOAD_ROOT, True, None
        else:
            cols = st.columns([5, 1], vertical_alignment="bottom")
            cols[0].text_input("Folder", key="dl_folder", icon=":material/folder:",
                               help="Created if it does not exist.")
            cols[1].button("Browse…", on_click=_browse, width="stretch",
                           disabled=ui_login.needs_login())
            per_game = st.toggle("Put each game in its own sub-folder", key="dl_per_game")
            root, problem = parse_folder(st.session_state["dl_folder"])
        as_zip = st.toggle("Also hand me one .zip to download", value=hosted, disabled=hosted,
                           help="Handy for moving a batch to another device.")
        target = game_folder(root, game["name"] if game else "<game>", per_game) if root else None
        if problem:
            st.error(problem)
        elif target:
            ui_theme.note("Files go to <b>%s</b>%s" % (
                ui_theme.escape(str(target)),
                " - Shorts in its <b>Shorts</b> sub-folder." if output != "video" else "."))
    return root, per_game, problem, as_zip, target, hosted


def render_form(api):
    left, right = st.container(key="dl_layout").columns([1.75, 1], gap="large")
    with left:
        game, wanted, hours, ranking = form_game(api)
        (min_s, max_s, gameplay_only, history_mode, top_up_lengths, top_up_foreign,
         streamer_mode) = form_filters(game)
        output, short_style, max_height, with_captions = form_output()
        root, per_game, problem, as_zip, target, hosted = form_folder(game, output)

    window_label = next(label for label, h in TIME_WINDOWS if h == hours)
    ranking_label = next(label for label, key in RANKINGS if key == ranking)
    noun = {"video": "clips", "short": "Shorts", "both": "clips + Shorts"}[output]
    other = busy_elsewhere("download")
    job = jobs.latest("download")
    no_ffmpeg = output != "video" and not find_ffmpeg()

    with right:
        with _card("summary"):
            art = ""
            if game:
                art = game.get("box_art_url") or box_arts(api, [game.get("id")]).get(game.get("id"))
            length = ("any length" if not max_s else "%d–%d s" % (min_s, max_s))
            ui_theme.summary(
                game["name"] if game else "No game picked yet",
                ui_theme.box_art(art),
                "Your run" if game else "Choose one in step 1",
                [("Clips", "%d, %s" % (wanted, RANK_LABELS[ranking].split(" ", 1)[1].lower())),
                 ("From", "the last " + WINDOW_LABELS[hours]),
                 ("Length", length + (", gameplay only" if gameplay_only else "")),
                 ("Output", "%s · %s" % (FORMAT_LABELS[output].split(" ", 1)[1],
                                         QUALITY_LABELS[max_height])),
                 ("Extras", ", ".join(x for x in (
                     STYLE_LABELS[short_style].lower() if output != "video" else "",
                     "captions" if with_captions else "", "title ideas (.txt)") if x)),
                 ("Streamers", STREAMER_LABELS[streamer_mode]),
                 ("Save to", str(target) if target else "-"),
                 ("Delivery", "folder + .zip" if as_zip else "folder")])
            locked = ui_login.needs_login()
            if locked:
                go = False
                if st.button("Sign in to download", type="primary", icon=":material/lock:",
                             width="stretch", key="dl_sign_in"):
                    ui_login.download_dialog()
            else:
                go = st.button("Download %d %s" % (wanted, noun), type="primary",
                               icon=":material/download:", width="stretch", disabled=(
                                   game is None or bool(other) or bool(job and job.running)
                                   or bool(problem) or no_ffmpeg))
            if locked:
                st.caption("🔒 Downloads are for approved accounts only.")
            elif other:
                st.caption("⏳ %s is downloading - this can start when it is done." % other)
            elif game is None:
                st.caption("Pick a game to enable the download.")
            else:
                st.caption("English channels only · talking clips and repeats of the same "
                           "moment skipped · clips you already have are never fetched twice.")
        if job and job.running:
            progress_panel("download", can_cancel=not locked)

    if not go:
        return
    if not hosted:
        # Now, and only now, make sure the folder can really be written to.
        root, problem = check_folder(st.session_state["dl_folder"])
        if problem:
            st.error(problem)
            return
        save_prefs(download_folder=str(root), per_game_folder=per_game)

    request = DownloadRequest(game, wanted, (window_label, hours), (ranking_label, ranking),
                              min_s, max_s, gameplay_only, history_mode, output, short_style,
                              max_height, root, per_game, captions=with_captions,
                              streamer_mode=streamer_mode)
    answers = {"lengths": top_up_lengths, "non_english": top_up_foreign}

    def work():
        result = run_session(api, request, lambda _q, kind: answers[kind])
        if as_zip and result.code == 0 and not STOP.is_set():
            build_zip(result)
        return result

    start_job("download", "%s, %d %s" % (game["name"], wanted, noun), work)


def render_result(result):
    if result is None or not result.jobs:
        return
    rows, total_bytes = [], 0
    for job in result.jobs:
        status = result.manifest.status_of(job.clip_id) if result.manifest else None
        video = job.path if job.path.exists() else job.existing
        video = video if video is not None and video.exists() else None
        short = getattr(job, "short", None)
        short = short if short is not None and short.exists() else None
        total_bytes += (video.stat().st_size if video else 0) + \
            (short.stat().st_size if short else 0)
        row = {"#": job.index, "Streamer": job.streamer, "Title": job.title,
               "Views": job.views, "Status": status or "not started",
               "Title idea": titles.suggest(job.title, job.streamer, job.game_name,
                                            clip_url=job.url)["titles"][0]}
        if result.request.output != "short":
            row["Video"] = human_size(video.stat().st_size) if video else ""
        if result.request.output != "video":
            row["Short"] = human_size(short.stat().st_size) if short else ""
        row["Clip"] = job.url
        rows.append(row)
    done = sum(1 for r in rows if r["Status"] in ("downloaded", "skipped-exists"))
    failed = sum(1 for r in rows if r["Status"] == "failed")

    cols = st.columns(4)
    cols[0].metric("Clips ready", "%d / %d" % (done, len(rows)))
    cols[1].metric("New downloads", sum(1 for r in rows if r["Status"] == "downloaded"))
    cols[2].metric("Failed", failed)
    cols[3].metric("On disk", human_size(total_bytes))

    actions = st.container(horizontal=True)
    if result.zip_path and result.zip_path.exists():
        size = result.zip_path.stat().st_size
        if size <= ZIP_LIMIT_BYTES:
            with open(result.zip_path, "rb") as handle:
                actions.download_button("Save the .zip (%s)" % human_size(size), handle,
                                        file_name=result.zip_path.name, type="primary",
                                        mime="application/zip", icon=":material/folder_zip:")
        else:
            st.warning("The .zip is %s - too big to hand to a browser in one piece. It is "
                       "saved at %s." % (human_size(size), result.zip_path))
    if not is_hosted() and hasattr(os, "startfile") and result.folder.exists():
        if result.request.output != "short" and actions.button(
                "Open the clips folder", icon=":material/folder_open:"):
            os.startfile(str(result.folder))
        shorts = result.folder / SHORTS_FOLDER
        if result.request.output != "video" and shorts.exists() and \
                actions.button("Open the Shorts folder", icon=":material/smartphone:"):
            os.startfile(str(shorts))
    ui_theme.note("Saved to <b>%s</b>" % ui_theme.escape(str(result.folder)))

    st.dataframe(rows, hide_index=True, width="stretch",
                 column_config={"Clip": st.column_config.LinkColumn(display_text="open ↗"),
                                "Views": st.column_config.NumberColumn(format="localized"),
                                "Title": st.column_config.TextColumn(width="large")})


def render(api):
    if api is None:
        ui_theme.empty_state("🔌", "Connect to Twitch first",
                             "Paste your Twitch app credentials in the sidebar to start.")
        return
    render_form(api)

    job = jobs.latest("download")
    if job is None or job.running or ui_login.needs_login():
        return
    st.space("medium")
    with _card("result"):
        if job.error:
            st.error("The download stopped: %s" % job.error, icon=":material/error:")
        elif job.cancelled:
            st.warning("Cancelled. Clips that finished are kept; the rest can be fetched "
                       "next run.", icon=":material/pause_circle:")
        else:
            st.success("Done: %s" % job.label, icon=":material/check_circle:")
        render_result(job.result)
        finished_log(job)
