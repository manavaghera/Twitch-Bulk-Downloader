"""Recording a YouTube live stream from now, until it ends or you stop it.

yt-dlp hands live HLS streams to ffmpeg and cannot be asked to stop part way,
so this runs ffmpeg itself: YouTube's stream address comes from yt-dlp, ffmpeg
copies it into a .ts file (which stays playable however it is interrupted),
Cancel sends ffmpeg the "q" key so it closes the file cleanly, and the result
is repackaged as an .mp4. YouTube's addresses expire after a few hours; when
ffmpeg stops while the stream is still live, a fresh address is fetched and
the recording carries on in a new part, all joined at the end.
"""

import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from . import media, timing
from .config import STOP
from .shorts import find_ffmpeg
from .util import human_size, sanitize, say

REPORT_EVERY = 10               # seconds between "Recording: ..." lines
_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def stream_address(url, height):
    """(HLS address, headers, height) of the best stream up to `height`."""
    from yt_dlp import YoutubeDL
    from . import ytdl
    opts = dict(ytdl.options(), skip_download=True, format=ytdl.format_for(height, live=True))
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False) or {}
    except Exception as error:
        raise ValueError(ytdl.explain(error))
    chosen = (info.get("requested_formats") or [info])[0]
    address = chosen.get("url") or info.get("url")
    if not address:
        raise ValueError("YouTube gave no stream address for this live stream.")
    return address, chosen.get("http_headers") or info.get("http_headers") or {}, \
        chosen.get("height") or info.get("height") or height, info.get("live_status")


def record_now(item, height, folder):
    """Record until the stream ends or Cancel. Returns the .mp4 made."""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise ValueError("Recording a live stream needs ffmpeg, and it is not installed.")
    address, headers, got, _status = stream_address(item["url"], height)
    stamp = datetime.now().strftime("%Y-%m-%d %H%M")
    base = Path(folder) / sanitize("%s %s [%sp] [%s]" % (item["title"][:70], stamp, got,
                                                          item["id"]), 150)
    say("  Live now - recording from now at %sp. Press Cancel to stop; what was recorded "
        "is kept." % got)
    parts, started = [], time.time()
    while True:
        part = base.with_name(base.name + " part%d.ts" % (len(parts) + 1))
        parts.append(part)
        ended_by_hand = _run(ffmpeg, address, headers, part, started, parts)
        if ended_by_hand or STOP.is_set():
            break
        # ffmpeg stopped by itself: the stream ended - or its address expired.
        try:
            address, headers, got, status = stream_address(item["url"], height)
        except ValueError:
            break
        if status != "is_live":
            break
        say("  The stream address expired - carrying on in part %d." % (len(parts) + 1))
    return _finish(parts, base.with_name(base.name + ".mp4"), started)


def _run(ffmpeg, address, headers, part, started, parts):
    """One ffmpeg run into `part`. True when stopped by Cancel."""
    header_text = "".join("%s: %s\r\n" % (k, v) for k, v in headers.items())
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    if header_text:
        command += ["-headers", header_text]
    command += ["-i", address, "-map", "0", "-c", "copy", "-f", "mpegts", str(part)]
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                   stderr=errors, creationflags=_FLAGS)
        said = 0.0
        while process.poll() is None:
            if STOP.wait(1):
                try:                        # "q" asks ffmpeg to finish the file properly
                    process.communicate(b"q", timeout=30)
                except (subprocess.TimeoutExpired, OSError, ValueError):
                    process.kill()
                return True
            if time.time() - said >= REPORT_EVERY:
                said = time.time()
                size = sum(p.stat().st_size for p in parts if p.exists())
                say("  Recording: %s so far, %s" % (timing.clock(time.time() - started),
                                                    human_size(size)))
        if process.returncode not in (0, 255):
            errors.seek(0)
            last = errors.read().decode("utf-8", "replace").strip().splitlines()
            say("  ffmpeg stopped: %s" % (last[-1][:160] if last else process.returncode))
    return False


def _finish(parts, target, started):
    """Join the recorded parts into one .mp4 (the .ts parts are removed)."""
    parts = [p for p in parts if p.exists() and p.stat().st_size]
    if not parts:
        raise ValueError("Nothing was recorded.")
    if target.exists():
        target = target.with_name(target.stem + " (2).mp4")
    listing = target.with_suffix(".parts.txt")
    listing.write_text("".join("file '%s'\n" % str(p.resolve()).replace("'", "'\\''")
                               for p in parts), encoding="utf-8")
    problem = media.run(["-f", "concat", "-safe", "0", "-i", str(listing.resolve()),
                         "-map", "0", "-c", "copy", "-bsf:a", "aac_adtstoasc",
                         "-movflags", "+faststart", str(target.resolve())])
    listing.unlink(missing_ok=True)
    if problem:
        raise ValueError("The recording is kept as %s, but could not be made an .mp4 (%s)."
                         % (parts[0].name, problem))
    for part in parts:
        part.unlink(missing_ok=True)
    say("  Recorded %s: %s (%s)" % (timing.clock(time.time() - started), target.name,
                                    human_size(target.stat().st_size)))
    return target


def keep_from_start(folder, video_id):
    """A "from the very start" recording stopped by hand: yt-dlp leaves the video
    and the sound as separate partial files - join what is there. None if nothing."""
    tag = "[%s]" % video_id
    parts = sorted((p for p in Path(folder).iterdir() if tag in p.name
                    and p.name.endswith(".part")), key=lambda p: p.stat().st_size, reverse=True)
    if not parts:
        return None
    # "Title [1080p] [id].f299.mp4.part" -> "Title [1080p] [id] (stopped).mp4"
    target = Path(folder) / (re.sub(r"\.f\d+\..*$", "", parts[0].name) + " (stopped).mp4")
    inputs, maps = [], []
    for index, part in enumerate(parts[:2]):    # the biggest two: the video and the sound
        inputs += ["-i", str(part.resolve())]
        maps += ["-map", str(index)]
    problem = media.run(inputs + maps + ["-c", "copy", "-movflags", "+faststart",
                                         str(target.resolve())])
    if problem:
        return None
    for part in parts:
        part.unlink(missing_ok=True)
    return target
