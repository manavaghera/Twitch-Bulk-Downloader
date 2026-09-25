"""Settings and paths. This is the file to open when tweaking behaviour."""

import threading
from pathlib import Path

# ---------------------------------------------------------------------------
# Settings. Tweak these if you want different behaviour.
# ---------------------------------------------------------------------------
HELIX = "https://api.twitch.tv/helix"              # Twitch API base URL
OAUTH_URL = "https://id.twitch.tv/oauth2/token"    # app access token endpoint

# This file is <project>\scripts\clipdl\config.py, so the project root is two
# levels up. SCRIPT_DIR stays pointed at \scripts\ because a config.json
# dropped beside the launcher is still honoured.
PACKAGE_DIR = Path(__file__).resolve().parent
SCRIPT_DIR = PACKAGE_DIR.parent
PROJECT_DIR = SCRIPT_DIR.parent if SCRIPT_DIR.name.lower() == "scripts" else SCRIPT_DIR

DATA_DIR = PROJECT_DIR / "data"                      # small JSON state files
DOWNLOAD_ROOT = PROJECT_DIR / "downloads"            # one sub-folder per game
CONFIG_FILE = DATA_DIR / "config.json"               # client id + secret
TOKEN_FILE = DATA_DIR / "token_cache.json"           # cached app access token
LANG_FILE = DATA_DIR / "language_cache.json"         # broadcaster_id -> language
MANIFEST_FILE = DATA_DIR / "download_manifest.json"  # per-clip outcome log
VOD_FILE = DATA_DIR / "vod_cache.json"               # video_id -> stream title

TARGET_LANGUAGE = "en"        # the channel language we keep
MAX_CLIPS = 1000              # highest number the user may ask for
PAGE_SIZE = 100              # Twitch allows at most 100 items per page
MAX_PAGES = 40                # safety net: at most 4000 clips scanned
MAX_CONCURRENT_DOWNLOADS = 5  # still gentle on Twitch's CDN
POLITE_DELAY = (0.3, 0.9)     # random seconds between starting downloads
MAX_TITLE_CHARS = 70          # keep file names readable
HTTP_TIMEOUT = 20             # seconds before an API call is considered dead
HTTP_ATTEMPTS = 5             # how many times to retry a failing API call

# Time windows offered in the menu: (label, hours).
TIME_WINDOWS = [
    ("Last 24 hours", 24),
    ("Last 7 days", 24 * 7),
    ("Last 30 days", 24 * 30),
]

# Ranking modes offered in the menu: (label, key used in the code).
RANKINGS = [
    ("Most views", "views"),
    ("Trending now (views per hour)", "trending"),
]

# What to end up with: (label, key). A Short is a 1080x1920 vertical copy made
# with ffmpeg after downloading, saved in a "Shorts" folder beside the clips.
# "short" deletes the 16:9 original once its Short is made; "both" keeps it.
OUTPUT_FORMATS = [
    ("Video - 16:9, full frame as streamed", "video"),
    ("Short - 9:16 vertical, 1080x1920", "short"),
    ("Both - the video and a Short of it", "both"),
]

# How a 16:9 clip becomes 9:16: (label, key). See shorts.py for the details.
SHORT_STYLES = [
    ("Blurred background - whole frame kept, nothing cut off", "blur"),
    ("Centre crop - fills the screen, the sides are cut off", "crop"),
    ("Facecam on top, gameplay below - each streamer's camera marked once", "split"),
]

# How long a clip should be to be worth editing with. Under ten seconds there
# is no room for a moment to land, and past a minute it stops being a clip.
# Clips outside the range are kept aside, not thrown away, so a run that cannot
# be filled can offer them instead of coming up short.
MIN_CLIP_SECONDS = 10
MAX_CLIP_SECONDS = 60

# Trending divides views by how old a clip is, so a clip made minutes ago would
# divide by nearly zero and win everything. The floor stops that: nothing counts
# as younger than this many hours.
VELOCITY_FLOOR_HOURS = 2.0

# Twitch hands back clips most-viewed-first, so a clip that is trending hard but
# has not overtaken the week's giants yet sits well down the list. Trending mode
# therefore collects this many times the asked-for number before re-ranking, or
# it would only be re-sorting the same top slice.
VELOCITY_OVERSCAN = 3
# ...and never fewer than this, because 3x a small run is still a small pool:
# asking for 20 trending clips should not mean ranking only the top 60.
VELOCITY_MIN_SCAN = 300

# The same highlight often gets clipped by several viewers at once. Two clips
# count as the same moment when they come from one VOD and start within this
# many seconds of each other...
SKIP_DUPLICATES = True
DUPLICATE_VOD_SECONDS = 45
# ...or, when Twitch gives no VOD position, when one channel produced two clips
# this close together whose titles read this much alike (1.0 being identical).
DUPLICATE_TITLE_MINUTES = 10
DUPLICATE_TITLE_RATIO = 0.85

# The default height we aim for. Twitch serves a clip at whatever the source
# stream ran at, so a 1080p target means "1080p when the streamer had it,
# otherwise the best they did have" - it never makes a 720p clip fail or upscale.
MAX_HEIGHT = 1080

# Quality choices offered in the menu: (label, height cap). 0 = no cap, so a
# clip from a 1440p or 4K broadcast comes down at 1440p or 4K, and a 1080p one
# at 1080p - every clip at the most it has. Most Twitch streams are 1080p or
# lower, so only clips from streamers broadcasting higher ever go above it.
QUALITIES = [
    ("Up to 1080p - smaller files, the usual Twitch maximum", MAX_HEIGHT),
    ("Maximum - the best each clip has, up to 4K", 0),
]

# The least a clip must have to be kept at all (0 = any). Unlike the cap above,
# this filters: most clips are 1080p or 720p, few are 1440p and fewer 4K, so a
# high minimum can leave a run short - and then the user is asked what to do.
MIN_QUALITIES = [
    ("Any quality", 0),
    ("720p or better", 720),
    ("1080p or better", 1080),
    ("1440p or better", 1440),
    ("4K only", 2160),
]


def ydl_format(max_height=MAX_HEIGHT):
    """yt-dlp format string for a height cap (0 = no cap).

    A single progressive mp4 stream is preferred because it needs no ffmpeg
    merge. The later options are fallbacks, and the bare "best" at the end
    means a clip is still fetched even when it is published at some odd size.

    Twitch also serves "portrait-*" renditions: a vertical crop of the clip.
    Sorting by resolution alone could pick one of those over the real 16:9
    clip, so they are ruled out everywhere except the last-resort "best".
    """
    landscape = "[format_id!*=portrait]"
    cap = "[height<=%d]" % max_height if max_height else ""
    return ("best{c}{l}[ext=mp4]/best{c}{l}/bestvideo{c}{l}+bestaudio/best"
            .format(c=cap, l=landscape))


def quality_text(max_height):
    return "up to %dp" % max_height if max_height else "maximum available (up to 4K)"


YDL_FORMAT = ydl_format(MAX_HEIGHT)

# Which kind of background job (download, trends) the current thread works
# for. The web page runs a download and a trend scan side by side; each has its
# own Cancel switch and its own log, found through this. Worker pools inherit
# it from the thread that made them (util.pool).
_thread_kind = threading.local()


def current_kind():
    return getattr(_thread_kind, "kind", None)


def set_kind(kind):
    _thread_kind.kind = kind


class _Stops:
    """The Cancel switch of whoever asks: STOP.is_set() in a download thread
    reads the download's switch, in a trend-scan thread the scan's. On the
    command line everything is one kind, so it acts like one plain Event."""

    def __init__(self):
        self._events = {}
        self._lock = threading.Lock()

    def event(self, kind=None):
        with self._lock:
            return self._events.setdefault(kind, threading.Event())

    def _mine(self):
        return self.event(current_kind())

    def is_set(self):
        return self._mine().is_set()

    def set(self):
        self._mine().set()

    def clear(self):
        self._mine().clear()

    def wait(self, timeout=None):
        return self._mine().wait(timeout)


# Set when the user presses Ctrl+C (or Cancel on the page) so threads can bail out quickly.
STOP = _Stops()
