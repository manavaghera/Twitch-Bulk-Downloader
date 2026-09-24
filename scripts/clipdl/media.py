"""Small ffmpeg helpers for the Studio and the branding: look inside a video,
fit one to a frame size (trimmed, captioned, with sound), and join videos.

Everything "normalised" here comes out the same way - H.264, 30 fps, AAC
48 kHz stereo - so the pieces of a compilation can be joined without encoding
them all over again.

Encoding uses the graphics card when it can (NVIDIA NVENC, Intel Quick Sync):
several times faster than the processor, and it leaves the processor free for
captions and downloads. Which one works is found out once and remembered;
when the card fails on a video, that video is made on the processor instead.
"""

import re
import subprocess
import sys
import threading
from pathlib import Path

from .config import DATA_DIR
from .util import load_json, save_json

FPS = 30
TIMEOUT = 1800
_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
AUDIO_OUT = ["-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2"]
ENCODER_FILE = DATA_DIR / "encoder.json"
SOFTWARE = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]
HARDWARE = {    # tried in this order; quality set to give files about libx264's crf 20 size
    "h264_nvenc": ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "27",
                   "-b:v", "0", "-pix_fmt", "yuv420p"],
    "h264_qsv": ["-c:v", "h264_qsv", "-preset", "veryfast", "-global_quality", "25",
                 "-pix_fmt", "nv12"],
}
_encoder = {}
_encoder_lock = threading.Lock()


def ffmpeg():
    from .shorts import find_ffmpeg         # shorts uses this module too
    return find_ffmpeg()


def encoder():
    """The fastest H.264 encoder that works on this PC: "h264_nvenc", "h264_qsv" or
    "libx264". Found once (a one-second test encode), then remembered."""
    exe = ffmpeg()
    with _encoder_lock:
        if exe in _encoder:
            return _encoder[exe]
        saved = load_json(ENCODER_FILE, {})
        if isinstance(saved, dict) and saved.get("ffmpeg") == exe and saved.get("encoder"):
            _encoder[exe] = saved["encoder"]
            return _encoder[exe]
        chosen = "libx264"
        for name, args in HARDWARE.items():
            if exe and _plain_run(exe, ["-f", "lavfi", "-i",
                                        "testsrc=size=640x360:rate=30:duration=1"]
                                  + args + ["-f", "null", "-"], timeout=30) is None:
                chosen = name
                break
        _encoder[exe] = chosen
        save_json(ENCODER_FILE, {"ffmpeg": exe, "encoder": chosen})
        return chosen


def video_args(software=False):
    """ffmpeg arguments that encode the video - on the graphics card when it can."""
    name = "libx264" if software else encoder()
    return list(HARDWARE.get(name, SOFTWARE))


def uses_hardware(args):
    return any(a in HARDWARE for a in args)


def software_instead(args):
    """The same command with the processor's encoder in place of the card's."""
    out, skip = [], 0
    for i, arg in enumerate(args):
        if skip:
            skip -= 1
            continue
        if arg == "-c:v" and i + 1 < len(args) and args[i + 1] in HARDWARE:
            hardware = HARDWARE[args[i + 1]]
            out += SOFTWARE
            skip = len(hardware) - 1
            continue
        out.append(arg)
    return out


def _plain_run(exe, args, cwd=None, timeout=TIMEOUT):
    try:
        done = subprocess.run([exe, "-hide_banner", "-loglevel", "error", "-y"] + list(args),
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", cwd=cwd, timeout=timeout,
                              creationflags=_FLAGS)
    except subprocess.TimeoutExpired:
        return "ffmpeg took too long"
    except OSError as error:
        return "could not start ffmpeg (%s)" % error
    if done.returncode != 0:
        last = (done.stderr or "").strip().splitlines()
        return "ffmpeg failed: %s" % (last[-1][:160] if last else "exit %d" % done.returncode)
    return None


def run(args, cwd=None, timeout=TIMEOUT):
    """Run ffmpeg with `args`. Returns None on success, else a short reason. A
    graphics-card encode that fails is tried once more on the processor."""
    exe = ffmpeg()
    if not exe:
        return "ffmpeg not found"
    problem = _plain_run(exe, args, cwd, timeout)
    if problem and uses_hardware(args):
        problem = _plain_run(exe, software_instead(args), cwd, timeout)
    return problem


_probed = {}                    # (path, size, modified) -> what probe() found


def probe(path):
    """{"duration", "audio", "width", "height", "fps"} read from ffmpeg's own
    description of the file (imageio's ffmpeg comes without ffprobe). Remembered
    until the file changes, since the page asks on every redraw."""
    try:
        stat = Path(path).stat()
        key = (str(path), stat.st_size, stat.st_mtime)
    except OSError:
        key = None
    if key in _probed:
        return dict(_probed[key])
    info = _probe(path)
    if key and info["duration"]:
        if len(_probed) > 500:
            _probed.clear()
        _probed[key] = dict(info)
    return info


def _probe(path):
    exe = ffmpeg()
    info = {"duration": 0.0, "audio": False, "width": 0, "height": 0, "fps": 0.0}
    if not exe:
        return info
    try:
        done = subprocess.run([exe, "-hide_banner", "-i", str(path)], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=60,
                              creationflags=_FLAGS)
    except (OSError, subprocess.TimeoutExpired):
        return info
    text = done.stderr or ""
    match = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if match:
        h, m, s = match.groups()
        info["duration"] = int(h) * 3600 + int(m) * 60 + float(s)
    video = re.search(r"Stream #.*Video:.*?(\d{2,5})x(\d{2,5})", text)
    if video:
        info["width"], info["height"] = int(video.group(1)), int(video.group(2))
    rate = re.search(r"(\d+(?:\.\d+)?) fps", text)
    if rate:
        info["fps"] = float(rate.group(1))
    info["audio"] = bool(re.search(r"Stream #.*Audio:", text))
    return info


def fit(source, target, width, height, start=None, end=None, subtitles=(), overlay=None):
    """Fit `source` into width x height (bars where the shape differs), trimmed
    to start..end seconds, with .ass files burned in and sound always present.

    `subtitles` must all sit in one folder (ffmpeg's subtitles filter cannot take
    a Windows path, so it runs from there). `overlay` is (image, "x:y" expression,
    width in px, opacity) - a logo. Returns None or the reason it failed.
    """
    info = probe(source)
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    args = []
    if start:
        args += ["-ss", "%.3f" % start]
    length = (end - (start or 0)) if end else None
    if length:
        args += ["-t", "%.3f" % length]
    args += ["-i", str(Path(source).resolve())]
    inputs = 1
    graph = ("[0:v]scale=%d:%d:force_original_aspect_ratio=decrease,"
             "pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps=%d,format=yuv420p[v0]"
             % (width, height, width, height, FPS))
    label = "[v0]"
    if overlay:
        image, position, logo_width, opacity = overlay
        args += ["-i", str(Path(image).resolve())]
        graph += (";[%d:v]scale=%d:-1,format=rgba,colorchannelmixer=aa=%.2f[lg];"
                  "%s[lg]overlay=%s[vl]" % (inputs, logo_width, opacity, label, position))
        label, inputs = "[vl]", inputs + 1
    for n, sub in enumerate(subtitles):
        graph += ";%ssubtitles=filename=%s[vs%d]" % (label, Path(sub).name, n)
        label = "[vs%d]" % n
    if info["audio"]:
        audio = ["-map", "0:a:0"]
    else:                                   # silence, so every piece can be joined
        args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]     # -shortest ends it
        audio = ["-map", "%d:a" % inputs]
    args += (["-filter_complex", graph, "-map", label] + audio + video_args()
             + ["-r", str(FPS)] + AUDIO_OUT
             + ["-shortest", "-movflags", "+faststart", str(target.resolve())])
    cwd = str(Path(subtitles[0]).parent) if subtitles else None
    return run(args, cwd=cwd)


def join(parts, target):
    """Join videos made by fit() with the same size, without encoding again."""
    target = Path(target)
    listing = target.with_suffix(".parts.txt")
    listing.write_text("".join("file '%s'\n" % str(Path(p).resolve()).replace("'", "'\\''")
                               for p in parts), encoding="utf-8")
    try:
        return run(["-f", "concat", "-safe", "0", "-i", str(listing.resolve()), "-c", "copy",
                    "-movflags", "+faststart", str(target.resolve())])
    finally:
        listing.unlink(missing_ok=True)


def join_encoding(parts, target):
    """Join videos of any make (the same frame size): slower, always works."""
    args, graph = [], ""
    for i, part in enumerate(parts):
        args += ["-i", str(Path(part).resolve())]
        graph += ("[%d:v]fps=%d,format=yuv420p,setsar=1[v%d];"
                  "[%d:a]aresample=48000,aformat=channel_layouts=stereo[a%d];" % (i, FPS, i, i, i))
    graph += "".join("[v%d][a%d]" % (i, i) for i in range(len(parts)))
    graph += "concat=n=%d:v=1:a=1[v][a]" % len(parts)
    return run(args + ["-filter_complex", graph, "-map", "[v]", "-map", "[a]"] + video_args()
               + ["-r", str(FPS)] + AUDIO_OUT + ["-movflags", "+faststart", str(Path(target).resolve())])


def still(source, target, at=1.0):
    """One frame of a video as a .jpg (for previews)."""
    return run(["-ss", "%.2f" % at, "-i", str(Path(source).resolve()), "-frames:v", "1",
                "-q:v", "3", str(Path(target).resolve())])


def cut(source, target, start, end):
    """start..end seconds of a video, same size, sound kept (encoded, so the cut
    lands on the exact frame rather than the nearest keyframe)."""
    return run(["-ss", "%.3f" % start, "-t", "%.3f" % max(end - start, 0.5),
                "-i", str(Path(source).resolve()), "-map", "0:v:0", "-map", "0:a?"]
               + video_args() + ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                str(Path(target).resolve())])
