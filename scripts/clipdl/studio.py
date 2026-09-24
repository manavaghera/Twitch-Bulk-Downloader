"""The Studio: trim one clip into a Short, and a weekly compilation video.

Both start from the clips already downloaded (the download history knows
them all). When a Shorts-only run deleted a clip's 16:9 file, it is fetched
again from Twitch into data/studio_cache - kept for a few clips, then tidied.

  trim_short     the best 20 s of a 60 s clip, as a Short (captions, branding)
  compilation    the week's top clips as one 16:9 video: an on-screen credit on
                 each clip, then a .txt with YouTube chapters and credits
"""

import shutil
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import branding, captions, facecams, media, timing, titles
from .config import DATA_DIR, MANIFEST_FILE, STOP
from .folders import saved_folder
from .shorts import SHORTS_FOLDER, make_short
from .util import load_json, sanitize, say

CACHE = DATA_DIR / "studio_cache"
KEEP_CACHED = 40
CREDIT_SECONDS = 4.5
CREDIT_ASS = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Credit,Arial Black,62,&H00FFFFFF,&H000000FF,&H00000000,&HB4000000,-1,0,0,0,100,100,0,0,3,14,0,1,60,60,70,1
Style: Link,Arial,38,&H00E0E0E0,&H000000FF,&H00000000,&HB4000000,0,0,0,0,100,100,0,0,3,10,0,1,60,60,28,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


# -- the clips we have -----------------------------------------------------------------
_read = {}                      # the download history, until the file changes


def _history():
    try:
        stamp = (str(MANIFEST_FILE), MANIFEST_FILE.stat().st_mtime)
    except OSError:
        return {}
    if _read.get("stamp") != stamp:
        _read.update(stamp=stamp, data=load_json(MANIFEST_FILE, {}))
    return _read["data"]


def library(days=None):
    """Downloaded clips, newest first: [{id, title, streamer, login, game, views,
    created_at, url, file, short, has_file, has_short}]."""
    data = _history()
    clips = (data or {}).get("clips") or {}
    since = datetime.now(timezone.utc) - timedelta(days=days) if days else None
    rows = []
    for clip_id, entry in clips.items():
        if entry.get("status") not in ("downloaded", "skipped-exists") or not entry.get("file"):
            continue
        if since and _when(entry.get("created_at")) < since:
            continue
        file = Path(entry["file"])
        short = file.parent / SHORTS_FOLDER / file.name
        rows.append({"id": clip_id, "title": entry.get("title") or "", "url": entry.get("url"),
                     "streamer": entry.get("streamer") or "", "game": entry.get("game") or "",
                     "login": titles.login_from(entry.get("url"), entry.get("streamer")),
                     "views": int(entry.get("views") or 0), "created_at": entry.get(
                         "created_at") or "", "updated_at": entry.get("updated_at") or "",
                     "file": file, "short": short, "has_file": file.exists(),
                     "has_short": short.exists()})
    return sorted(rows, key=lambda r: r["updated_at"], reverse=True)


def _when(stamp):
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def fetch(url, target, max_height=1080):
    """Download one clip again (its 16:9 file was deleted). None or a reason."""
    from yt_dlp import YoutubeDL
    from .config import ydl_format
    target.parent.mkdir(parents=True, exist_ok=True)
    options = {"outtmpl": str(target), "format": ydl_format(max_height),
               "format_sort": ["res", "fps"], "merge_output_format": "mp4", "quiet": True,
               "no_warnings": True, "noprogress": True, "noplaylist": True, "retries": 3,
               "overwrites": True}
    try:
        with YoutubeDL(options) as ydl:
            ydl.download([url])
    except Exception as error:                  # deleted clip, network, ...
        return str(error)[:160]
    return None if target.exists() else "no file was written"


def source_of(entry):
    """A 16:9 file of this clip: the downloaded one, or a fresh copy. None if gone."""
    if entry["file"].exists():
        return entry["file"]
    cached = CACHE / ("%s.mp4" % sanitize(entry["id"], 80))
    if cached.exists():
        return cached
    if not entry.get("url"):
        return None
    say("  Fetching %s again from Twitch (its 16:9 file was deleted)..." % entry["title"][:50])
    if fetch(entry["url"], cached):
        return None
    old = sorted(CACHE.glob("*.mp4"), key=lambda p: p.stat().st_mtime)
    for path in old[:-KEEP_CACHED]:
        path.unlink(missing_ok=True)
    return cached


# -- one Short, trimmed ----------------------------------------------------------------
def trim_short(entry, start, end, style="blur", with_captions=False, use_brand=False,
               target=None):
    """Make a Short of start..end of a clip - beside the clip's other Shorts, or at
    `target` (a preview). Returns (path, None) or (None, reason)."""
    source = source_of(entry)
    if source is None:
        return None, "the clip file is gone and could not be fetched again"
    started = time.time()
    work = Path(tempfile.mkdtemp(prefix="clipdl-studio-"))
    try:
        piece = work / "piece.mp4"
        problem = media.cut(source, piece, start, end)
        if problem:
            return None, problem
        box = facecams.region(entry["streamer"]) if style == "split" else None
        look = "split" if box else ("blur" if style == "split" else style)
        ass = None
        if with_captions and captions.available():
            ass = work / "captions.ass"
            if not captions.make_captions(piece, ass):
                ass = None
        brand = branding.for_short(work, force=True) if use_brand else None
        preview = target is not None
        target = Path(target or entry["file"].parent / SHORTS_FOLDER / (
            "%s (%d-%ds).mp4" % (entry["file"].stem, start, end)))
        problem = make_short(piece, target, look, box, ass, brand)
        if problem:
            return None, problem
        timing.record("trim", time.time() - started)
        if preview:
            return target, None
        suggestion = titles.suggest(entry["title"], entry["streamer"], entry["game"],
                                    clip_url=entry.get("url") or "", login=entry["login"])
        titles.write_sidecar(target, suggestion)
        return target, None
    finally:
        shutil.rmtree(work, ignore_errors=True)


# -- a compilation ---------------------------------------------------------------------
def _credit_text(text):
    return text.replace("\\", "").replace("{", "(").replace("}", ")")


def write_credit(path, rank, entry):
    link = "twitch.tv/%s" % entry["login"] if entry["login"] else "on Twitch"
    # One block, the name over the link ({\rLink} switches the style mid-line).
    body = ("Dialogue: 0,0:00:00.20,0:00:%05.2f,Credit,,0,0,0,,{\\fad(250,400)}%s\\N"
            "{\\rLink}%s\n" % (CREDIT_SECONDS, _credit_text("#%d  %s" % (rank, entry["streamer"])),
                               _credit_text(link)))
    path.write_text(CREDIT_ASS + body, encoding="utf-8")
    return path


def _stamp(seconds):
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    return ("%d:%02d:%02d" % (hours, rest // 60, rest % 60) if hours
            else "%d:%02d" % (rest // 60, rest % 60))


def compilation(entries, title, countdown=True, use_brand=False, folder=None):
    """The clips (best first) as one video with credits. Returns {"video", "notes",
    "chapters", "skipped"} - or raises ValueError when nothing could be made."""
    if not media.ffmpeg():
        raise ValueError("Making videos needs ffmpeg, and it is not installed.")
    ranked = list(enumerate(entries, 1))            # rank 1 = the best clip
    order = list(reversed(ranked)) if countdown else ranked
    work = Path(tempfile.mkdtemp(prefix="clipdl-compilation-"))
    folder = Path(folder or Path(saved_folder()[0]) / "Compilations")
    folder.mkdir(parents=True, exist_ok=True)
    name = sanitize("%s %s" % (date.today().isoformat(), title), 120)
    started = time.time()
    try:
        parts, chapters, skipped, clock = [], [], [], 0.0
        brand = branding.settings() if use_brand else None
        intro = branding.extra_path("intro", "h") if brand and brand["intro"] else None
        outro = branding.extra_path("outro", "h") if brand and brand["outro"] else None
        logo = branding.logo_path(brand) if brand else None
        overlay = ((logo, branding.POSITIONS[brand["logo_pos"]],
                    int(1920 * brand["logo_size"] / 200), float(brand["logo_opacity"]))
                   if logo else None)
        if intro:
            parts.append(intro)
            chapters.append((0.0, "Intro"))
            clock += media.probe(intro)["duration"]
        for position, (rank, entry) in enumerate(order, 1):
            if STOP.is_set():
                raise ValueError("Cancelled.")
            say("[%d/%d] %s - %s" % (position, len(order), entry["streamer"], entry["title"][:60]))
            source = source_of(entry)
            if source is None:
                skipped.append(entry["title"])
                say("  Skipped: the clip could not be fetched.")
                continue
            credit = write_credit(work / ("credit%02d.ass" % position), rank, entry)
            part = work / ("part%02d.mp4" % position)
            problem = media.fit(source, part, 1920, 1080, subtitles=[credit], overlay=overlay)
            if problem:
                skipped.append(entry["title"])
                say("  Skipped: %s" % problem)
                continue
            chapters.append((clock, "#%d %s - %s" % (rank, entry["streamer"], entry["title"][:60])))
            clock += media.probe(part)["duration"]
            parts.append(part)
        if not [p for p in parts if p not in (intro, outro)]:
            raise ValueError("None of the clips could be used.")
        if outro:
            parts.append(outro)
            chapters.append((clock, "Outro"))
        say("Joining %d pieces..." % len(parts))
        video = folder / (name + ".mp4")
        if media.join(parts, video):
            say("  Quick join failed - joining the slow way...")
            problem = media.join_encoding(parts, video)
            if problem:
                raise ValueError(problem)
        notes = folder / (name + ".txt")
        notes.write_text(description(title, chapters, entries), encoding="utf-8")
        timing.record("compilation", time.time() - started, len(order))
        say("Compilation ready: %s (%s long)" % (video, _stamp(media.probe(video)["duration"])))
        return {"video": video, "notes": notes, "chapters": chapters, "skipped": skipped}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def description(title, chapters, entries):
    """YouTube description: chapters (they need 0:00 first), then credit to everyone."""
    lines = [title, "", "CHAPTERS"] + ["%s %s" % (_stamp(at), text) for at, text in chapters]
    lines += ["", "CREDITS - go follow them!"]
    for rank, entry in enumerate(entries, 1):
        channel = ("https://twitch.tv/%s" % entry["login"] if entry["login"]
                   else "on Twitch")
        lines.append("#%d %s - %s  (clip: %s)" % (rank, entry["streamer"], channel,
                                                   entry.get("url") or "?"))
    games = sorted({e["game"] for e in entries if e["game"]})
    tags = ["#" + "".join(ch for ch in g.lower() if ch.isalnum()) for g in games[:3]]
    lines += ["", " ".join(tags + ["#twitch", "#gaming", "#highlights"])]
    return "\n".join(lines) + "\n"


def weekly_pick(days=7, top=10, game=None):
    """The most-viewed clips of the last `days` days, for the compilation maker."""
    rows = [r for r in library(days) if not game or r["game"] == game]
    seen, best = set(), []
    for row in sorted(rows, key=lambda r: r["views"], reverse=True):
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        best.append(row)
    return best[:top]


def recent_outputs(limit=6):
    folder = Path(saved_folder()[0]) / "Compilations"
    videos = sorted(folder.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True) \
        if folder.is_dir() else []
    return [(p, time.strftime("%d %b %H:%M", time.localtime(p.stat().st_mtime)))
            for p in videos[:limit]]
