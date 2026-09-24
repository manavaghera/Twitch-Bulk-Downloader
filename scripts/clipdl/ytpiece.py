"""Fetching part of a YouTube video fast: seconds, not the time it takes to play.

Asking ffmpeg for "34 s starting at 1:17:56" of a YouTube file makes it read
the video from the start, at about playback speed - over an hour for a moment
an hour in. So the app fetches only the bytes it needs, several requests at once:

  segmented (HLS)   finished live streams, and many videos, come as a list of
                    few-second pieces: download the pieces that cover the moment
  one file (DASH)   an .mp4 starts with an index ("sidx") of where every few
                    seconds begin: read the index, ask for just that byte range
Video and sound come separately and are joined, then cut to the exact second.
The whole sound track (for finding loud moments) comes the same way, in
parallel chunks. Cancel is checked between requests.
"""

import struct
import time
from pathlib import Path

import requests

from . import media
from .config import HTTP_TIMEOUT, STOP
from .util import say, thread_pool

WORKERS = 8
CHUNK = 8 * 1024 * 1024         # bytes per request: small enough that YouTube does not slow it
HEAD = 256 * 1024               # the start of an .mp4, where its index lives


class Stopped(Exception):
    """Cancel was pressed."""


# -- picking formats ------------------------------------------------------------------------
def _audio_rank(fmt):
    return (fmt.get("abr") or fmt.get("tbr") or 0, str(fmt.get("format_id")))


def choose(info, height=1080, protocol="m3u8_native"):
    """(video, audio) formats of one protocol, or None. Prefers H.264 (quick to
    edit) at the height asked for or the nearest below."""
    formats = [f for f in info.get("formats") or [] if f.get("protocol") == protocol
               and f.get("url")]
    if protocol == "https":
        formats = [f for f in formats if f.get("ext") in ("mp4", "m4a")]
    videos = [f for f in formats if f.get("vcodec") not in (None, "none") and f.get("height")
              and f["height"] <= height and f.get("acodec") in (None, "none", "")]
    audios = [f for f in formats if f.get("vcodec") in (None, "none") and not f.get("height")]
    if not videos or not audios:
        return None
    video = max(videos, key=lambda f: (f["height"], (f.get("vcodec") or "").startswith("avc"),
                                       f.get("tbr") or 0))
    return video, max(audios, key=_audio_rank)


# -- HLS: a list of short pieces ---------------------------------------------------------------
def _hls_pieces(session, fmt, start, end):
    """(init URL or None, [(start time, URL)]) of the pieces covering start..end."""
    text = session.get(fmt["url"], headers=fmt.get("http_headers"),
                       timeout=HTTP_TIMEOUT).text
    at, length, init, pieces = 0.0, None, None, []
    for line in text.splitlines():
        if line.startswith("#EXT-X-MAP") and 'URI="' in line:
            init = line.split('URI="', 1)[1].split('"', 1)[0]
        elif line.startswith("#EXTINF:"):
            length = float(line[8:].split(",", 1)[0])
        elif line and not line.startswith("#") and length is not None:
            if at + length > start and at < end:
                pieces.append((at, line))
            at += length
            length = None
    return init, pieces


def _fetch_all(session, urls, headers):
    def get(url):
        if STOP.is_set():
            raise Stopped()
        for attempt in range(3):
            try:
                response = session.get(url, headers=headers, timeout=HTTP_TIMEOUT)
                if response.status_code == 200:
                    return response.content
            except requests.RequestException:
                pass
        raise ValueError("YouTube did not send a piece of the video.")
    with thread_pool(WORKERS) as pool:   # the job's own Cancel reaches these
        return list(pool.map(get, urls))


def _hls_part(session, fmt, start, end, target):
    init, pieces = _hls_pieces(session, fmt, start, end)
    if not pieces:
        raise ValueError("That moment is not in the stream's pieces.")
    blobs = _fetch_all(session, ([init] if init else []) + [url for _at, url in pieces],
                       fmt.get("http_headers"))
    target.write_bytes(b"".join(blobs))
    return pieces[0][0]                     # the time the file starts at


# -- DASH: one .mp4 with an index at the start --------------------------------------
def _boxes(data, offset=0):
    """(type, start, size) of the top-level boxes in `data`."""
    while offset + 8 <= len(data):
        size, kind = struct.unpack(">I4s", data[offset:offset + 8])
        if size == 1 and offset + 16 <= len(data):
            size = struct.unpack(">Q", data[offset + 8:offset + 16])[0]
        if size < 8:
            return
        yield kind.decode("latin-1"), offset, size
        offset += size


def parse_sidx(data, at):
    """[(start s, end s, first byte, last byte)] from the "sidx" box at `at`."""
    version = data[at + 8]
    timescale = struct.unpack(">I", data[at + 16:at + 20])[0]
    if version == 0:
        earliest, first = struct.unpack(">II", data[at + 20:at + 28])
        cursor = at + 28
    else:
        earliest, first = struct.unpack(">QQ", data[at + 20:at + 36])
        cursor = at + 36
    count = struct.unpack(">H", data[cursor + 2:cursor + 4])[0]
    cursor += 4
    size = struct.unpack(">I", data[at:at + 4])[0]
    byte = at + size + first
    time = earliest / float(timescale)
    out = []
    for _ in range(count):
        reference, duration, _sap = struct.unpack(">III", data[cursor:cursor + 12])
        length = reference & 0x7FFFFFFF
        out.append((time, time + duration / float(timescale), byte, byte + length - 1))
        time += duration / float(timescale)
        byte += length
        cursor += 12
    return out


def _dash_part(session, fmt, start, end, target):
    headers = dict(fmt.get("http_headers") or {})
    head = _range(session, fmt["url"], headers, 0, HEAD - 1)
    boxes = {kind: (at, size) for kind, at, size in _boxes(head)}
    if "sidx" not in boxes:
        raise ValueError("no index in this file")
    at, size = boxes["sidx"]
    if at + size > len(head):               # a very long video: a bigger index
        head = _range(session, fmt["url"], headers, 0, at + size - 1)
    moov_end = boxes["moov"][0] + boxes["moov"][1] if "moov" in boxes else at
    wanted = [r for r in parse_sidx(head, at) if r[1] > start and r[0] < end]
    if not wanted:
        raise ValueError("That moment is not in the file's index.")
    first, last = wanted[0][2], wanted[-1][3]
    spans = [(b, min(b + CHUNK - 1, last)) for b in range(first, last + 1, CHUNK)]

    def get(span):
        if STOP.is_set():
            raise Stopped()
        return _range(session, fmt["url"], headers, *span)
    with thread_pool(WORKERS) as pool:   # the job's own Cancel reaches these
        body = b"".join(pool.map(get, spans))
    target.write_bytes(head[:moov_end] + body)
    return wanted[0][0]


def _range(session, url, headers, first, last):
    status = "no answer"
    for attempt in range(4):
        if attempt:
            time.sleep(attempt)             # a moment's rest: YouTube refuses bursts at times
        try:
            response = session.get(url, headers=dict(headers, Range="bytes=%d-%d" % (first, last)),
                                   timeout=HTTP_TIMEOUT)
            if response.status_code in (200, 206):
                return response.content
            status = "HTTP %d" % response.status_code
        except requests.RequestException as error:
            status = type(error).__name__
    raise ValueError("YouTube did not send the bytes asked for (%s)." % status)


# -- the parts put together ---------------------------------------------------------
def fetch(info, start, end, target, height=1080, work=None):
    """Save start..end (seconds) of a video as an .mp4 at `target`. Raises ValueError
    when neither quick way works (the caller can then fall back to yt-dlp)."""
    work = Path(work or Path(target).parent)
    stem = Path(target).stem                # its own temporary files: fetches run side by side
    session = requests.Session()
    for protocol, part in (("m3u8_native", _hls_part), ("https", _dash_part)):
        pair = choose(info, height, protocol)
        if not pair:
            continue
        try:
            starts = []
            for kind, fmt in zip(("video", "sound"), pair):
                starts.append(part(session, fmt, start, end,
                                   work / ("%s.%s.part" % (stem, kind))))
        except ValueError:
            continue
        problem = media.run(["-i", str((work / ("%s.video.part" % stem)).resolve()),
                             "-i", str((work / ("%s.sound.part" % stem)).resolve()),
                             "-filter_complex", exact_cut(start - starts[0], start - starts[1],
                                                          end - start),
                             "-map", "[v]", "-map", "[a]"] + _fine(media.video_args())
                            + ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                               str(Path(target).resolve())])
        for kind in ("video", "sound"):
            (work / ("%s.%s.part" % (stem, kind))).unlink(missing_ok=True)
        if not problem:
            return Path(target)
    raise ValueError("no quick way to fetch this part")


def exact_cut(video_from, sound_from, duration):
    """The ffmpeg filters that cut both parts to the same stretch, frame- and
    sample-exact. Each part is read from its own start (a whole piece, so a
    keyframe) and cut by its time from there - never by jumping into it: a jump
    lands on a keyframe, and the picture then started seconds after the sound,
    which players that ignore the gap show as the voice coming late."""
    return ("[0:v]setpts=PTS-STARTPTS,trim=start=%.3f:duration=%.3f,setpts=PTS-STARTPTS[v];"
            "[1:a]asetpts=PTS-STARTPTS,atrim=start=%.3f:duration=%.3f,asetpts=PTS-STARTPTS[a]"
            % (max(video_from, 0), duration, max(sound_from, 0), duration))


def _fine(args):
    """Encoder arguments at a higher quality: this piece is encoded again for the Short."""
    finer = {"-cq": "19", "-crf": "16", "-global_quality": "20"}
    return [finer.get(args[i - 1], arg) if i else arg for i, arg in enumerate(args)]


def fetch_sound(info, target):
    """The whole sound track in parallel chunks. Returns the file or None (the caller
    can then fall back to yt-dlp). The plain AAC version when there is one: the
    smaller HE-AAC one decodes a few hundredths of a second late, and this track
    is also what each clip's sound is checked against."""
    audios = [f for f in info.get("formats") or [] if f.get("protocol") == "https"
              and f.get("vcodec") in (None, "none") and f.get("url")
              and f.get("acodec") not in (None, "none")]
    plain = [f for f in audios if (f.get("acodec") or "").startswith("mp4a.40.2")]
    return fetch_whole(min(plain or audios, key=_audio_rank), target) if audios else None


def fetch_whole(fmt, target):
    """One whole format (a sound track, a small copy of the video) in parallel
    chunks. Returns the file, or None."""
    session = requests.Session()
    headers = dict(fmt.get("http_headers") or {})
    size = fmt.get("filesize")              # exact; "filesize_approx" is only a guess
    if not size:
        try:
            size = int(session.head(fmt["url"], headers=headers, timeout=HTTP_TIMEOUT,
                                    allow_redirects=True).headers.get("Content-Length") or 0)
        except requests.RequestException:
            size = 0
    if not size:
        return None
    spans = [(b, min(b + CHUNK - 1, size - 1)) for b in range(0, size, CHUNK)]

    def get(span):
        if STOP.is_set():
            raise Stopped()
        return _range(session, fmt["url"], headers, *span)
    try:
        with thread_pool(WORKERS) as pool:   # the job's own Cancel reaches these
            Path(target).write_bytes(b"".join(pool.map(get, spans)))
    except ValueError as error:
        say("  %s" % error)
        return None
    return Path(target)
