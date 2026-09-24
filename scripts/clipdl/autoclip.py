"""Auto clips: the best moments of a YouTube video or finished live stream, made
into ready-to-post Shorts - give a link and how many.

How "best" is found, second by second over the whole video:
  most replayed   YouTube's own rewatch graph (the "Most replayed" line on the
                  progress bar) - the strongest signal, when the video has one
  chat bursts     for past live streams, how fast the live chat was going,
                  shifted a few seconds earlier (chat reacts after the moment)
  loud moments    how loud the sound was compared with the rest - hype, screams
                  and big reactions; this one works on every video
Each signal is ranked 0..1 and blended; the best non-overlapping stretches of
the chosen length win. Only those stretches are downloaded - not the whole
four-hour stream - then each becomes a Short (blurred background or crop, the
captions, the branding) with a .txt of title ideas and credit, in
<download folder>/YouTube/Auto clips/<video>/.
"""

import json
import re
import shutil
import tempfile
import time
from pathlib import Path

from . import branding, captions, media, timing, ytdl
from .config import STOP
from .folders import saved_folder
from .shorts import make_short
from .util import sanitize, say

CHAT_DELAY = 8                  # seconds chat reacts after the moment it reacts to
SKIP_START = 60                 # "starting soon" screens and intros are never a highlight
SKIP_END = 45                   # nor are end screens and "thanks for watching"
WEIGHTS = {"replayed": 0.55, "chat": 0.3, "loud": 0.25}
LABELS = {"replayed": "most replayed", "chat": "chat burst", "loud": "loud moment"}


# -- the signals -------------------------------------------------------------------------------
def replay_curve(item, seconds):
    """YouTube's "Most replayed" graph as one value a second (0..1), or None."""
    points = item.get("heatmap") or []
    if not points:
        return None
    curve = [0.0] * seconds
    for point in points:
        start, end = int(point.get("start_time") or 0), int(point.get("end_time") or 0)
        for t in range(max(start, 0), min(max(end, start + 1), seconds)):
            curve[t] = float(point.get("value") or 0)
    return curve


def chat_curve(url, seconds, folder):
    """Chat messages a second from a past live stream's chat replay, or None."""
    from yt_dlp import YoutubeDL
    say("Reading the live chat replay (long streams take a few minutes)...")
    opts = dict(ytdl.options(), skip_download=True, writesubtitles=True,
                subtitleslangs=["live_chat"], outtmpl=str(Path(folder) / "chat"))
    try:
        with YoutubeDL(opts) as ydl:
            ydl.extract_info(url, download=True)
    except Exception as error:
        say("  No chat replay (%s)." % ytdl.explain(error)[:80])
        return None
    files = list(Path(folder).glob("chat*.live_chat.json"))
    if not files:
        return None
    counts = [0.0] * seconds
    with files[0].open(encoding="utf-8") as handle:
        for line in handle:
            try:
                action = json.loads(line).get("replayChatItemAction") or {}
                offset = int(action.get("videoOffsetTimeMsec") or 0) // 1000 - CHAT_DELAY
            except (ValueError, AttributeError):
                continue
            if 0 <= offset < seconds:
                counts[offset] += 1
    return _smooth(counts, 10) if sum(counts) >= 50 else None


def loud_curve(url, seconds, folder):
    """How loud each second is (dB RMS), from a small copy of the sound, or None."""
    from yt_dlp import YoutubeDL
    say("Listening to the sound for the loudest moments...")
    opts = dict(ytdl.options(), format="worstaudio/bestaudio/worst",
                outtmpl=str(Path(folder) / "sound.%(ext)s"))
    try:
        with YoutubeDL(opts) as ydl:
            ydl.extract_info(url, download=True)
    except Exception as error:
        say("  Could not get the sound (%s)." % ytdl.explain(error)[:80])
        return None
    sound = next(iter(Path(folder).glob("sound.*")), None)
    return levels(sound, seconds) if sound else None


def levels(sound, seconds):
    """Loudness (dB RMS) of each second of a sound file, smoothed; None if unreadable."""
    folder = Path(sound).parent
    # One RMS level a second, written by ffmpeg into levels.txt (run from the folder:
    # the filter cannot take a Windows path).
    problem = media.run(["-i", Path(sound).name, "-af", "aresample=8000,asetnsamples=8000,"
                         "astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats."
                         "Overall.RMS_level:file=levels.txt", "-f", "null", "-"],
                        cwd=str(folder), timeout=3600)
    levels_file = Path(folder) / "levels.txt"
    if problem or not levels_file.exists():
        say("  Could not measure the sound (%s)." % (problem or "no levels"))
        return None
    levels = []
    for line in levels_file.read_text(encoding="utf-8", errors="replace").splitlines():
        if "RMS_level=" in line:
            try:
                value = float(line.split("=", 1)[1])
            except ValueError:
                value = -90.0
            levels.append(max(value, -90.0) if value == value else -90.0)   # NaN = silence
    return _smooth((levels + [-90.0] * seconds)[:seconds], 3) if levels else None


def _smooth(values, width):
    """A running average over `width` seconds (a burst, not a single blip)."""
    out, total, window = [], 0.0, []
    for value in values:
        window.append(value)
        total += value
        if len(window) > width:
            total -= window.pop(0)
        out.append(total / len(window))
    return out


def bumps(values, seconds=None):
    """How far each second stands above its own surroundings (the few minutes around
    it). Raw levels drift - fewer people watch hour three, a loud game stays loud -
    so a highlight is a bump, not a high level."""
    count = len(values)
    window = int(min(max((seconds or count) / 8, 60), 600))
    prefix = [0.0]
    for value in values:
        prefix.append(prefix[-1] + value)
    out = []
    for t in range(count):
        low, high = max(0, t - window // 2), min(count, t + window // 2 + 1)
        baseline = (prefix[high] - prefix[low]) / (high - low)
        out.append(values[t] - baseline)
    return out


def _ranked(values):
    """Each value's rank among all of them, 0..1 - so every signal counts alike.
    Equal values share one rank (a flat stretch must not favour its later seconds)."""
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    top = max(len(values) - 1, 1)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for position in range(i, j + 1):
            ranks[order[position]] = (i + j) / 2.0 / top
        i = j + 1
    return ranks


def blend(curves, seconds):
    """{name: curve or None} -> one score a second, 0..1: each signal's bumps,
    ranked, then weighted."""
    present = {name: _ranked(bumps(curve, seconds)) for name, curve in curves.items() if curve}
    if not present:
        return [0.0] * seconds
    weight = sum(WEIGHTS[name] for name in present)
    return [sum(WEIGHTS[name] * curve[t] for name, curve in present.items()) / weight
            for t in range(seconds)]


def pick(score, count, length, gap=10, skip_start=SKIP_START, skip_end=SKIP_END):
    """The `count` best non-overlapping stretches: [(start, end, how good 0..1)]."""
    seconds = len(score)
    if seconds <= length:
        return [(0, seconds, 1.0)]
    prefix = [0.0]
    for value in score:
        prefix.append(prefix[-1] + value)
    # Intros and end screens are left out - unless the video is too short to spare them.
    first = min(skip_start, max(seconds - length - 1, 0))
    last = max(seconds - length - min(skip_end, max(seconds - length - first, 0)), first)
    windows = sorted(((prefix[t + length] - prefix[t]) / length, t)
                     for t in range(first, last + 1, 2))
    chosen = []
    for value, start in reversed(windows):
        if all(abs(start - other) >= length + gap for other, _e, _v in chosen):
            chosen.append((start, start + length, value))
        if len(chosen) == count:
            break
    return chosen


# -- making the clips ----------------------------------------------------------------------------------
def find_moments(url, count, length, use_chat=True, use_sound=True, work=None):
    """(video info, [(start, end, score, why)]) - the best moments of a video."""
    item = ytdl.inspect(url)
    if item["live"] in ("is_live", "is_upcoming"):
        raise ValueError("This stream is still live (or has not started). Run this after it "
                         "ends - YouTube keeps the whole stream.")
    seconds = int(item["duration"] or 0)
    if seconds < length:
        raise ValueError("The video is shorter than one clip.")
    say("%s - %s long. Finding the best %d moment(s) of %d s..." % (
        item["title"][:60], timing.clock(seconds), count, length))
    curves = {"replayed": replay_curve(item, seconds)}
    say("  Most replayed: %s" % ("yes" if curves["replayed"] else "YouTube has no graph for it"))
    curves["chat"] = (chat_curve(item["url"], seconds, work)
                      if use_chat and item["live"] == "was_live" else None)
    curves["loud"] = loud_curve(item["url"], seconds, work) if use_sound else None
    if not any(curves.values()):
        raise ValueError("Nothing to judge the video by: no Most-replayed graph, no chat "
                         "and no sound.")
    ranked = {name: _ranked(bumps(curve, seconds)) for name, curve in curves.items() if curve}
    score = blend(curves, seconds)
    moments = []
    for start, end, value in pick(score, count, length):
        # Named when that signal alone puts the stretch in the top fifth.
        why = [LABELS[name] for name, curve in ranked.items()
               if sum(curve[start:end]) / (end - start) >= 0.8]
        moments.append((start, end, value, why))
    return item, moments


def make_clips(url, count=5, length=30, style="blur", with_captions=True, use_brand=None,
               use_chat=True, use_sound=True, height=1080):
    """The whole job: find the moments, fetch just them, make the Shorts."""
    started = time.time()
    work = Path(tempfile.mkdtemp(prefix="clipdl-autoclip-"))
    try:
        item, moments = find_moments(url, count, length, use_chat, use_sound, work)
        folder = Path(saved_folder()[0]) / "YouTube" / "Auto clips" / sanitize(
            item["title"], 80)
        folder.mkdir(parents=True, exist_ok=True)
        brand_config = branding.settings()
        brand = branding.for_short(work, brand_config) if (
            use_brand if use_brand is not None else brand_config["enabled"]) else None
        results = []
        for rank, (start, end, value, why) in enumerate(moments, 1):
            if STOP.is_set():
                break
            say("[%d/%d] Moment %d at %s (%s)" % (rank, len(moments), rank,
                                                  timing.clock(start), ", ".join(why) or "blend"))
            piece = _fetch_piece(item["url"], start, end, work, rank, height)
            if piece is None:
                continue
            target = folder / sanitize("%02d %s at %s.mp4" % (
                rank, item["title"][:50], timing.clock(start).replace(":", "-")), 120)
            ass, words = None, []
            if with_captions and captions.available():
                ass = work / ("captions%02d.ass" % rank)
                words = captions.transcribe(piece)
                if words:
                    captions.write_ass(captions.chunks(words), ass,
                                       line_groups=captions.groups(words))
                else:
                    ass = None
            problem = make_short(piece, target, style if style != "split" else "blur", None,
                                 ass, brand)
            if problem:
                say("  Could not make the Short: %s" % problem)
                continue
            target.with_suffix(".txt").write_text(
                notes(item, start, words, why), encoding="utf-8")
            results.append({"file": str(target), "start": start, "end": end,
                            "score": value, "why": why})
            say("  Ready: %s" % target.name)
        if results:
            timing.record("autoclip_clip", time.time() - started, len(results))
        say("")
        say("Auto clips: %d Short(s) in %s" % (len(results), folder))
        return {"video": item, "folder": str(folder), "clips": results}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _fetch_piece(url, start, end, work, rank, height):
    """Download just start..end (a little extra either side, then cut exactly)."""
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import download_range_func
    opts = dict(ytdl.options(), format=ytdl.format_for(height),
                format_sort=["res:%d" % height, "fps", "vcodec:vp9"],
                merge_output_format="mp4", outtmpl=str(work / ("raw%02d.%%(ext)s" % rank)),
                download_ranges=download_range_func(None, [(max(start - 2, 0), end + 2)]),
                force_keyframes_at_cuts=False)
    try:
        with YoutubeDL(opts) as ydl:
            ydl.extract_info(url, download=True)
    except Exception as error:
        say("  Could not fetch that part: %s" % ytdl.explain(error))
        return None
    raw = next(iter(sorted(work.glob("raw%02d.*" % rank))), None)
    if raw is None:
        return None
    piece = work / ("piece%02d.mp4" % rank)
    offset = min(2, start)
    if media.cut(raw, piece, offset, offset + (end - start)):
        return raw                          # could not trim: the few extra seconds stay
    return piece


def notes(item, start, words, why):
    """Title ideas from what was said, a description with credit, hashtags."""
    said = " ".join(w for _s, _e, w in words).strip()
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", said) if 8 <= len(s.strip()) <= 70]
    quote = max(sentences, key=lambda s: ("!" in s, len(s)), default="")
    channel = item["channel"] or "YouTube"
    titles = ([u"“%s” — %s" % (quote.rstrip("."), channel)] if quote else []) + [
        "%s's best moment \U0001F633" % channel,
        "%s (%s)" % (item["title"][:70], timing.clock(start))]
    link = "%s&t=%ds" % (item["url"], start) if "?" in item["url"] else \
        "%s?t=%ds" % (item["url"], start)
    tag = re.sub(r"[^A-Za-z0-9]", "", channel).lower()
    lines = ["TITLE OPTIONS"] + ["  %d. %s" % (i, t[:95]) for i, t in enumerate(titles[:3], 1)]
    lines += ["", "DESCRIPTION", "From \"%s\" by %s." % (item["title"], channel),
              "Full video: %s" % link, "All credit to %s - go watch the full video!" % channel,
              "", "HASHTAGS", " ".join("#" + t for t in ["shorts", tag, "highlights"] if t),
              "", "WHY THIS MOMENT", ", ".join(why) or "the blend of every signal"]
    return "\n".join(lines) + "\n"


def estimate(duration, count, use_chat, was_live):
    """Seconds the whole job usually takes."""
    hours = max(duration, 60) / 3600.0
    return (hours * (40 + (120 if use_chat and was_live else 0))
            + count * timing.per("autoclip_clip"))
