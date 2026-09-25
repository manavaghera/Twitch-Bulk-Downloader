"""Auto clips: the best moments of a YouTube video or finished live stream, made
into ready-to-post Shorts - give a link and how many.

A clip is a play or a laugh, not just a loud stretch. Three looks run at once:

  listening   the whole sound track is transcribed: highlight words ("ace",
              "clutch", "let's go", "no way", laughing...), the game's announcer
              ("last player standing"), and the high end of the sound (shots,
              impacts) - plain loudness counts for very little
  watching    a small copy of the video: how much the picture moves (menus,
              lobbies and talking to camera count for less) and, in VALORANT,
              every kill you get (kills.py) - 3Ks, 4Ks, aces and clutches
  the chat    for past live streams, how fast the chat went and how hyped or
              amused it was, shifted a few seconds earlier (chat reacts late)
YouTube's "Most replayed" graph counts too, when the video has one. The best
stretches (moments.py) then get a second opinion from an AI model on this
computer, if Ollama is running (judge.py): it reads what was said and rates it -
a joke or a big play beats callouts and small talk.

Only the chosen stretches are downloaded - not the whole four-hour stream - each
checked against YouTube's own files so sound and picture line up
(synccheck.py), then made into a Short (blurred background or crop, captions,
branding) in <download folder>/YouTube/Auto clips/<video>/ - title ideas and
credit are kept by the app for uploading, not as files beside the clips.
"""

import json
import re
import shutil
import tempfile
import time
from pathlib import Path

from . import (branding, captions, highlights, judge, kills, media, moments, synccheck,
               timing, titles, ytdl, ytpiece)
from .config import STOP
from .folders import saved_folder
from .moments import SKIP_END, SKIP_START, blend, bumps, pick   # noqa: F401 (used by tests)
from .shorts import make_short
from .util import sanitize, say, thread_pool

CHAT_DELAY = 8                  # seconds chat reacts after the moment it reacts to
LABELS = {"replayed": "most replayed", "chat": "chat went wild", "hype": "what was said",
          "action": "action sound", "loud": "loud"}
CHAT_HYPE = re.compile(r"\b(?:lul|lol|lmao+|kekw|omegalul|icant|w+|pog\w*|clip\s*(?:it|that)|"
                       r"insane|no\s*way|ace|clutch|wha{2,}t+|holy)\b|[\U0001F602\U0001F923]|"
                       r"[!?]{3,}", re.I)
levels = highlights.loud_levels         # loudness a second (kept for older callers)


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
    """How much the chat of a past live stream went off, a second at a time: each
    message counts, a hyped or laughing one ("KEKW", "W", "clip it", "???") three
    times. None when there is no chat replay."""
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
        say("  This stream has no chat replay.")
        return None
    counts, messages = [0.0] * seconds, 0
    with files[0].open(encoding="utf-8") as handle:
        for line in handle:
            try:
                action = json.loads(line).get("replayChatItemAction") or {}
                offset = int(action.get("videoOffsetTimeMsec") or 0) // 1000 - CHAT_DELAY
            except (ValueError, AttributeError):
                continue
            if 0 <= offset < seconds:
                counts[offset] += 3.0 if CHAT_HYPE.search(_chat_text(action)) else 1.0
                messages += 1
    return moments.smooth(counts, 10) if messages >= 50 else None


def _chat_text(action):
    """The words and emoji of one chat replay message."""
    try:
        item = action["actions"][0]["addChatItemAction"]["item"]
        runs = next(iter(item.values()))["message"]["runs"]
    except (KeyError, IndexError, TypeError, StopIteration):
        return ""
    return " ".join(run.get("text") or (run.get("emoji") or {}).get("shortcuts", [""])[0]
                    for run in runs)


def get_sound(item, work):
    """The whole sound track: the quick way, else yt-dlp's. None if it cannot be had."""
    say("Getting the sound...")
    sound = ytpiece.fetch_sound(item["_info"], Path(work) / "sound.m4a")
    if sound is not None:
        return sound
    from yt_dlp import YoutubeDL
    opts = dict(ytdl.options(), format="worstaudio/bestaudio/worst",
                outtmpl=str(Path(work) / "sound.%(ext)s"))
    try:
        with YoutubeDL(opts) as ydl:
            ydl.extract_info(item["url"], download=True)
    except Exception as error:
        say("  Could not get the sound (%s)." % ytdl.explain(error)[:80])
        return None
    return next(iter(Path(work).glob("sound.*")), None)


def _hear(item, seconds, listen, extra_words, work):
    """Everything from the sound: {sound, hype, heard, words, action, loud}."""
    out = {"sound": get_sound(item, work), "hype": None, "heard": {}, "words": [],
           "action": None, "loud": None}
    if out["sound"] is None:
        return out
    if listen:
        out["hype"], out["heard"] = highlights.hype_curve(out["sound"], seconds, extra_words,
                                                          said=out["words"])
    out["action"] = highlights.action_curve(out["sound"], seconds)
    out["loud"] = highlights.loud_levels(out["sound"], seconds)
    return out


def _see(item, seconds, watch, valorant, work):
    """Everything from the picture: {video, motion, kills, dead}."""
    out = {"video": None, "motion": None, "kills": [], "dead": []}
    if not watch:
        return out
    if valorant:
        out["video"] = kills.watch_copy(item["_info"], Path(work))
        if out["video"] is not None:
            say("Looking for kills on screen...")
            out["kills"] = kills.find_kills(out["video"])
            out["dead"] = kills.dead_spans(out["video"]) if out["kills"] else []
            spectated = sum(1 for t, _s in out["kills"]
                            if any(a <= t <= b for a, b in out["dead"]))
            say("  %d kill(s) seen%s." % (len(out["kills"]), (
                " - %d of them a teammate's, while watching them after dying" % spectated)
                if spectated else ""))
    out["motion"] = highlights.motion_curve(item["_info"], seconds, work, out["video"])
    if out["video"] is None:
        out["video"] = next(iter(Path(work).glob("small.video")), None)
    return out


# -- choosing the moments ----------------------------------------------------------------------
def find_moments(url, count, length, use_chat=True, listen=True, watch=True,
                 extra_words=(), work=None, ask_ai=True):
    """(video info, [(start, end, score, why)]) - the best moments of a video."""
    item = ytdl.inspect(url, keep_info=True)
    if item["live"] in ("is_live", "is_upcoming"):
        raise ValueError("This stream is still live (or has not started). Run this after it "
                         "ends - YouTube keeps the whole stream.")
    seconds = int(item["duration"] or 0)
    if seconds < length:
        raise ValueError("The video is shorter than one clip.")
    valorant = kills.is_valorant(item)
    say("%s - %s long. Finding the best %d moment(s) of %d s%s..." % (
        item["title"][:60], timing.clock(seconds), count, length,
        " (VALORANT: counting kills)" if valorant and watch else ""))
    curves = {"replayed": replay_curve(item, seconds)}
    say("  Most replayed: %s" % ("yes" if curves["replayed"] else "YouTube has no graph for it"))
    with thread_pool(3) as pool:                # listening, watching and the chat at once
        hearing = pool.submit(_hear, item, seconds, listen, extra_words, work)
        seeing = pool.submit(_see, item, seconds, watch, valorant, work)
        chatting = (pool.submit(chat_curve, item["url"], seconds, work)
                    if use_chat and item["live"] == "was_live" else None)
        heard = _result(hearing, "Listening", {"sound": None, "hype": None, "heard": {},
                                                "words": [], "action": None, "loud": None})
        seen = _result(seeing, "Watching", {"video": None, "motion": None, "kills": [],
                                            "dead": []})
        curves["chat"] = _result(chatting, "The chat", None) if chatting else None
    curves.update(hype=heard["hype"], action=heard["action"], loud=heard["loud"])
    alone = sorted(t for t, phrases in heard["heard"].items()
                   if any("player standing" in p for p in phrases))
    plays = {}
    if seen["kills"]:
        curves["kills"], plays = kills.play_curve(seen["kills"], seconds, alone, seen["dead"])
    if not any(curves.values()):
        raise ValueError("Nothing to judge the video by: no Most-replayed graph, no chat "
                         "and no sound.")
    item["_sound"], item["_watch"] = heard["sound"], seen["video"]
    score = blend(curves, seconds, gate=seen["motion"])
    ai = judge.model() if ask_ai else None
    candidates = pick(score, count * 3 if ai else count, length)
    ready = _describe(candidates, curves, seconds, heard, plays)
    if ai:
        _second_opinion(ready, heard["words"], ai)
    ready.sort(key=lambda moment: -moment[2])
    return item, ready[:count]


def _result(future, what, instead):
    """A look's result - or, if it failed, `instead`, so the others still count.
    Cancel is not a failure: it stops the job."""
    try:
        return future.result()
    except ytpiece.Stopped:
        raise
    except Exception as error:              # a network hiccup, an odd file: go on without it
        say("  %s did not work (%s) - going on without it." % (what, str(error)[:80]))
        return instead


def _describe(candidates, curves, seconds, heard, plays):
    """[(start, end, score, why)]: edges moved to pauses, and what made each one."""
    ranked = moments.prepared({k: v for k, v in curves.items() if k != "kills"}, seconds)
    out = []
    for start, end, value in candidates:
        start, end = moments.snap(start, end, heard["words"], seconds)
        why = [label for t, label in sorted(plays.items()) if start - 2 <= t <= end]
        why += [LABELS[name] for name, curve in ranked.items()
                if name != "loud" and sum(curve[start:end]) / max(end - start, 1) >= 0.8]
        # The words that made this clip - said in it, or just after (the play came first).
        said = sorted({p for t in range(start - highlights.AFTER, end + highlights.BEFORE)
                       for p in heard["heard"].get(t, []) if "player standing" not in p})
        if said:
            why.append("said \"%s\"" % "\", \"".join(said[:4]))
        out.append((start, end, value, why))
    return out


def _second_opinion(ready, words, name):
    """Blend in the AI's rating of what was said (in place)."""
    stretches = []
    for start, end, _value, why in ready:
        text = " ".join(w for s, _e, w in words if start <= s < end)
        shown = [w for w in why if re.match(r"(\d+K|ACE|clutch)", w)]
        stretches.append((text, ", ".join(shown)))
    ratings = judge.rate_all(stretches, name)
    high = max([value for _s, _e, value, _w in ready] + [1e-6])
    for i, rating in enumerate(ratings):
        start, end, value, why = ready[i]
        signal = value / high                   # 1.0 for the strongest by the signals
        if rating is None:                      # nothing to judge: an average opinion
            ready[i] = (start, end, 0.5 * signal + 0.25, why)
            continue
        rated, kind, reason = rating
        ready[i] = (start, end, 0.5 * signal + 0.5 * rated,
                    why + ["AI: %s %d/10%s" % (kind or "rated", round(rated * 10),
                                                (" - " + reason) if reason else "")])


# -- making the clips ----------------------------------------------------------------------------------
def make_clips(url, count=5, length=30, style="blur", with_captions=True, use_brand=None,
               use_chat=True, listen=True, watch=True, extra_words=(), height=1080,
               ask_ai=True):
    """The whole job: find the moments, fetch just them, make the Shorts."""
    started = time.time()
    work = Path(tempfile.mkdtemp(prefix="clipdl-autoclip-"))
    fetcher = None
    try:
        item, chosen = find_moments(url, count, length, use_chat, listen, watch,
                                    extra_words, work, ask_ai)
        folder = Path(saved_folder()[0]) / "YouTube" / "Auto clips" / sanitize(
            item["title"], 80)
        folder.mkdir(parents=True, exist_ok=True)
        brand_config = branding.settings()
        brand = branding.for_short(work, brand_config) if (
            use_brand if use_brand is not None else brand_config["enabled"]) else None
        results = []
        # The next pieces download while this one is captioned and encoded.
        fetcher = thread_pool(2)
        pieces = [fetcher.submit(_fetch_piece, item, start, end, work, rank, height)
                  for rank, (start, end, _v, _w) in enumerate(chosen, 1)]
        for rank, (start, end, value, why) in enumerate(chosen, 1):
            if STOP.is_set():
                break
            say("[%d/%d] Moment %d at %s (%s)" % (rank, len(chosen), rank,
                                                  timing.clock(start), ", ".join(why) or "blend"))
            try:
                piece = pieces[rank - 1].result()
            except ytpiece.Stopped:
                say("Stopped.")
                break
            if piece is None:
                continue
            piece, sync = synccheck.ensure(Path(piece), start, item.get("_sound"),
                                           item.get("_watch"))
            say("  %s" % sync[0].upper() + sync[1:])
            channel = sanitize(item["channel"] or "clip", 20)
            target, number = folder / ("%d %s.mp4" % (rank, channel)), 1
            while target.exists():              # an earlier run's clip keeps its file
                number += 1
                target = folder / ("%d %s (%d).mp4" % (rank, channel, number))
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
            titles.save_notes([(target, notes(item, start, words, why + [sync]))])
            results.append({"file": str(target), "start": start, "end": end,
                            "score": value, "why": why})
            say("  Ready: %s" % target.name)
        if results:
            timing.record("autoclip_clip", time.time() - started, len(results))
        say("")
        say("Auto clips: %d Short(s) in %s" % (len(results), folder))
        return {"video": item, "folder": str(folder), "clips": results}
    finally:
        if fetcher is not None:             # pieces still downloading stop before tidying up
            fetcher.shutdown(wait=True, cancel_futures=True)
        shutil.rmtree(work, ignore_errors=True)


def _fetch_piece(item, start, end, work, rank, height):
    """Download just start..end: the quick way (only the bytes needed, in parallel),
    else yt-dlp's (it reads the video from the start - slow deep into long ones)."""
    piece = work / ("piece%02d.mp4" % rank)
    if item.get("_info"):
        try:
            return ytpiece.fetch(item["_info"], start, end, piece, height, work)
        except ValueError:
            say("  The quick way did not work for this video - using the slow one.")
    return _fetch_piece_slowly(item["url"], start, end, work, rank, height)


def _fetch_piece_slowly(url, start, end, work, rank, height):
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import download_range_func
    opts = dict(ytdl.options(), format=ytdl.format_for(height),
                format_sort=["res:%d" % height, "fps", "vcodec:vp9"],
                merge_output_format="mp4", outtmpl=str(work / ("raw%02d.%%(ext)s" % rank)),
                download_ranges=download_range_func(None, [(max(start - 2, 0), end + 2)]),
                # Exact cuts: cutting video at the nearest keyframe but sound at the
                # second put the voice seconds behind the picture.
                force_keyframes_at_cuts=True)
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
    plays = [w for w in why if re.match(r"(\d+K|ACE|clutch)", w)]
    titles = ([u"“%s” — %s" % (quote.rstrip("."), channel)] if quote else []) + (
        ["%s %s \U0001F92F" % (channel, plays[0])] if plays else []) + [
        "%s's best moment \U0001F633" % channel,
        "%s (%s)" % (item["title"][:70], timing.clock(start))]
    link = "%s&t=%ds" % (item["url"], start) if "?" in item["url"] else \
        "%s?t=%ds" % (item["url"], start)
    tag = re.sub(r"[^A-Za-z0-9]", "", channel).lower()
    lines = ["TITLE OPTIONS"] + ["  %d. %s" % (i, t[:95]) for i, t in enumerate(titles[:3], 1)]
    lines += ["", "DESCRIPTION", "From \"%s\" by %s." % (item["title"], channel),
              "Full video: %s" % link, "All credit to %s - go watch the full video!" % channel,
              "", "HASHTAGS", " ".join("#" + t for t in ["shorts", tag, "highlights"] if t),
              "", "WHY THIS MOMENT"] + (["  - %s" % w for w in why] or
                                         ["  - the blend of every signal"])
    return "\n".join(lines) + "\n"


def estimate(duration, count, use_chat, was_live, valorant=False, ask_ai=False):
    """Seconds the whole job usually takes."""
    hours = max(duration, 60) / 3600.0
    return (hours * (40 + (35 if valorant else 0) + (120 if use_chat and was_live else 0) / 2)
            + (count * 3 * 4 if ask_ai else 0) + count * timing.per("autoclip_clip"))
