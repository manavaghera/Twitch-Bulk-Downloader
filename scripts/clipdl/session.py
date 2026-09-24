"""One download run, with every choice already made.

The terminal menus (cli.py) and the web page (ui_downloader.py) both collect
the same answers and hand them here, so the two can never drift apart. The
only questions this still asks are the two "top up with ...?" offers that
depend on what the search found; they go through `confirm`, which the terminal
answers by asking and the web page answers from the boxes ticked up front.
"""

import time
import zipfile
from datetime import datetime, timezone

from .api import TwitchError
from .config import DATA_DIR, DOWNLOAD_ROOT, MANIFEST_FILE, MAX_HEIGHT, STOP, VOD_FILE
from . import shortfall, timing, titles
from .download import build_jobs, next_free_number, run_downloads
from .filter import classify_clip, clip_age_hours, resolve_stream_titles
from .folders import game_folder
from .manifest import Manifest
from .report import report, report_shortfall
from .shorts import SHORTS_FOLDER, convert_all, find_ffmpeg
from .util import human_size, load_json, sanitize, save_json, say

EXPORTS_DIR = DATA_DIR / "exports"      # zips built for the web page


class DownloadRequest:
    """Everything the menus ask, in one place."""

    def __init__(self, game, wanted, window, ranking, min_seconds, max_seconds,
                 gameplay_only=True, history_mode="new", output="video",
                 short_style="blur", max_height=MAX_HEIGHT, save_root=None,
                 per_game=True, captions=False, streamer_mode="not_blocked", min_height=0):
        self.game = game                        # a Twitch category dict
        self.wanted = wanted
        self.window_label, self.window_hours = window
        self.ranking_label, self.ranking = ranking
        self.min_seconds, self.max_seconds = min_seconds, max_seconds
        self.gameplay_only = gameplay_only
        self.history_mode = history_mode        # "new" or "include"
        self.output = output                    # "video", "short" or "both"
        self.short_style = short_style          # "blur" or "crop"
        # A minimum above the cap would throw away the very clips it asks for.
        self.min_height = min_height            # the least a clip must have; 0 = any
        self.max_height = 0 if min_height > (max_height or 0) > 0 else max_height
        self.save_root = save_root or DOWNLOAD_ROOT
        self.per_game = per_game                # a sub-folder per game?
        self.captions = captions                # burn captions into the Shorts?
        self.streamer_mode = streamer_mode      # permissions.MODES key

    @property
    def folder(self):
        return game_folder(self.save_root, self.game_name, self.per_game)

    @property
    def game_name(self):
        return self.game.get("name", "?")


class DownloadResult:
    """What a run did, for the web page to show. `code` is the exit code."""

    def __init__(self, code, folder=None, jobs=(), manifest=None, request=None):
        self.code = code
        self.folder = folder
        self.jobs = list(jobs)
        self.manifest = manifest
        self.request = request
        self.zip_path = None        # set by build_zip()

    def files(self):
        """[(path, name inside a zip)] of everything this run delivered."""
        found = []
        for job in self.jobs:
            if self.manifest.status_of(job.clip_id) not in ("downloaded", "skipped-exists"):
                continue
            video = job.path if job.path.exists() else job.existing
            if video is not None and video.exists():
                found.append((video, video.name))
            short = getattr(job, "short", None)
            if short is not None and short.exists():
                found.append((short, "%s/%s" % (SHORTS_FOLDER, short.name)))
        return found


def build_zip(result):
    """Pack this run's clips into data/exports/<game>_<time>.zip for the browser.

    Stored, not compressed: the clips are already compressed video, so
    squeezing them again saves almost nothing and takes minutes. Zips older
    than a day are cleared out first so the folder does not fill the disk.
    """
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    for old in EXPORTS_DIR.glob("*.zip"):
        if time.time() - old.stat().st_mtime > 24 * 3600:
            old.unlink(missing_ok=True)
    files = result.files()
    if not files:
        return None
    name = "%s_%s.zip" % (sanitize(result.folder.name, 40).replace(" ", "_"),
                          datetime.now().strftime("%Y%m%d-%H%M"))
    target = EXPORTS_DIR / name
    say("")
    say("Packing %d file(s) into %s..." % (len(files), name))
    with zipfile.ZipFile(target, "w", zipfile.ZIP_STORED, allowZip64=True) as archive:
        for path, inside in files:
            archive.write(path, inside)
    say("Zip ready : %s (%s)" % (target, human_size(target.stat().st_size)))
    result.zip_path = target
    return target


def known_clip_count(game_name):
    """How many clips of this game earlier runs already downloaded."""
    return len(Manifest(MANIFEST_FILE).known_ids(game_name))


def run_session(api, request, confirm):
    """Search, ask how to fill any shortfall, then download.

    `confirm(question, kind)` -> bool is asked once for each way to fill a
    shortfall that would add clips - kind "older", "lengths", "non_english" or
    "talking" - so a caller that decided in advance can answer without reading.
    """
    if not ffmpeg_ready(request):
        return DownloadResult(1, request=request)
    searching = time.monotonic()
    plan = shortfall.find(api, request)
    if plan is None:
        return DownloadResult(1, request=request)
    plan.started = searching
    fills = []
    for key, _label, _count in plan.options():
        if len(plan.choose(fills, quiet=True)) >= request.wanted:
            break
        if confirm(plan.question(key), key):
            fills.append(key)
    return download_plan(api, plan, fills)


def ffmpeg_ready(request):
    if request.output in ("short", "both") and not find_ffmpeg():
        say("")
        say("Making Shorts needs ffmpeg, and it is not installed.")
        say("  Windows:  winget install Gyan.FFmpeg   (then open a new window)")
        say("  or:       pip install imageio-ffmpeg")
        return False
    return True


def download_plan(api, plan, fills=()):
    """Download what a search found plus the chosen fills (see shortfall.py)."""
    request, manifest, buckets = plan.request, plan.manifest, plan.buckets
    wanted, game_name, rank_key = request.wanted, request.game_name, plan.rank_key
    chosen = plan.choose(fills)
    if not chosen:
        say("")
        say("Nothing to download. Try a longer time window or a different game.")
        return DownloadResult(1, request=request)

    # The file numbers are a chart: 001 leads the run and the rest follow it
    # down. The buckets are already in order, but the gameplay filter draws
    # from two of them, so a clip that only made the cut as filler could
    # otherwise sit above a bigger one.
    chosen.sort(key=rank_key, reverse=True)

    folder = request.folder
    # A "new clips only" run sits beside files it is not touching, so its
    # numbers carry on from the last batch instead of restarting at 001.
    start_index = next_free_number(folder) if request.history_mode == "new" else 1
    jobs = build_jobs(chosen, folder, game_name, start_index)

    say("")
    top = chosen[0]
    views = "{:,}".format(top.get("view_count") or 0)
    if request.ranking == "trending":
        hours = clip_age_hours(top, datetime.now(timezone.utc)) or 0.0
        say("Ranking  : trending first - %03d is %s, %s views in %.1f hours"
            % (start_index, top.get("broadcaster_name", "?"), views, hours))
    else:
        say("Ranking  : most-viewed first - %03d is %s, %s views"
            % (start_index, top.get("broadcaster_name", "?"), views))
    if start_index > 1:
        say("Numbering: this batch is %03d-%03d, continuing from the last run."
            % (start_index, start_index + len(chosen) - 1))
    say("Saving to: %s" % folder)
    started_monotonic = time.monotonic()
    if plan.started:
        timing.record("search", started_monotonic - plan.started)
    try:
        run_downloads(jobs, manifest, request.max_height)
        convert_all(jobs, manifest, request.output, request.short_style,
                    with_captions=request.captions)
        write_sidecars(jobs, manifest, request.game, api)
    except KeyboardInterrupt:
        STOP.set()
        manifest.flush()
        say("")
        say("Stopped by Ctrl+C.")
    report(jobs, manifest, folder, started_monotonic, request.max_height)
    report_shortfall(wanted, chosen, buckets, game_name, request.window_label,
                     request.gameplay_only, request.history_mode)
    return DownloadResult(0, folder, jobs, manifest, request)


def trending_tags(game_name, limit=5):
    """The tags streamers of this game use right now, from the stats collector."""
    try:
        from . import stats_db
        from .web import normalize_name
        rows = stats_db.query("SELECT term, SUM(channels) FROM game_terms WHERE game_key = ?"
                              " AND term LIKE '#%' GROUP BY term ORDER BY 2 DESC LIMIT ?",
                              (normalize_name(game_name), limit * 3))
    except Exception:           # no stats recorded yet: titles do without
        return []
    return [term[1:] for term, _c in rows if "drop" not in term.lower()][:limit]


def channel_logins(api, jobs):
    """{broadcaster id: login} for the channels whose login the clip itself does
    not give away (display names in another script) - one Twitch call per 100."""
    wanted = sorted({job.broadcaster_id for job in jobs if job.broadcaster_id
                     and not titles.login_from(job.url, job.streamer)})
    logins = {}
    for start in range(0, len(wanted) if api is not None else 0, 100):
        try:
            users = api.get("/users", {"id": wanted[start:start + 100]}).get("data") or []
        except TwitchError:
            break                           # the .txt simply leaves the channel link out
        logins.update({u["id"]: u["login"] for u in users if u.get("login")})
    return logins


def write_sidecars(jobs, manifest, game, api=None):
    """A .txt of title options, a description and hashtags beside every file made."""
    from .web import normalize_name
    game_name = (game or {}).get("name", "")
    jobs = [job for job in jobs
            if manifest.status_of(job.clip_id) in ("downloaded", "skipped-exists")]
    logins = channel_logins(api, jobs)
    tags_of, written = {}, 0            # each game's trending tags, looked up once
    for job in jobs:
        name = job.game_name or game_name
        if name not in tags_of:
            tags_of[name] = trending_tags(name) if name else []
        suggestion = titles.suggest(job.title, job.streamer, name, normalize_name(name),
                                    job.url, tags_of[name], logins.get(job.broadcaster_id))
        for path in {job.path, getattr(job, "short", None)} - {None}:
            if path.exists() and titles.write_sidecar(path, suggestion):
                written += 1
    if written:
        say("Titles    : title ideas, a description and hashtags saved beside %d file(s) "
            "(.txt)" % written)


def keep_gameplay(clips, api=None):
    """The clips minus the talking ones (caster desks, chatting, reactions),
    judged like a normal run: the clip title plus the title of the stream it
    came from, when `api` can look that up."""
    vod_cache = load_json(VOD_FILE, {})
    vod_cache = vod_cache if isinstance(vod_cache, dict) else {}
    if api is not None:
        try:
            if resolve_stream_titles(api, clips, vod_cache):
                save_json(VOD_FILE, vod_cache)
        except TwitchError:
            pass                            # judge by the clip titles alone
    kept = []
    for clip in clips:
        kind, reason = classify_clip(clip, vod_cache.get(clip.get("video_id") or "", ""))
        if kind == "talk":
            say("  Skipped - talking, not gameplay (%s): %s" % (
                reason or "title", (clip.get("title") or "")[:60]))
        else:
            kept.append(clip)
    return kept


def download_clips(clips, folder, output="video", short_style="blur", captions=False,
                   max_height=MAX_HEIGHT, label="clips", api=None, gameplay_only=True,
                   limit=None):
    """Download a hand-picked list of clips (from the clip radar, a streamer's page
    or the autopilot) into `folder`, then make Shorts and title files like a run.

    Each clip dict needs Twitch's clip fields plus "game_name". With
    `gameplay_only` the talking clips are left out first, like in a normal run;
    `limit` then keeps the first that many. Returns a DownloadResult. Clips
    already downloaded before are fetched again only if their file is gone.
    """
    if output in ("short", "both") and not find_ffmpeg():
        say("Making Shorts needs ffmpeg, and it is not installed.")
        return DownloadResult(1)
    if gameplay_only:
        before = len(clips)
        clips = keep_gameplay(clips, api)
        if len(clips) < before:
            say("Left out %d talking clip(s) of %d." % (before - len(clips), before))
    clips = clips[:limit] if limit else clips
    if not clips:
        say("Nothing left to download.")
        return DownloadResult(0)
    manifest = Manifest(MANIFEST_FILE)
    folder.mkdir(parents=True, exist_ok=True)
    jobs = build_jobs(clips, folder, label, next_free_number(folder))
    for job, clip in zip(jobs, clips):
        job.game_name = clip.get("game_name") or label
    say("")
    say("Downloading %d hand-picked clip(s) to %s" % (len(jobs), folder))
    started = time.monotonic()
    try:
        run_downloads(jobs, manifest, max_height)
        convert_all(jobs, manifest, output, short_style, with_captions=captions)
        write_sidecars(jobs, manifest, None, api)
    except KeyboardInterrupt:
        STOP.set()
        manifest.flush()
    report(jobs, manifest, folder, started, max_height)
    request = DownloadRequest({"name": label}, len(clips), ("picked", 0), ("picked", "views"),
                              0, 0, output=output, short_style=short_style,
                              max_height=max_height, save_root=folder.parent, per_game=False,
                              captions=captions)
    return DownloadResult(0, folder, jobs, manifest, request)

