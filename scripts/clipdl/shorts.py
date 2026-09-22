"""Turning a downloaded 16:9 clip into a 9:16 vertical Short (1080x1920).

Two looks, because each suits different clips:

  blur   the whole frame, scaled to the full width, over a blurred and
         zoomed copy of itself that fills the top and bottom. Nothing is cut
         off, so HUDs, killfeeds and minimaps stay readable. The safe default.

  crop   the centre of the frame, zoomed to fill the whole screen. Bigger and
         punchier, but the sides are gone - fine for a fight in the middle of
         the screen, bad when the moment is in a corner or the killfeed.

Converting needs ffmpeg. The one on PATH is used when there is one (winget,
choco and the gyan.dev builds all put it there); otherwise the copy bundled
with the imageio-ffmpeg package, which is what a hosted server will use.
"""

import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

from .config import STOP
from .download import name_tail
from .util import say

WIDTH, HEIGHT = 1080, 1920
SHORTS_FOLDER = "Shorts"
CONVERT_TIMEOUT = 600       # seconds; a 60 s clip takes well under one minute

FILTERS = {
    "blur": (
        "[0:v]split=2[bg][fg];"
        "[bg]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
        "boxblur=20:2,eq=brightness=-0.08[bg];"
        "[fg]scale={w}:-2[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v]"
    ).format(w=WIDTH, h=HEIGHT),
    "crop": (
        "[0:v]scale=-2:{h},crop={w}:{h},setsar=1[v]"
    ).format(w=WIDTH, h=HEIGHT),
}

_ffmpeg = None


def find_ffmpeg():
    """Path to an ffmpeg executable, or None when there is none at all."""
    global _ffmpeg
    if _ffmpeg is None:
        _ffmpeg = shutil.which("ffmpeg") or ""
        if not _ffmpeg:
            try:
                import imageio_ffmpeg
                _ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            except Exception:       # not installed, or no binary for this platform
                _ffmpeg = ""
    return _ffmpeg or None


def short_path(video_path):
    """Where the Short of a clip goes: a Shorts folder beside it, same name."""
    return video_path.parent / SHORTS_FOLDER / video_path.name


def make_short(source, target, style="blur"):
    """Convert one clip. Returns None on success, or a short reason on failure."""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return "ffmpeg not found"
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.stem + ".part.mp4")
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
        "-filter_complex", FILTERS.get(style, FILTERS["blur"]),
        "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
        str(partial),
    ]
    # No console window flashing up for every clip on Windows.
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        done = subprocess.run(command, capture_output=True, text=True,
                              timeout=CONVERT_TIMEOUT, creationflags=flags)
    except subprocess.TimeoutExpired:
        partial.unlink(missing_ok=True)
        return "conversion took too long"
    except OSError as error:
        return "could not start ffmpeg (%s)" % error
    if done.returncode != 0 or not partial.exists() or partial.stat().st_size == 0:
        partial.unlink(missing_ok=True)
        last = (done.stderr or "").strip().splitlines()
        return "ffmpeg failed: %s" % (last[-1][:120] if last else "exit %d" % done.returncode)
    partial.replace(target)
    return None


def _existing_shorts(folder):
    """{name tail: path} for Shorts already made, so a clip is never converted twice."""
    found = {}
    for path in (folder / SHORTS_FOLDER).glob("*.mp4"):
        if not path.name.endswith(".part.mp4"):
            found.setdefault(name_tail(path.name), path)
    return found


def convert_all(jobs, manifest, output, style, workers=2):
    """Make a Short of every clip that landed; in "short" mode drop the 16:9 file.

    Runs after the downloads so a slow conversion never holds up a download.
    Two at a time: ffmpeg already uses several cores for each one.
    """
    landed = [job for job in jobs
              if manifest.status_of(job.clip_id) in ("downloaded", "skipped-exists")]
    if not landed or output not in ("short", "both"):
        return
    folder = landed[0].path.parent
    made_before = _existing_shorts(folder)

    todo = []
    for job in landed:
        job.short = short_path(job.path)
        earlier = made_before.get(name_tail(job.path.name))
        if earlier is not None and earlier != job.short:
            try:                # renumber it to match today's ranking, like the videos
                earlier.replace(job.short)
            except OSError:
                job.short = earlier
        if job.short.exists():
            continue
        source = job.existing if job.existing and job.existing.exists() else job.path
        if source.exists():
            todo.append((job, source))

    if todo:
        say("")
        say("Making %d Short(s), 9:16 at %dx%d, style: %s..."
            % (len(todo), WIDTH, HEIGHT, "blurred background" if style == "blur"
               else "centre crop"))
    counter, lock = [0], threading.Lock()

    def one(item):
        job, source = item
        if STOP.is_set():
            return None
        reason = make_short(source, job.short, style)
        with lock:
            counter[0] += 1
        if reason:
            job.short = None
            say("[%d/%d] Short FAILED (%s): %s" % (counter[0], len(todo), reason, source.name))
        else:
            say("[%d/%d] Short ready: %s" % (counter[0], len(todo), source.name))
        return reason

    with ThreadPoolExecutor(max_workers=workers) as pool:
        failed = sum(1 for reason in pool.map(one, todo) if reason)

    if output == "short":
        # Short-only: the 16:9 file was just the raw material. Keep it when its
        # Short failed, so nothing is lost.
        for job in landed:
            if getattr(job, "short", None) and job.short.exists():
                for video in {job.path, job.existing} - {None}:
                    try:
                        video.unlink(missing_ok=True)
                    except OSError:
                        pass
    say("")
    say("Shorts    : %d ready in %s%s"
        % (sum(1 for job in landed if getattr(job, "short", None) and job.short.exists()),
           folder / SHORTS_FOLDER,
           ", %d failed (their 16:9 file was kept)" % failed if failed else ""))
