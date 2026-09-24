"""Checking each clip's sound against its picture - against YouTube's own files.

Finding the moments already downloads the whole sound track and (to watch the
screen) a whole small copy of the video: complete files, where sound and picture
sit exactly where YouTube's player puts them. So each fetched piece is compared
with them: its sound with the whole sound track, its picture's changes with the
small copy's. If the two disagree by more than a tenth of a second, the piece is
re-cut so they line up again - the voice never lands before or after the play.

It also checks that both start at the very beginning: a picture that starts a
few seconds in (after a gap the file marks) plays in sync in some players, but
many ignore the gap and run the picture that much ahead of the voice.
"""

import re
import subprocess

import numpy as np

from . import media
from .util import say

RATE = 8000                     # sound samples a second compared
FPS = 10                        # picture changes a second compared
MOST = 5.0                      # the largest slip looked for, seconds
TOLERANCE = 0.1                 # seconds: less than this is in sync


def _read(args):
    done = subprocess.run([media.ffmpeg(), "-v", "error"] + args, capture_output=True,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return done.stdout


def _from(path, start, duration):
    """Input arguments for `path` from `start`. No jump at all from the very start:
    a jump to 0 lands on the first keyframe, skipping any sound before it."""
    return (["-ss", "%.3f" % start] if start > 0 else []) + ["-i", str(path)] + (
        ["-t", "%.3f" % duration] if duration else [])


def sound(path, start=0.0, duration=None):
    """The sound of `path` from `start`, mono at RATE: float array."""
    args = _from(path, start, duration) + [
        "-map", "0:a:0", "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"]
    return np.frombuffer(_read(args), dtype=np.int16).astype(np.float32)


def changes(path, start=0.0, duration=None):
    """How much the picture changes, FPS times a second: float array."""
    args = _from(path, start, duration) + [
        "-map", "0:v:0", "-vf", "fps=%d,scale=32:18,format=gray" % FPS, "-f", "rawvideo", "-"]
    frames = np.frombuffer(_read(args), dtype=np.uint8)
    frames = frames[:len(frames) // 576 * 576].reshape(-1, 576).astype(np.float32)
    return np.abs(np.diff(frames, axis=0)).mean(axis=1) if len(frames) > 2 else np.zeros(0)


def lag(a, b, rate, most=MOST, least=0.3):
    """Seconds `a` is late compared with `b` (negative: early), or None when they
    are too unlike to say."""
    n = min(len(a), len(b))
    if n < rate * 3:
        return None
    a, b = a[:n] - a[:n].mean(), b[:n] - b[:n].mean()
    scale = np.sqrt((a * a).sum() * (b * b).sum())
    if not scale:
        return None
    both = np.fft.irfft(np.fft.rfft(a, 2 * n) * np.conj(np.fft.rfft(b, 2 * n)))
    reach = int(min(most * rate, n - 1))
    near = np.concatenate([both[-reach:], both[:reach + 1]])      # lags -reach..+reach
    best = int(np.argmax(near))
    if near[best] / scale < least:
        return None
    return (best - reach) / float(rate)


def first_times(path):
    """(first picture, first sound) - seconds into the file each one starts."""
    out = []
    for stream, show in (("0:v:0", ["-vf", "showinfo", "-frames:v", "1"]),
                         ("0:a:0", ["-af", "ashowinfo", "-frames:a", "1"])):
        done = subprocess.run([media.ffmpeg(), "-hide_banner", "-i", str(path), "-map", stream]
                              + show + ["-f", "null", "-"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        found = re.search(r"pts_time:\s*(-?[0-9.]+)", done.stderr or "")
        out.append(float(found.group(1)) if found else 0.0)
    return tuple(out)


def check(piece, start, whole_sound=None, whole_video=None):
    """(sound late by, picture late by, picture starts after the sound by), in
    seconds - the first two None when they cannot be told - for a piece meant to
    start `start` seconds into the video."""
    duration = media.probe(piece).get("duration") or 0
    if duration < 5:
        return None, None, 0.0
    heard = seen = None
    if whole_sound:
        heard = lag(sound(piece), sound(whole_sound, start, duration), RATE)
    if whole_video:
        seen = lag(changes(piece), changes(whole_video, start, duration), FPS, least=0.5)
    picture, voice = first_times(piece)
    return heard, seen, picture - voice


def fix(piece, heard, seen, target):
    """Re-cut `piece` into `target` so the sound and picture line up and both start
    at the beginning. Returns `target`, or None when that did not work."""
    duration = media.probe(piece).get("duration") or 0
    picture, voice = first_times(piece)
    seen, heard = (seen or 0.0) - picture, (heard or 0.0) - voice    # once both start at 0

    def shift(late, video):
        if late >= 0:                       # starts too early: drop the extra
            return ("trim=start=%.3f,setpts=PTS-STARTPTS" if video else
                    "atrim=start=%.3f,asetpts=PTS-STARTPTS") % late
        return ("tpad=start_duration=%.3f:start_mode=clone" % -late if video else
                "adelay=%d:all=1" % int(round(-late * 1000)))
    graph = "[0:v]setpts=PTS-STARTPTS,%s[v];[0:a]asetpts=PTS-STARTPTS,%s[a]" % (
        shift(seen, True), shift(heard, False))
    problem = media.run(["-i", str(piece), "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
                         "-t", "%.3f" % max(duration - abs(seen - heard), 1)]
                        + media.video_args() + ["-c:a", "aac", "-b:a", "192k", str(target)])
    return None if problem else target


def ensure(piece, start, whole_sound=None, whole_video=None):
    """The piece, checked - re-cut when its sound and picture disagree. Returns
    (piece to use, a note for the log)."""
    heard, seen, gap = check(piece, start, whole_sound, whole_video)
    checked = heard is not None or seen is not None
    # A side that could not be checked is taken as right.
    off = (heard or 0.0) - (seen or 0.0)
    if abs(off) <= TOLERANCE and abs(gap) <= TOLERANCE:
        return piece, ("sound and picture in sync (%+.2f s)" % off if checked
                       else "sync not checked")
    if abs(off) > TOLERANCE:
        say("  Sound was %.2f s %s the picture - lining them up." % (
            abs(off), "behind" if off > 0 else "ahead of"))
    else:
        say("  The picture started %.2f s in - making both start together." % gap)
    fixed = fix(piece, heard, seen, piece.with_name(piece.stem + "-synced.mp4"))
    if fixed is None:
        return piece, "sound %+.2f s off the picture (could not fix)" % off
    return fixed, "sound lined up with the picture (was %+.2f s, picture started %.2f s in)" % (
        off, gap)
