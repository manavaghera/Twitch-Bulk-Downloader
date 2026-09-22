"""One download run, with every choice already made.

The terminal menus (cli.py) and the web page (ui_downloader.py) both collect
the same answers and hand them here, so the two can never drift apart. The
only questions this still asks are the two "top up with ...?" offers that
depend on what the search found; they go through `confirm`, which the terminal
answers by asking and the web page answers from the boxes ticked up front.
"""

import time
import zipfile
from datetime import datetime, timedelta, timezone

from .config import (DATA_DIR, DOWNLOAD_ROOT, LANG_FILE, MANIFEST_FILE, MAX_HEIGHT,
                     OUTPUT_FORMATS, SHORT_STYLES, STOP, TARGET_LANGUAGE,
                     VELOCITY_MIN_SCAN, VELOCITY_OVERSCAN, quality_text)
from .download import build_jobs, next_free_number, run_downloads
from .filter import clip_age_hours, collect_clips, ranker
from .folders import game_folder
from .manifest import Manifest
from .report import report, report_filtered, report_shortfall
from .shorts import SHORTS_FOLDER, convert_all, find_ffmpeg
from .util import human_size, load_json, sanitize, say

EXPORTS_DIR = DATA_DIR / "exports"      # zips built for the web page


class DownloadRequest:
    """Everything the menus ask, in one place."""

    def __init__(self, game, wanted, window, ranking, min_seconds, max_seconds,
                 gameplay_only=True, history_mode="new", output="video",
                 short_style="blur", max_height=MAX_HEIGHT, save_root=None,
                 per_game=True):
        self.game = game                        # a Twitch category dict
        self.wanted = wanted
        self.window_label, self.window_hours = window
        self.ranking_label, self.ranking = ranking
        self.min_seconds, self.max_seconds = min_seconds, max_seconds
        self.gameplay_only = gameplay_only
        self.history_mode = history_mode        # "new" or "include"
        self.output = output                    # "video", "short" or "both"
        self.short_style = short_style          # "blur" or "crop"
        self.max_height = max_height            # 1080, or 0 for no cap
        self.save_root = save_root or DOWNLOAD_ROOT
        self.per_game = per_game                # a sub-folder per game?

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


def offer_out_of_range(chosen, buckets, missing, request, rank_key, confirm):
    """Offer the clips held back for being the wrong length. Returns how many were added."""
    if not buckets.out_of_range or missing <= 0:
        return 0

    say("There are %d clip(s) outside %d-%d seconds: %d shorter, %d longer."
        % (len(buckets.out_of_range), request.min_seconds, request.max_seconds,
           buckets.too_short, buckets.too_long))
    if not confirm("Top up the remaining %d with those?" % missing, "lengths"):
        return 0

    buckets.out_of_range.sort(key=rank_key, reverse=True)
    filler = buckets.out_of_range[:missing]
    chosen.extend(filler)
    say("Added %d clip(s) of other lengths." % len(filler))
    return len(filler)


def run_session(api, request, confirm):
    """Search, filter, rank and download. `confirm(question, kind)` -> bool.

    `kind` is "lengths" or "non_english", so a caller that decided in advance
    can answer without reading the question.
    """
    wanted, game_name = request.wanted, request.game_name
    if request.output in ("short", "both") and not find_ffmpeg():
        say("")
        say("Making Shorts needs ffmpeg, and it is not installed.")
        say("  Windows:  winget install Gyan.FFmpeg   (then open a new window)")
        say("  or:       pip install imageio-ffmpeg")
        return DownloadResult(1, request=request)
    manifest = Manifest(MANIFEST_FILE)
    known_ids = manifest.known_ids(game_name)
    skip_ids = known_ids if request.history_mode == "new" else frozenset()

    # Twitch wants RFC3339 UTC timestamps, e.g. 2026-09-20T12:00:00Z
    ended = datetime.now(timezone.utc).replace(microsecond=0)
    started = ended - timedelta(hours=request.window_hours)
    stamp = "%Y-%m-%dT%H:%M:%SZ"
    started_at, ended_at = started.strftime(stamp), ended.strftime(stamp)

    say("")
    say("Game      : %s" % game_name)
    say("Clips     : up to %d" % wanted)
    say("Window    : %s  (%s -> %s UTC)" % (request.window_label, started_at, ended_at))
    say("Ranking   : %s" % request.ranking_label)
    say("Length    : %s" % ("%d to %d seconds" % (request.min_seconds, request.max_seconds)
                            if request.min_seconds or request.max_seconds else "any length"))
    say("Language  : channels set to '%s' only" % TARGET_LANGUAGE)
    say("Filter    : %s" % ("gameplay only - talking clips are skipped"
                            if request.gameplay_only else "off - every clip in the window"))
    say("Quality   : %s - never more than the streamer broadcast at"
        % quality_text(request.max_height))
    say("Folder    : %s" % request.folder)
    output = {key: label for label, key in OUTPUT_FORMATS}[request.output]
    if request.output != "video":
        style = {key: label for label, key in SHORT_STYLES}[request.short_style]
        output += "  (%s)" % style.split(" - ")[0].lower()
    say("Format    : %s" % output)
    if known_ids:
        say("History   : %s" % (
            "skipping %d clip(s) from earlier runs" % len(known_ids)
            if request.history_mode == "new"
            else "including earlier runs - deleted files get fetched again"))

    # Language cache survives across runs, so repeat streamers cost no API call.
    language_cache = load_json(LANG_FILE, {})
    if not isinstance(language_cache, dict):
        language_cache = {}
    if language_cache:
        say("Cache     : %d known channel languages loaded" % len(language_cache))

    # Trending re-ranks what the search found, and the search returns clips in
    # view order, so the top slice alone would be the same clips in a different
    # sequence. Collecting several times the target first is what lets a clip
    # Twitch buried at number 300 come out on top.
    target = wanted
    if request.ranking == "trending":
        target = max(wanted * VELOCITY_OVERSCAN, VELOCITY_MIN_SCAN)
    buckets = collect_clips(api, request.game, target, started_at, ended_at, language_cache,
                            request.gameplay_only, skip_ids,
                            min_seconds=request.min_seconds, max_seconds=request.max_seconds)

    report_filtered(buckets)

    window = request.window_label.lower()
    if not buckets.total_kept():
        say("")
        if buckets.already_had:
            say("Nothing new for '%s' in the %s window." % (game_name, window))
            say("All %d clip(s) Twitch has for that window are ones you already"
                % buckets.scanned)
            say("downloaded. Try a longer window, or run this again tomorrow.")
        elif buckets.dropped:
            say("Every clip for '%s' in the %s window looked like talking, not playing."
                % (game_name, window))
            say("Try a longer window, or turn the gameplay filter off to keep them.")
        else:
            say("No clips at all for '%s' in the %s window." % (game_name, window))
            say("Try a longer window, or a more popular category.")
        return DownloadResult(1, request=request)

    # Put every bucket in the chosen order before any of them is sliced. The
    # search returns clips by view count, so on "most views" this changes
    # nothing, but on "trending" it is what decides which clips make the cut
    # rather than merely what order they are downloaded in.
    rank_key = ranker(request.ranking)
    for bucket in (buckets.gameplay, buckets.maybe, buckets.others):
        bucket.sort(key=rank_key, reverse=True)

    # Clear gameplay first; clips the filter could not read are used only to
    # make up the numbers, and non-English ones only if we are still short.
    chosen = list(buckets.gameplay[:wanted])
    if len(chosen) < wanted and buckets.maybe:
        filler = buckets.maybe[:wanted - len(chosen)]
        chosen.extend(filler)
        say("")
        say("Found %d clear gameplay clip(s); added %d the filter could not read"
            % (len(buckets.gameplay), len(filler)))
        say("to reach the %d you asked for." % wanted)

    if len(chosen) < wanted:
        say("")
        say("Found %d usable clip(s); you asked for %d." % (len(chosen), wanted))

        # Clips of the wrong length are offered first: they are still English,
        # still gameplay, and only set aside over a rule the user just chose,
        # which makes them a smaller compromise than a channel in another
        # language. Both offers are skipped when there is nothing to offer.
        offer_out_of_range(chosen, buckets, wanted - len(chosen), request, rank_key, confirm)

        missing = wanted - len(chosen)
        if missing > 0 and buckets.others:
            say("There are %d clip(s) from non-English channels available." % len(buckets.others))
            if confirm("Top up the remaining %d with non-English clips?" % missing,
                       "non_english"):
                chosen.extend(buckets.others[:missing])
                say("Added %d non-English clip(s)." % min(missing, len(buckets.others)))
        elif missing > 0 and not buckets.out_of_range:
            say("There are no other clips in this window to top up with.")

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
    try:
        run_downloads(jobs, manifest, request.max_height)
        convert_all(jobs, manifest, request.output, request.short_style)
    except KeyboardInterrupt:
        STOP.set()
        manifest.flush()
        say("")
        say("Stopped by Ctrl+C.")
    report(jobs, manifest, folder, started_monotonic, request.max_height)
    report_shortfall(wanted, chosen, buckets, game_name, request.window_label,
                     request.gameplay_only, request.history_mode)
    return DownloadResult(0, folder, jobs, manifest, request)
