"""Turning a downloaded 16:9 clip into a 9:16 vertical Short (1080x1920).

Three looks, because each suits different clips:

  blur   the whole frame, scaled to the full width, over a blurred and
         zoomed copy of itself that fills the top and bottom. Nothing is cut
         off, so HUDs, killfeeds and minimaps stay readable. The safe default.

  crop   the centre of the frame, zoomed to fill the whole screen. Bigger and
         punchier, but the sides are gone - fine for a fight in the middle of
         the screen, bad when the moment is in a corner or the killfeed.

  split  the streamer's camera on top, the middle of the gameplay below - the
         usual clip-channel look. Needs the camera's place, marked once per
         streamer (facecams.py); a clip from a streamer with none marked
         gets the blur look instead.

Any look can have captions burned in (captions.py).

Converting needs ffmpeg. The one on PATH is used when there is one (winget,
choco and the gyan.dev builds all put it there); otherwise the copy bundled
with the imageio-ffmpeg package, which is what a hosted server will use.
"""

import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from . import branding, captions, facecams, media, timing
from .config import STOP
from .download import clip_key, name_tail
from .util import say, thread_pool

WIDTH, HEIGHT = 1080, 1920
SHORTS_FOLDER = "Shorts"
CONVERT_TIMEOUT = 600       # seconds; a 60 s clip takes well under one minute

FILTERS = {
    # The background is blurred at a quarter of the size and scaled back up:
    # it looks the same, and is about four times faster than blurring it full size.
    "blur": (
        "[0:v]split=2[bg][fg];"
        "[bg]scale={qw}:{qh}:force_original_aspect_ratio=increase,crop={qw}:{qh},"
        "boxblur=6:2,scale={w}:{h},eq=brightness=-0.08[bg];"
        "[fg]scale={w}:-2[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v]"
    ).format(w=WIDTH, h=HEIGHT, qw=WIDTH // 4, qh=HEIGHT // 4),
    "crop": (
        "[0:v]scale=-2:{h},crop={w}:{h},setsar=1[v]"
    ).format(w=WIDTH, h=HEIGHT),
}

CAM_HEIGHT = 640           # split look: camera on top, gameplay fills the rest


def split_filter(box):
    """Camera box (x, y, w, h as fractions) on top, the centre of the game below."""
    x, y, w, h = box
    game_h = HEIGHT - CAM_HEIGHT
    return (
        "[0:v]split=2[cam][game];"
        "[cam]crop=iw*{w:.4f}:ih*{h:.4f}:iw*{x:.4f}:ih*{y:.4f},"
        "scale={W}:{C}:force_original_aspect_ratio=increase,crop={W}:{C},setsar=1[cam];"
        "[game]crop=ih*{W}/{G}:ih:(iw-ih*{W}/{G})/2:0,scale={W}:{G},setsar=1[game];"
        "[cam][game]vstack=inputs=2[v]"
    ).format(x=x, y=y, w=w, h=h, W=WIDTH, C=CAM_HEIGHT, G=game_h)


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


def make_short(source, target, style="blur", box=None, caption_file=None, brand=None):
    """Convert one clip. Returns None on success, or a short reason on failure.

    `box` is the camera for the split look; `caption_file` an .ass file to burn in;
    `brand` the logo, hook line, intro and outro from branding.for_short().
    """
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return "ffmpeg not found"
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.stem + ".part.mp4")
    graph = split_filter(box) if style == "split" and box else FILTERS.get(style, FILTERS["blur"])
    out_label = "[v]"
    inputs = ["-i", str(Path(source).resolve())]
    brand = brand or {}
    if brand.get("overlay"):
        logo, position, logo_width, opacity = brand["overlay"]
        inputs += ["-i", str(Path(logo).resolve())]
        graph += (";[1:v]scale=%d:-1,format=rgba,colorchannelmixer=aa=%.2f[lg];"
                  "[v][lg]overlay=%s[vl]" % (logo_width, opacity, position))
        out_label = "[vl]"
    subtitles = [s for s in (brand.get("hook"), caption_file) if s]
    workdir = None
    for n, sub in enumerate(subtitles):
        # ffmpeg's subtitles filter trips over Windows paths ("C:"), so it is run
        # from the captions' folder and given the bare file names.
        workdir = str(Path(sub).parent)
        graph += ";%ssubtitles=filename=%s[vs%d]" % (out_label, Path(sub).name, n)
        out_label = "[vs%d]" % n
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y"] + inputs + [
        "-filter_complex", graph,
        "-map", out_label, "-map", "0:a?"] + media.video_args() + [
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
        str(Path(partial).resolve()),
    ]
    # No console window flashing up for every clip on Windows.
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", cwd=workdir,
                              timeout=CONVERT_TIMEOUT, creationflags=flags)
    except subprocess.TimeoutExpired:
        partial.unlink(missing_ok=True)
        return "conversion took too long"
    except OSError as error:
        return "could not start ffmpeg (%s)" % error
    if done.returncode != 0 and media.uses_hardware(command):
        # The graphics card said no (busy, driver): make this one on the processor.
        try:
            done = subprocess.run(media.software_instead(command), capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", cwd=workdir,
                                  timeout=CONVERT_TIMEOUT, creationflags=flags)
        except (OSError, subprocess.TimeoutExpired) as error:
            partial.unlink(missing_ok=True)
            return "conversion failed (%s)" % error
    if done.returncode != 0 or not partial.exists() or partial.stat().st_size == 0:
        partial.unlink(missing_ok=True)
        last = (done.stderr or "").strip().splitlines()
        return "ffmpeg failed: %s" % (last[-1][:120] if last else "exit %d" % done.returncode)
    problem = branding.wrap(partial, brand)
    if problem:
        say("  Intro/outro left off %s (%s)" % (target.name, problem))
    partial.replace(target)
    return None


def _existing_shorts(folder):
    """{name tail: path} for Shorts already made, so a clip is never converted twice."""
    found = {}
    for path in (folder / SHORTS_FOLDER).glob("*.mp4"):
        key = name_tail(path.name)
        if key and not path.name.endswith(".part.mp4"):
            found.setdefault(key, path)
    return found


def _look(job, style, warned):
    """The look for one clip: split needs the streamer's camera marked."""
    if style != "split":
        return style, None
    box = facecams.region(job.streamer, job.broadcaster_id or None)
    if box is None:
        if job.streamer not in warned:
            warned.add(job.streamer)
            say("  No camera marked for %s - their Shorts use the blurred look. Mark it "
                "on the Streamers page." % job.streamer)
        return "blur", None
    return "split", box


def _captioned(job, source, folder):
    """An .ass caption file for a clip in `folder`, or None (no speech, or no captions)."""
    ass = Path(folder) / ("c%s.ass" % abs(hash(job.clip_id)))
    try:
        return ass if captions.make_captions(source, ass) else None
    except Exception as error:      # captions are a bonus; the Short still gets made
        say("  Captions skipped for %s (%s)" % (source.name, str(error)[:80]))
        return None


def convert_all(jobs, manifest, output, style, workers=None, with_captions=False):
    """Make a Short of every clip that landed; in "short" mode drop the 16:9 file.

    Runs after the downloads so a slow conversion never holds up a download.
    Two at a time on the processor (ffmpeg already uses several cores for each),
    three when the graphics card does the encoding.
    """
    workers = workers or (3 if media.encoder() != "libx264" else 2)
    landed = [job for job in jobs
              if manifest.status_of(job.clip_id) in ("downloaded", "skipped-exists")]
    if not landed or output not in ("short", "both"):
        return
    folder = landed[0].path.parent
    made_before = _existing_shorts(folder)

    moves = []
    for job in landed:
        job.short = short_path(job.path)
        earlier = short_path(job.previous) if getattr(job, "previous", None) else None
        if earlier is None or not earlier.exists():
            earlier = made_before.get(clip_key(job.streamer, job.title))
        if earlier is not None and earlier.exists() and earlier != job.short:
            moves.append((job, earlier))
    # Renumbered to match today's ranking, like the videos - through a temporary
    # name first, since two Shorts can swap numbers.
    staged = []
    for job, earlier in moves:
        try:
            temp = earlier.with_name(earlier.name + ".renaming")
            earlier.rename(temp)
            staged.append((job, temp, earlier))
        except OSError:
            job.short = earlier
    for job, temp, earlier in staged:
        try:
            temp.replace(job.short)
        except OSError:
            temp.rename(earlier)
            job.short = earlier

    todo = []
    for job in landed:
        if job.short.exists():
            continue
        source = job.existing if job.existing and job.existing.exists() else job.path
        if source.exists():
            todo.append((job, source))

    if with_captions and not captions.available():
        say("Captions are off: faster-whisper is not installed (pip install faster-whisper).")
        with_captions = False
    if todo:
        say("")
        say("Making %d Short(s), 9:16 at %dx%d, style: %s%s..."
            % (len(todo), WIDTH, HEIGHT, {"blur": "blurred background", "crop": "centre crop",
                                          "split": "facecam + gameplay"}.get(style, style),
               ", with captions" if with_captions else ""))
    counter, lock, warned = [0], threading.Lock(), set()
    brand_config = branding.settings()
    wants_folder = with_captions or (brand_config["enabled"] and brand_config["hook_on"])
    caption_dir = tempfile.mkdtemp(prefix="clipdl-captions-") if wants_folder else None
    brand = branding.for_short(caption_dir, brand_config) if todo else None
    if brand:
        say("Branding  : %s" % ", ".join(name for name, on in (
            ("logo", brand["overlay"]), ("hook line", brand["hook"]),
            ("intro", brand["intro"]), ("outro", brand["outro"])) if on) or "on (nothing set)")

    def one(item):
        job, source = item
        if STOP.is_set():
            return None
        look, box = _look(job, style, warned)
        ass = _captioned(job, source, caption_dir) if with_captions else None
        reason = make_short(source, job.short, look, box, ass, brand)
        if ass:
            ass.unlink(missing_ok=True)
        with lock:
            counter[0] += 1
        if reason:
            job.short = None
            say("[%d/%d] Short FAILED (%s): %s" % (counter[0], len(todo), reason, source.name))
        else:
            say("[%d/%d] Short ready: %s" % (counter[0], len(todo), source.name))
        return reason

    started = time.monotonic()
    with thread_pool(workers) as pool:
        failed = sum(1 for reason in pool.map(one, todo) if reason)
    if todo and not STOP.is_set():
        timing.record("short_captions" if with_captions else "short",
                      time.monotonic() - started, len(todo))
    if caption_dir:
        shutil.rmtree(caption_dir, ignore_errors=True)

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
