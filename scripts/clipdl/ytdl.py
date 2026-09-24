"""YouTube downloads: videos, Shorts and live streams, up to 4K.

Built on yt-dlp, which the Twitch downloader uses already. YouTube serves
video and sound separately above 720p; they are fetched side by side and
joined into one .mp4. At 4K YouTube only has VP9 or AV1 video (no H.264):
VP9 is preferred - it plays in browsers, VLC and the Windows player - and
"plays everywhere" re-encodes to H.264 on the graphics card afterwards.

  videos, Shorts, finished streams   the best up to the chosen quality
  a stream that is live now          recorded from now until it ends or you
                                     stop (what was recorded is kept), or from
                                     the very start when YouTube still has it
  a stream that has not started      waited for, then recorded

YouTube's download links need a small puzzle solved in JavaScript: Node.js
or Deno is used when installed (with the yt-dlp[default] extras). When
YouTube asks to "confirm you're not a bot" or a video needs a sign-in,
cookies from your browser (or a cookies.txt) are the way through - they are
your login session, so they stay in data/ on this PC only.
"""

import shutil
import sys
import time
from pathlib import Path

from . import media, timing
from .config import DATA_DIR, STOP
from .folders import saved_folder
from .shorts import find_ffmpeg
from .util import human_size, load_json, save_json, say

MAX_HEIGHT = 2160
HEIGHTS = (2160, 1440, 1080, 720, 480, 360)
SETTINGS = DATA_DIR / "youtube_dl.json"
COOKIE_FILE = DATA_DIR / "youtube_cookies.txt"
HISTORY = DATA_DIR / "youtube_history.json"
# Chrome and the browsers built on it keep their cookies locked and encrypted
# on Windows ("app-bound encryption", since mid-2024): no other program can read
# them, open or closed. Firefox's can be read - or export a cookies.txt.
LOCKED_ON_WINDOWS = ("chrome", "edge", "brave", "opera", "vivaldi", "chromium")
BROWSERS = (("firefox",) if sys.platform == "win32"
            else ("firefox", "chrome", "edge", "brave", "opera", "vivaldi"))
COOKIE_CHECK_SECONDS = 300
_cookie_checks = {}             # browser -> (when checked, problem or None)
LIVE_POLL = (30, 300)           # seconds between checks while a stream has not started


# -- settings ----------------------------------------------------------------------------
def settings():
    data = load_json(SETTINGS, {})
    return dict({"cookies": "none"}, **(data if isinstance(data, dict) else {}))


def save_settings(**changes):
    save_json(SETTINGS, dict(settings(), **changes))


def save_cookie_file(raw):
    """Keep an uploaded cookies.txt. Returns a problem, or None."""
    text = raw.decode("utf-8", "replace")
    if "youtube.com" not in text or "\t" not in text:
        return "That is not a Netscape cookies.txt with youtube.com cookies in it."
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    COOKIE_FILE.write_text(text, encoding="utf-8")
    save_settings(cookies="file")
    return None


def remove_cookies():
    COOKIE_FILE.unlink(missing_ok=True)
    save_settings(cookies="none")


def js_runtime():
    """"deno" or "node" when one is installed - YouTube's links need it."""
    return next((name for name in ("deno", "node") if shutil.which(name)), None)


class _Log:
    """yt-dlp's messages: errors and warnings to the job log, the rest nowhere."""

    def debug(self, message):
        pass

    info = debug

    def warning(self, message):
        if "JavaScript runtime" not in message:
            say("  Note: %s" % message.replace("WARNING: ", "")[:200])

    def error(self, message):
        say("  %s" % message.replace("ERROR: ", "")[:300])


def options():
    """What every call to YouTube starts from."""
    opts = {"quiet": True, "no_warnings": True, "noprogress": True, "logger": _Log(),
            "noplaylist": True, "retries": 5, "fragment_retries": 10,
            "concurrent_fragment_downloads": 4, "windowsfilenames": True}
    runtime = js_runtime()
    if runtime:
        opts["js_runtimes"] = {runtime: {}}
    ffmpeg = find_ffmpeg()
    if ffmpeg:
        opts["ffmpeg_location"] = ffmpeg
    choice = settings()["cookies"]
    if choice == "file" and COOKIE_FILE.exists():
        opts["cookiefile"] = str(COOKIE_FILE)
    elif choice.startswith("browser:") and not cookie_problem():
        opts["cookiesfrombrowser"] = (choice.split(":", 1)[1],)
    if "cookiefile" in opts or "cookiesfrombrowser" in opts:
        # Signed-in sessions sometimes get "the page needs to be reloaded" from
        # YouTube's default player; these clients avoid it.
        opts["extractor_args"] = {"youtube": {"player_client": [
            "default", "-tv_downgraded", "web_embedded"]}}
    return opts


def cookie_problem():
    """Why the chosen browser's cookies cannot be used right now, or None. Then
    everything carries on without cookies (most videos need none)."""
    choice = settings()["cookies"]
    if not choice.startswith("browser:"):
        return None
    browser = choice.split(":", 1)[1]
    if sys.platform == "win32" and browser in LOCKED_ON_WINDOWS:
        return ("%s keeps its cookies locked and encrypted on Windows, so no other program "
                "can read them. Use Firefox, or export a cookies.txt from %s (the \"Get "
                "cookies.txt LOCALLY\" extension) and upload it." % (browser.title(),
                                                                    browser.title()))
    checked = _cookie_checks.get(browser)
    if checked and time.time() - checked[0] < COOKIE_CHECK_SECONDS:
        return checked[1]
    try:
        from yt_dlp.cookies import extract_cookies_from_browser
        jar = extract_cookies_from_browser(browser, logger=_Quiet())
        problem = None if any("youtube" in (c.domain or "") for c in jar) else (
            "%s has no YouTube cookies - sign in to YouTube in %s first."
            % (browser.title(), browser.title()))
    except Exception as error:              # locked, encrypted, not installed...
        problem = "Could not read %s's cookies (%s)." % (browser.title(),
                                                         str(error).splitlines()[0][:120])
    _cookie_checks[browser] = (time.time(), problem)
    return problem


class _Quiet:
    def debug(self, message):
        pass

    info = warning = error = debug


def explain(error):
    """A plain-English reason for a yt-dlp failure."""
    text = str(error)
    lower = text.lower()
    if "not a bot" in lower or "sign in to confirm" in lower:
        return ("YouTube wants to be sure this is not a bot. Add cookies below (from a "
                "browser where you are signed in to YouTube), then try again.")
    if "private video" in lower:
        return "It is a private video."
    if "members-only" in lower or "join this channel" in lower:
        return "Only the channel's members can watch it - cookies of a member account are needed."
    if "age" in lower and ("confirm" in lower or "restricted" in lower):
        return "It is age-restricted - cookies of a signed-in adult account are needed."
    if "not available" in lower or "unavailable" in lower:
        return "The video is not available (removed, or blocked in this country)."
    if "page needs to be reloaded" in lower:
        return "YouTube asked to reload - export fresh cookies, or wait a minute and retry."
    if "cookie" in lower and ("could not" in lower or "failed" in lower
                              or "decrypt" in lower or "database" in lower):
        return ("Could not read the browser's cookies - Chrome, Edge and Brave keep them "
                "locked and encrypted on Windows. On the YouTube tab, under cookies, pick "
                "Firefox or upload a cookies.txt (or no cookies).")
    return text.splitlines()[0].replace("ERROR: ", "")[:240]


# -- looking before downloading --------------------------------------------------------------
def inspect(url, keep_info=False):
    """What a link is: {url, id, title, channel, duration, thumbnail, live, starts,
    heights {height: approx. bytes}, best, above_4k, audio_bytes, heatmap} - plus
    "_info", yt-dlp's full answer, with `keep_info`. Raises ValueError."""
    from yt_dlp import YoutubeDL
    try:
        # A stream that has not started has no formats yet: still describe it.
        with YoutubeDL(dict(options(), skip_download=True,
                            ignore_no_formats_error=True)) as ydl:
            info = ydl.extract_info(url.strip(), download=False) or {}
    except Exception as error:              # yt-dlp's DownloadError and friends
        raise ValueError(explain(error))
    if info.get("_type") == "playlist":
        raise ValueError("That is a playlist - paste the link of one video.")
    duration = info.get("duration") or 0
    audio = max((f for f in info.get("formats") or [] if f.get("vcodec") == "none"
                 and f.get("acodec") not in (None, "none")),
                key=lambda f: f.get("abr") or f.get("tbr") or 0, default=None)
    audio_bytes = _size(audio, duration) if audio else 0
    heights = {}
    for fmt in info.get("formats") or []:
        height = fmt.get("height")
        if not height or fmt.get("vcodec") in (None, "none"):
            continue
        size = _size(fmt, duration)
        heights[height] = max(heights.get(height, 0), size)
    usable = sorted((h for h in heights if h <= MAX_HEIGHT), reverse=True)
    if not heights and not audio and info.get("live_status") != "is_upcoming":
        # YouTube answered but held back every file: its bot check, in practice.
        raise ValueError("YouTube shows this video but holds back its files - it wants to be "
                         "sure this is not a bot. Add cookies below (from a browser where "
                         "you are signed in to YouTube), then check again.")
    item = {"url": info.get("webpage_url") or url.strip(), "id": info.get("id"),
            "title": info.get("title") or "YouTube video", "channel": info.get("channel")
            or info.get("uploader") or "", "duration": duration,
            "thumbnail": info.get("thumbnail") or "", "live": info.get("live_status")
            or "not_live", "starts": info.get("release_timestamp"),
            "heights": {h: heights[h] + audio_bytes for h in usable},
            "best": usable[0] if usable else 0,
            "above_4k": any(h > MAX_HEIGHT for h in heights), "audio_bytes": audio_bytes,
            "heatmap": info.get("heatmap") or []}
    if keep_info:
        item["_info"] = info
    return item


def _size(fmt, duration):
    size = fmt.get("filesize") or fmt.get("filesize_approx")
    if not size and fmt.get("tbr") and duration:
        size = fmt["tbr"] * 1000 / 8 * duration
    return int(size or 0)


def size_at(item, height):
    """Approx. bytes of this video at (at most) `height`; 0 when unknown (live)."""
    fits = [h for h in item["heights"] if h <= height]
    return item["heights"][max(fits)] if fits else 0


# -- downloading ------------------------------------------------------------------------------
def format_for(height, audio_only=False, live=False):
    if audio_only:
        return "ba/b"
    if live:            # live streams come as one file with sound (HLS) - take that first
        return "b[height<=%d]/bv*[height<=%d]+ba/b" % (height, height)
    return ("bv*[height<=%d]+ba[ext=m4a]/bv*[height<=%d]+ba/b[height<=%d]/b"
            % (height, height, height))


def download_all(items, height, folder=None, audio_only=False, h264=False,
                 live_mode="now"):
    """Download the checked links one after the other. Returns [(item, file or None,
    problem or None)]."""
    folder = Path(folder or Path(saved_folder()[0]) / "YouTube")
    folder.mkdir(parents=True, exist_ok=True)
    results = []
    for index, item in enumerate(items, 1):
        if STOP.is_set():
            break
        say("")
        say("(%d of %d) %s" % (index, len(items), item["title"]))
        try:
            path = download(item, height, folder, audio_only, h264, live_mode)
            results.append((item, path, None))
        except ValueError as error:
            say("  Failed: %s" % error)
            results.append((item, None, str(error)))
    done = sum(1 for _i, path, _p in results if path)
    say("")
    say("YouTube: %d of %d saved in %s" % (done, len(items), folder))
    return results


def download(item, height=MAX_HEIGHT, folder=None, audio_only=False, h264=False,
             live_mode="now"):
    """One link. Returns the file made; raises ValueError with the reason."""
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadCancelled
    folder = Path(folder or Path(saved_folder()[0]) / "YouTube")
    height = min(height or MAX_HEIGHT, MAX_HEIGHT)
    if item["live"] == "is_upcoming":
        item = _wait_for_start(item)
    live = item["live"] == "is_live"
    if live:
        folder = folder / "Live recordings"
    folder.mkdir(parents=True, exist_ok=True)
    if live and live_mode != "start" and not audio_only:
        from . import ytlive
        path = ytlive.record_now(item, height, folder)
        _remember(item, path, {"height": height})
        return path
    state = {"last": -1, "said": 0.0, "started": time.time()}

    def hook(status):
        if STOP.is_set():
            raise DownloadCancelled("stopped")
        if status.get("status") != "downloading":
            return
        done = status.get("downloaded_bytes") or 0
        total = status.get("total_bytes") or status.get("total_bytes_estimate")
        if total and not live:
            percent = int(done * 100 / total)
            if percent >= state["last"] + 2:
                state["last"] = percent
                say("[%d/100] %s of %s at %s/s" % (
                    percent, human_size(done), human_size(total),
                    human_size(status.get("speed") or 0)))
        elif time.time() - state["said"] >= 10:
            state["said"] = time.time()
            say("  Recording: %s so far, %s" % (
                timing.clock(time.time() - state["started"]), human_size(done)))

    opts = dict(options(), format=format_for(height, audio_only, live),
                format_sort=["res:%d" % height, "fps", "vcodec:vp9"],
                outtmpl=str(folder / ("%(title).90s [%(id)s].%(ext)s" if audio_only else
                                      "%(title).80s [%(height)sp] [%(id)s].%(ext)s")),
                merge_output_format="mp4", progress_hooks=[hook])
    if audio_only:
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3",
                                   "preferredquality": "320"}]
    if live:
        opts["live_from_start"] = True
        say("  Live now - recording from the very start, until the stream ends. Cancel "
            "keeps what was fetched so far.")
    started = time.time()
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(item["url"], download=True) or {}
            path = Path(ydl.prepare_filename(info))
    except DownloadCancelled:
        if live:
            from . import ytlive
            kept = ytlive.keep_from_start(folder, item["id"])
            if kept:
                say("  Stopped - kept what was recorded: %s" % kept.name)
                return kept
        return _stopped(folder, item["id"])
    except Exception as error:
        raise ValueError(explain(error))
    path = _real_file(path, audio_only)
    if not path:
        raise ValueError("yt-dlp finished but no file was written.")
    if h264 and not audio_only and not (info.get("vcodec") or "").startswith("avc"):
        path = _to_h264(path)
    if not live and path.stat().st_size:
        timing.record("youtube_mb", time.time() - started, path.stat().st_size / 1e6)
    _remember(item, path, info)
    say("  Saved: %s (%s%s)" % (path.name, human_size(path.stat().st_size),
                                ", %dp" % info["height"] if info.get("height") else ""))
    return path


def _real_file(path, audio_only):
    """The file yt-dlp ended with (a merge or MP3 changes the extension)."""
    for candidate in ([path.with_suffix(".mp3")] if audio_only else []) + [
            path, path.with_suffix(".mp4"), path.with_suffix(".mkv"), path.with_suffix(".webm")]:
        if candidate.exists():
            return candidate
    return None


def _stopped(folder, video_id):
    """Remove the half-downloaded pieces (video and sound come as separate parts)."""
    tag = "[%s]" % video_id
    for leftover in list(Path(folder).iterdir()):
        if tag in leftover.name and leftover.name.endswith((".part", ".ytdl", ".temp")):
            leftover.unlink(missing_ok=True)
    raise ValueError("Stopped - the half-downloaded file was removed.")


def _wait_for_start(item):
    """A scheduled stream: check back until it goes live (Cancel stops waiting)."""
    from datetime import datetime
    while item["live"] == "is_upcoming":
        starts = item.get("starts")
        left = (starts - time.time()) if starts else None
        say("  Waiting for the stream to start%s..." % (
            " (planned %s)" % datetime.fromtimestamp(starts).strftime("%a %H:%M")
            if starts else ""))
        wait = LIVE_POLL[1] if left and left > 600 else LIVE_POLL[0]
        if STOP.wait(wait):
            raise ValueError("Stopped while waiting for the stream to start.")
        item = dict(item, **inspect(item["url"]))
    return item


def _to_h264(path):
    """Re-encode to H.264 + AAC (the graphics card when it can) so it plays anywhere."""
    say("  Converting to H.264 so it plays everywhere...")
    target = path.with_name(path.stem + " (H.264).mp4")
    problem = media.run(["-i", str(path.resolve())] + media.video_args()
                        + ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                           str(target.resolve())])
    if problem:
        say("  Could not convert (%s) - kept the original." % problem)
        return path
    path.unlink(missing_ok=True)
    return target


def _remember(item, path, info):
    history = load_json(HISTORY, [])
    history = history if isinstance(history, list) else []
    history.insert(0, {"url": item["url"], "title": item["title"], "file": str(path),
                       "height": info.get("height"), "when": time.time(),
                       "live": item["live"]})
    save_json(HISTORY, history[:200])


def history(limit=20):
    data = load_json(HISTORY, [])
    return (data if isinstance(data, list) else [])[:limit]
