"""Telling a real gameplay moment from someone just talking loudly.

Loudness alone cannot: a scream at a 5K and a loud story sound the same. So a
moment is judged the way a clipper would, from three more angles:

  what is said    the stream is transcribed (on the graphics card, a few minutes
                  for hours of audio) and highlight words score: "ace", "clutch",
                  "1v3", "four k", "headshot", "let's go", "oh my god", "no way",
                  "clip it", laughing and disbelief ("haha", "I'm dead", "what the
                  hell")... plus your own words; announcer lines that come every
                  round ("one enemy remaining") count for little
  action sound    the high end of the sound - gunfire, explosions, impacts - not
                  its volume, which mostly follows the voice
  on screen       a tiny copy of the video is scanned for how much the picture
                  changes: menus, lobbies and talking to camera barely move, and
                  count for less
"""

import re
import subprocess
from pathlib import Path

from . import captions, media, ytpiece
from .util import say

# phrase -> how much it says "this is a clip" (whole words; "1v3" style handled below)
HYPE = {
    "ace": 3.0, "clutch": 3.0, "clutched": 3.0, "team wipe": 2.5, "wiped": 1.5,
    "four k": 2.5, "4k": 2.5, "quad": 2.0, "quadra": 2.5, "penta": 3.0, "five k": 3.0,
    "5k": 3.0, "triple": 2.0, "three k": 2.0, "3k": 2.0, "double": 1.0,
    "headshot": 1.5, "one tap": 2.0, "onetap": 2.0, "no scope": 2.0, "collateral": 2.0,
    "let's go": 2.0, "lets go": 2.0, "let's gooo": 2.5, "oh my god": 1.5, "omg": 1.5,
    "no way": 1.5, "holy": 1.5, "insane": 1.5, "crazy": 1.0, "sheesh": 1.5, "wow": 1.0,
    "clip it": 3.0, "clip that": 3.0, "clipped": 2.0, "did you see": 1.5, "what was that": 1.5,
    "yes": 0.7, "get in": 1.5, "unreal": 1.5, "cracked": 1.5, "goated": 1.5, "gg": 0.7,
    # Something funny: laughing, disbelief, a mess.
    "haha": 1.5, "hahaha": 2.0, "lol": 1.0, "lmao": 1.5, "i'm dead": 1.5, "i'm crying": 1.5,
    "what the hell": 1.5, "what the fuck": 1.5, "wtf": 1.5, "are you serious": 1.5,
    "bro what": 1.5, "why would you": 1.0, "that's so bad": 1.0, "oh no": 1.0,
    # What the games' own announcers say - it is in the sound, and the transcript
    # hears it even when the streamer is quiet. Lines that come every round (an
    # enemy left, the spike) are worth little alone.
    "team ace": 3.0, "flawless": 2.5, "thrifty": 1.5, "enemy remaining": 0.3,
    "one enemy remaining": 0.3, "spike defused": 0.5, "last one": 1.0, "one left": 1.0,
    "last player standing": 0.3, "player standing": 0.3,
    "double kill": 1.5, "triple kill": 2.5, "quadra kill": 3.0, "penta kill": 3.0,
    "killing spree": 1.5, "rampage": 2.0, "unstoppable": 2.0, "godlike": 2.5, "legendary": 2.5,
    "shut down": 1.5, "shutdown": 1.5, "first blood": 1.5, "victory royale": 3.0,
    "winner winner": 3.0, "chicken dinner": 3.0, "squad wiped": 2.5, "squad eliminated": 2.5,
    "you are the champion": 3.0, "champion": 1.5, "kill leader": 1.5, "enemy team eliminated": 2.5,
    "multi kill": 2.0, "multikill": 2.0, "bomb has been defused": 1.0, "terrorists win": 1.0,
    "counter terrorists win": 1.0, "victory": 1.5,
}
VERSUS = re.compile(r"^(?:1|one)\s*v(?:s)?\s*(?:[2-5]|two|three|four|five)$")
MOTION_HEIGHT = 144
BEFORE, AFTER = 12, 5           # seconds a highlight word covers: the play, then the shout


def hype_curve(sound, seconds, extra_words=(), gpu_only_over=3600, said=None):
    """(score a second, {second: [phrases heard]}) from what is said - or (None, {})
    when captions are not installed, or a long video would take too long without
    a graphics card. Every word heard is added to `said` (a list), if given."""
    if not captions.available():
        return None, {}
    if seconds > gpu_only_over and not captions.gpu_ready():
        say("  Skipping what is said: more than an hour of audio needs a graphics card.")
        return None, {}
    phrases = dict(HYPE, **{w.strip().lower(): 3.0 for w in extra_words if w.strip()})
    longest = max(len(p.split()) for p in phrases)
    curve, heard, recent = [0.0] * seconds, {}, []
    reported = 0
    say("Listening to what is said...")
    for start, end, word in captions.listen(sound):
        if said is not None:
            said.append((start, end, word))
        second = int(start)
        if second >= seconds:
            break
        token = re.sub(r"[^a-z0-9' ]", "", word.lower())
        recent = (recent + [(second, token)])[-longest:]
        for size in range(1, len(recent) + 1):
            phrase = " ".join(t for _s, t in recent[-size:])
            weight = phrases.get(phrase) or (2.5 if size <= 3 and VERSUS.match(phrase) else 0)
            if weight:
                at = recent[-size][0]
                curve[at] += weight
                heard.setdefault(at, []).append(phrase)
        if word.endswith("!"):
            curve[second] += 0.5            # said with feeling
        if second - reported >= 300:
            reported = second
            say("[%d/%d] Listening: %d of %d min" % (second // 60, seconds // 60,
                                                     second // 60, seconds // 60))
    return _spread(curve, BEFORE, AFTER), heard


def action_curve(sound, seconds):
    """The high end of the sound, a second at a time (dB): shots and impacts."""
    return _levels(sound, seconds, "aresample=16000,highpass=f=2500,highpass=f=2500",
                   16000, "action.txt")


def loud_levels(sound, seconds):
    """Plain loudness a second at a time (dB) - only a small part of the judgement."""
    return _levels(sound, seconds, "aresample=8000", 8000, "levels.txt")


def _levels(sound, seconds, filters, rate, name):
    folder = Path(sound).parent
    problem = media.run(["-i", Path(sound).name, "-af", "%s,asetnsamples=%d,astats=metadata=1:"
                         "reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level:file=%s"
                         % (filters, rate, name), "-f", "null", "-"],
                        cwd=str(folder), timeout=3600)
    path = folder / name
    if problem or not path.exists():
        return None
    values = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "RMS_level=" in line:
            try:
                value = float(line.split("=", 1)[1])
            except ValueError:
                value = -90.0
            values.append(max(value, -90.0) if value == value else -90.0)   # NaN: silence
    return _average((values + [-90.0] * seconds)[:seconds], 3) if values else None


def motion_curve(info, seconds, folder, copy=None):
    """How much the picture changes, a second at a time, from a small copy of the
    video (`copy`, else a tiny one fetched now; keyframes only, so hours take
    seconds). None when it cannot be had."""
    if copy is None:
        videos = [f for f in info.get("formats") or [] if f.get("protocol") == "https"
                  and f.get("vcodec") not in (None, "none") and f.get("height")
                  and f.get("acodec") in (None, "none") and f.get("url")]
        if not videos:
            return None
        small = min(videos, key=lambda f: (abs(f["height"] - MOTION_HEIGHT), f.get("tbr") or 0))
        say("Getting a tiny copy of the video to watch...")
        copy = ytpiece.fetch_whole(small, Path(folder) / "small.video")
        if copy is None:
            return None
    say("Watching for action on screen...")
    ffmpeg = media.ffmpeg()
    try:
        done = subprocess.run(
            [ffmpeg, "-hide_banner", "-skip_frame", "nokey", "-i", str(copy), "-vf",
             "scale=64:36,format=gray,tblend=all_mode=difference,signalstats,metadata=print:"
             "key=lavfi.signalstats.YAVG", "-fps_mode", "passthrough", "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess,
                                                                  "CREATE_NO_WINDOW") else 0)
    except (OSError, subprocess.TimeoutExpired):
        return None
    points, when = [], None
    for line in done.stderr.splitlines():
        found = re.search(r"pts_time:([0-9.]+)", line)
        if found:
            when = float(found.group(1))
        elif "signalstats.YAVG=" in line and when is not None:
            points.append((when, float(line.rsplit("=", 1)[1])))
    if len(points) < 3:
        return None
    curve, index = [0.0] * seconds, 0
    for second in range(seconds):          # each second takes the nearest keyframe's change
        while index + 1 < len(points) and points[index + 1][0] <= second:
            index += 1
        curve[second] = points[index][1]
    return _average(curve, 5)


def _average(values, width):
    out, total, window = [], 0.0, []
    for value in values:
        window.append(value)
        total += value
        if len(window) > width:
            total -= window.pop(0)
        out.append(total / len(window))
    return out


def _spread(values, before, after):
    """A word's weight covers the seconds before it (the play that made someone say
    it) and a few after (the reaction), so a clip around it scores as a whole."""
    out = [0.0] * len(values)
    for second, value in enumerate(values):
        if value:
            for t in range(max(0, second - before), min(len(values), second + after + 1)):
                out[t] = max(out[t], value)
    return out
