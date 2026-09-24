"""Kills on screen, for VALORANT: the moments a clipper actually wants - a 3K, a 4K,
an ace, a clutch - are runs of kills, and the game shows every kill you get.

Each kill flashes an emblem just below the middle of the screen: a skull (or the
skin's own art) inside a ring, for a second or two. A small copy of the video is
looked at four times a second, in that one spot, for three things together:

  mirror      the spot is symmetric left-to-right - with edges both across and
              down, so a floor line or a wall does not count
  ring        edges facing the middle, at one distance, all the way round (round,
              or wider on a stretched 4:3 screen) - a held gun has no ring
  contrast    light art on a dark backdrop - the spike being planted is one
              smooth light shape

After dying, the stream shows teammates - and their kills flash the same
emblem. Dying brings up the combat report on the right, so those stretches
are found too and their kills left out.

Kills close together make one play: 2, 3, 4, 5 kills. And when the announcer
has just said "last player standing", kills after it are a clutch.
A four-hour stream is looked at in about a minute.
"""

import re
import subprocess

import numpy as np

from . import media, ytpiece
from .config import STOP
from .util import say

FPS = 4                         # frames a second looked at
WIDE, HIGH = 64, 48             # the patch below the centre, in pixels (square on screen)
AREA = (0.42, 0.692, 0.16, 0.21)    # left, top, width, height - fractions of the screen
ON = 0.45                       # an emblem is showing
CHAIN = 20                      # at most this many seconds between kills of one play (a round)
CLUTCH_WINDOW = 45              # kills this soon after "last player standing" are a clutch
STRETCH = (1.0, 1.33)           # emblems are round - or wider, on a stretched 4:3 screen
PANEL_AREA = (0.70, 0.30, 0.30, 0.40)   # where the combat report shows after dying
WATCH_HEIGHT = 360
PLAY_VALUE = {1: 0.25, 2: 0.55, 3: 0.8, 4: 0.95}     # 5 and more: 1.0
WORDS = re.compile(r"valorant|radiant|immortal|vandal|phantom|jett|reyna|raze|sage|"
                   r"chamber|clove|neon|yoru|ascent|haven|lotus|sunset|abyss|icebox|"
                   r"fracture|breeze", re.I)


def is_valorant(item):
    """Does the video say it is VALORANT (title, tags, description)?"""
    info = item.get("_info") or {}
    text = " ".join([item.get("title") or "", " ".join(info.get("tags") or []),
                     (info.get("description") or "")[:2000]])
    return bool(re.search(r"valorant", text, re.I)) or len(set(
        found.lower() for found in WORDS.findall(text))) >= 3


def watch_copy(info, folder, height=WATCH_HEIGHT):
    """A whole small copy of the video (no sound) to look at. None if YouTube
    offers none."""
    videos = [f for f in info.get("formats") or [] if f.get("protocol") == "https"
              and f.get("url") and f.get("height") and f.get("ext") == "mp4"
              and f.get("vcodec") not in (None, "none") and f.get("acodec") in (None, "none")]
    if not videos:
        return None
    small = min(videos, key=lambda f: (abs(f["height"] - height),
                                       not (f.get("vcodec") or "").startswith("avc"),
                                       f.get("tbr") or 0))
    say("Getting a small copy of the video to watch (%dp)..." % small["height"])
    return ytpiece.fetch_whole(small, folder / ("watch%d.mp4" % small["height"]))


def patches(video):
    """The spot below the centre, FPS times a second: uint8 array (frames, HIGH, WIDE)."""
    left, top, width, height = AREA
    process = subprocess.Popen(
        [media.ffmpeg(), "-v", "error", "-skip_frame", "noref", "-i", str(video), "-an",
         "-vf", "fps=%d,crop=iw*%g:ih*%g:iw*%g:ih*%g,scale=%d:%d:flags=area,format=gray"
         % (FPS, width, height, left, top, WIDE, HIGH), "-f", "rawvideo", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    blocks = []
    while True:
        if STOP.is_set():
            process.kill()
            break
        block = process.stdout.read(WIDE * HIGH * 4096)
        if not block:
            break
        blocks.append(block)
    process.wait()
    data = b"".join(blocks)
    usable = len(data) // (WIDE * HIGH) * WIDE * HIGH
    return np.frombuffer(data[:usable], dtype=np.uint8).reshape(-1, HIGH, WIDE)


def _gradients(frames):
    frames = frames.astype(np.float32)
    gx = np.zeros_like(frames)
    gy = np.zeros_like(frames)
    gx[:, :, 1:-1] = frames[:, :, 2:] - frames[:, :, :-2]
    gy[:, 1:-1, :] = frames[:, 2:, :] - frames[:, :-2, :]
    return gx, gy


def _correlate(a, b):
    a = a - a.mean(axis=(1, 2), keepdims=True)
    b = b - b.mean(axis=(1, 2), keepdims=True)
    return (a * b).sum(axis=(1, 2)) / np.sqrt((a * a).sum(axis=(1, 2))
                                              * (b * b).sum(axis=(1, 2)) + 1e-6)


def _rings(sectors=16, radii=range(9, 23)):
    """For each stretch: weights that average a patch over (sector, radius) cells,
    and the unit vectors pointing out from the middle."""
    y, x = np.mgrid[0:HIGH, 0:WIDE]
    dx, dy = x - (WIDE - 1) / 2.0, y - (HIGH - 1) / 2.0
    out = []
    for stretch in STRETCH:
        round_x = dx / stretch                              # the ellipse, made round
        r = np.hypot(round_x, dy) + 1e-6
        sector = (np.floor((np.arctan2(dy, round_x) + np.pi) / (2 * np.pi) * sectors)
                  .astype(int)) % sectors
        cells = np.zeros((sectors * len(radii), HIGH * WIDE), np.float32)
        for s in range(sectors):
            for k, radius in enumerate(radii):
                mask = ((sector == s) & (np.abs(r - radius) <= 1.0)).ravel()
                cells[s * len(radii) + k, mask] = 1.0 / max(mask.sum(), 1)
        out.append((cells, (round_x / r).astype(np.float32), (dy / r).astype(np.float32),
                    sectors, len(radii)))
    return out


_RINGS = []


def ring(gx, gy):
    """0..1 a frame: how much of a ring there is around the middle."""
    if not _RINGS:
        _RINGS.extend(_rings())
    best = np.zeros(len(gx), np.float32)
    for cells, ux, uy, sectors, radii in _RINGS:
        facing = np.abs(gx * ux + gy * uy).reshape(len(gx), -1)
        profile = (facing @ cells.T).reshape(len(gx), sectors, radii)
        where = profile.argmax(axis=2)                      # the ring's distance, each way
        sharp = profile.max(axis=2) / (profile.mean(axis=2) + 1e-6)
        middle = np.median(where, axis=1, keepdims=True)
        best = np.maximum(best, ((np.abs(where - middle) <= 2) & (sharp > 1.5)).mean(axis=1))
    return best


def mirror(gx, gy):
    """0..1 a frame: how mirror-symmetric the middle of the patch is. Thin lines
    make this touchy about where the middle is, so the fold is tried at a few
    places half a pixel apart (streams differ by a pixel or two)."""
    best = np.zeros(len(gx), np.float32)
    half = HIGH // 2 - 1
    for twice in range(WIDE - 5, WIDE + 4):         # the fold at twice/2, around the middle
        left = twice // 2 - half
        width = 2 * half + 1 + twice % 2
        mx, my = gx[:, :, left:left + width], gy[:, :, left:left + width]
        across = _correlate(mx, -mx[:, :, ::-1])    # side edges mirror, with their sign flipped
        down = _correlate(my, my[:, :, ::-1])
        ex, ey = np.abs(mx).mean(axis=(1, 2)), np.abs(my).mean(axis=(1, 2))
        both = np.minimum(ex, ey) / (np.maximum(ex, ey) + 1e-6)   # a ring has both kinds
        best = np.maximum(best, np.clip(across, 0, 1) * np.clip(down, 0, 1) * both
                          * np.clip(ex / 6, 0, 1))
    return best


def contrast(frames):
    """0..1 a frame: sharp light-on-dark art inside the ring. The spike being
    planted, or a gun held in the middle, is one smooth light or dark shape."""
    y, x = np.mgrid[0:HIGH, 0:WIDE]
    inside = frames[:, np.hypot(x - (WIDE - 1) / 2.0, y - (HIGH - 1) / 2.0) < 17]
    spread = np.percentile(inside, 80, axis=1) - np.percentile(inside, 20, axis=1)
    return np.clip((spread - 22) / 18.0, 0, 1)


def emblem_score(frames, step=2048):
    """0..1 a frame: how much the patch looks like a kill emblem."""
    out = []
    for at in range(0, len(frames), step):
        block = frames[at:at + step]
        gx, gy = _gradients(block)
        out.append(mirror(gx, gy) * np.clip(ring(gx, gy) / 0.75, 0, 1) * contrast(block))
    return np.concatenate(out) if out else np.zeros(0, np.float32)


def runs(score, on, off=None, shortest=2, gap=2):
    """[(first frame, last frame)] where `score` stays up (reaches `on`, stays above
    `off`); dips of up to `gap` frames do not end a run."""
    off = on * 0.65 if off is None else off
    out, i = [], 0
    while i < len(score):
        if score[i] < on:
            i += 1
            continue
        j = i
        while True:
            ahead = [k for k in range(j + 1, min(j + gap + 2, len(score))) if score[k] >= off]
            if not ahead:
                break
            j = ahead[0]
        if j - i + 1 >= shortest:
            out.append((i, j))
        i = j + 1
    return out


def find_kills(video):
    """[(second, how sure 0..1)] - one for each kill shown on screen."""
    frames = patches(video)
    if len(frames) < FPS * 30:
        return []
    score = emblem_score(frames)
    kills = []
    for first, last in runs(score, ON):
        seconds = (last - first + 1) / float(FPS)
        # An emblem stays up to 2 s; one kept up longer is kills one after another.
        count = 1 if seconds <= 2.75 else min(int(round(seconds / 1.75)), 5)
        sure = float(score[first:last + 1].max())
        for k in range(count):
            kills.append((first / float(FPS) + k * seconds / count, sure))
    minutes = len(frames) / float(FPS) / 60
    if len(kills) > minutes * 12:           # an emblem every 5 s, non-stop: not VALORANT's emblems
        return []
    return kills


def dead_spans(video):
    """[(from, to)] seconds when the player is dead - watching teammates, whose kills
    flash the same emblem. Dying brings up the combat report on the right: a panel
    whose divider lines run exactly across it and stop at its edge (a wall's edge
    runs on). Its height changes with what it lists, so the lines are looked for,
    not the panel's picture. Keyframes only: hours take seconds."""
    left, top, width, height = PANEL_AREA
    done = subprocess.run(
        [media.ffmpeg(), "-hide_banner", "-skip_frame", "nokey", "-i", str(video), "-an",
         "-vf", "crop=iw*%g:ih*%g:iw*%g:ih*%g,scale=192:144,format=gray,showinfo"
         % (width, height, left, top), "-fps_mode", "passthrough", "-f", "rawvideo", "-"],
        capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    times = [float(t) for t in re.findall(rb"pts_time:\s*([0-9.]+)", done.stderr)]
    frames = np.frombuffer(done.stdout[:len(done.stdout) // 27648 * 27648], dtype=np.uint8)
    frames = frames.reshape(-1, 144, 192)[:len(times)]
    shown = []
    for at in range(0, len(frames), 256):         # a block at a time: hours fit in memory
        block = frames[at:at + 256].astype(np.float32)
        down = np.abs(block[:, 2:, :] - block[:, :-2, :]) > 10
        lines = (down[:, :, 60:180].mean(axis=2) >= 0.85) & (down[:, :, 5:45].mean(axis=2) <= 0.35)
        count = (lines[:, 1:] & ~lines[:, :-1]).sum(axis=1) + lines[:, 0]
        across = np.abs(block[:, :, 2:] - block[:, :, :-2]) > 12
        sides = across.mean(axis=1)               # a column that is one straight edge
        edged = np.minimum(sides[:, 40:75].max(axis=1), sides[:, 175:189].max(axis=1)) > 0.8
        shown.extend((count >= 2) | edged)
    spans = []
    for t, dead in zip(times, shown):
        if not dead:
            continue
        if spans and t - spans[-1][1] <= 12:     # still the same death
            spans[-1][1] = t
        else:
            spans.append([t - 1.0, t])
    return [(first, last + 5.0) for first, last in spans]


def plays(kills, chain=CHAIN):
    """Kills close together as one play: [(first second, last second, kills)]."""
    out = []
    for second, _sure in sorted(kills):
        if out and second - out[-1][1] <= chain:
            out[-1] = (out[-1][0], second, out[-1][2] + 1)
        else:
            out.append((second, second, 1))
    return out


def play_curve(kills, seconds, last_alive=(), dead=(), lead=8, tail=4):
    """(one value a second, {second: label}): each play is worth what it is - a
    lone kill little, an ace the most; kills soon after "last player standing"
    are a clutch. The play itself counts fully, the lead-up before it (up to
    `lead` s) a little less, so a clip holds every kill before it holds the
    run-up. Kills while dead (`dead` spans) are a teammate's and do not count."""
    own = [(t, sure) for t, sure in kills if not any(a <= t <= b for a, b in dead)]
    curve, labels = [0.0] * seconds, {}
    for first, last, count in plays(own):
        value = PLAY_VALUE.get(count, 1.0)
        label = "%dK" % count if count < 5 else "ACE"
        alone = [t for t in last_alive if t - 3 <= first <= t + CLUTCH_WINDOW]
        if alone:
            value, label = max(value, 0.5 + 0.1 * count), "clutch (%dK)" % count
        # A clutch starts at "last player standing" - but not ages before the first kill.
        begin = int(max(min(alone), first - 14)) if alone else int(first) - lead
        for t in range(max(begin, 0), min(int(last) + tail + 1, seconds)):
            curve[t] = max(curve[t], value if t >= first - 2 else value * 0.8)
        if count >= 2 or alone:
            labels[int(first)] = label
    return curve, labels
