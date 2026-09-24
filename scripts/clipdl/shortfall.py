"""Search first, then ask: what to do when a game has fewer clips than asked for.

A run looks for clips in the chosen window that pass every rule (English,
gameplay, the length, not downloaded before). When that finds fewer than
asked - 300 of 1,000 - nothing is downloaded yet. The search has already set
aside everything that only missed on one rule, and it looks further back in
time, so the choice can be made with real numbers:

  older        clips from before the window: the 7 days before a 24-hour run,
               the 30 days before a 7-day one (the same rules apply)
  lengths      clips that only failed the length rule
  non_english  clips from channels in other languages
  talking      clips the gameplay filter read as chatting or a caster desk

The web page shows them as boxes to tick, the command line asks one by one,
and only then does anything download. Quality is never a reason to be short:
every clip is downloaded at the best it has, up to the cap.
"""

from datetime import datetime, timedelta, timezone

from . import permissions, quality
from .config import LANG_FILE, MANIFEST_FILE, OUTPUT_FORMATS, SHORT_STYLES, TARGET_LANGUAGE, \
    VELOCITY_MIN_SCAN, VELOCITY_OVERSCAN, quality_text
from .filter import collect_clips, ranker
from .manifest import Manifest
from .report import report_filtered
from .util import load_json, say

STAMP = "%Y-%m-%dT%H:%M:%SZ"
# How far back "older clips" reach, for each window: (label, hours back from now).
OLDER = {24: ("the 7 days before", 24 * 7), 24 * 7: ("the 30 days before", 24 * 30)}
ORDER = ("older", "quality", "lengths", "non_english", "talking")
QUESTIONS = {
    "older": "Fill up with older clips from %s?",
    "quality": "Fill up with clips below the quality you asked for?",
    "lengths": "Fill up with clips of other lengths?",
    "non_english": "Fill up with clips from non-English channels?",
    "talking": "Fill up with clips the gameplay filter thought were talking?",
}


class Plan:
    """What a search found, and every way to make up a shortfall."""

    def __init__(self, request, buckets, rank_key):
        self.request = request
        self.buckets = buckets
        self.rank_key = rank_key
        self.base = list(buckets.gameplay[:request.wanted])
        if len(self.base) < request.wanted:
            # Clips the filter could not read either way make up the numbers first.
            self.base += buckets.maybe[:request.wanted - len(self.base)]
        self.pools = {"older": [], "quality": [],
                      "lengths": sorted(buckets.out_of_range, key=rank_key, reverse=True),
                      "non_english": list(buckets.others), "talking": list(buckets.talk)}
        self.older_label = ""
        self.manifest = None
        self.started = None                     # when the search began (for timing)

    @property
    def wanted(self):
        return self.request.wanted

    @property
    def missing(self):
        return max(self.wanted - len(self.base), 0)

    def options(self):
        """[(key, label, how many more it can add)] - only the ones that add any."""
        labels = {"older": "Older clips from %s" % self.older_label,
                  "quality": "Clips below %s (lower quality, same window)"
                             % _quality_text(self.request.min_height),
                  "lengths": "Clips of other lengths (%d shorter, %d longer)"
                             % (self.buckets.too_short, self.buckets.too_long),
                  "non_english": "Clips from non-English channels",
                  "talking": "Clips the gameplay filter thought were talking"}
        if self.request.min_height:         # only the window's own clips were looked up
            for key in ("lengths", "non_english", "talking"):
                labels[key] += " - any quality"
        return [(key, labels[key], min(len(self.pools[key]), self.missing))
                for key in ORDER if self.pools[key] and self.missing]

    def question(self, key):
        return QUESTIONS[key] % self.older_label if key == "older" else QUESTIONS[key]

    def choose(self, fills=(), quiet=False):
        """The clips to download: what was found, then the chosen fills in order."""
        chosen = list(self.base)
        have = {clip.get("id") for clip in chosen}
        for key in ORDER:
            if key not in fills:
                continue
            added = 0
            for clip in self.pools[key]:
                if len(chosen) >= self.wanted:
                    break
                if clip.get("id") not in have:
                    chosen.append(clip)
                    have.add(clip.get("id"))
                    added += 1
            if added and not quiet:
                say("Added %d %s." % (added, {"older": "older clip(s)",
                                              "quality": "lower-quality clip(s)",
                                              "lengths": "clip(s) of other lengths",
                                              "non_english": "non-English clip(s)",
                                              "talking": "clip(s) marked as talking"}[key]))
        return chosen

    def summary(self):
        """"300 of 1,000 found" and the rules that decided it, for the page."""
        request = self.request
        rules = [request.window_label.lower()]
        if request.min_seconds or request.max_seconds:
            rules.append("%d-%d s" % (request.min_seconds, request.max_seconds))
        if request.min_height:
            rules.append(_quality_text(request.min_height))
        rules.append("English channels")
        if request.gameplay_only:
            rules.append("gameplay only")
        if request.history_mode == "new":
            rules.append("new clips only")
        return rules


def _quality_text(height):
    return "4K" if height >= 2160 else "%dp" % height


def apply_quality(plan):
    """Keep only clips at the minimum quality; the rest become the "quality" fill."""
    request = plan.request
    candidates = plan.buckets.gameplay + plan.buckets.maybe
    good, low = quality.split(candidates, request.min_height, request.wanted)
    plan.base = good[:request.wanted]
    plan.pools["quality"] = low
    say("Quality   : %d clip(s) at %s or better, %d below it"
        % (len(good), _quality_text(request.min_height), len(low)))


def _window(hours, back_to_hours=None):
    ended = datetime.now(timezone.utc).replace(microsecond=0)
    started = ended - timedelta(hours=hours)
    if back_to_hours is None:
        return started.strftime(STAMP), ended.strftime(STAMP)
    return (ended - timedelta(hours=back_to_hours)).strftime(STAMP), started.strftime(STAMP)


def _header(request, started_at, ended_at, known_ids):
    say("")
    say("Game      : %s" % request.game_name)
    say("Clips     : up to %d" % request.wanted)
    say("Window    : %s  (%s -> %s UTC)" % (request.window_label, started_at, ended_at))
    say("Ranking   : %s" % request.ranking_label)
    say("Length    : %s" % ("%d to %d seconds" % (request.min_seconds, request.max_seconds)
                            if request.min_seconds or request.max_seconds else "any length"))
    say("Language  : channels set to '%s' only" % TARGET_LANGUAGE)
    say("Filter    : %s" % ("gameplay only - talking clips are skipped"
                            if request.gameplay_only else "off - every clip in the window"))
    say("Quality   : %s - never more than the streamer broadcast at"
        % quality_text(request.max_height))
    say("Folder    : %s" % request.folder)
    output = {key: label for label, key in OUTPUT_FORMATS}[request.output]
    if request.output != "video":
        style = {key: label for label, key in SHORT_STYLES}[request.short_style]
        output += "  (%s)" % style.split(" - ")[0].lower()
    say("Format    : %s" % output)
    if known_ids:
        say("History   : %s" % (
            "skipping %d clip(s) from earlier runs" % len(known_ids)
            if request.history_mode == "new"
            else "including earlier runs - deleted files get fetched again"))


def find(api, request):
    """Search the window and, when it comes up short, the time before it.
    Returns a Plan, or None when there is nothing at all (after saying why)."""
    manifest = Manifest(MANIFEST_FILE)
    known_ids = manifest.known_ids(request.game_name)
    skip_ids = known_ids if request.history_mode == "new" else frozenset()
    started_at, ended_at = _window(request.window_hours)
    _header(request, started_at, ended_at, known_ids)

    # Language cache survives across runs, so repeat streamers cost no API call.
    language_cache = load_json(LANG_FILE, {})
    language_cache = language_cache if isinstance(language_cache, dict) else {}
    if language_cache:
        say("Cache     : %d known channel languages loaded" % len(language_cache))

    # Trending re-ranks what the search found, and the search returns clips in
    # view order, so the top slice alone would be the same clips in a different
    # sequence. Collecting several times the target first is what lets a clip
    # Twitch buried at number 300 come out on top.
    target = request.wanted
    if request.ranking == "trending":
        target = max(request.wanted * VELOCITY_OVERSCAN, VELOCITY_MIN_SCAN)
    if request.min_height:
        # Some will not be sharp enough: find extra to choose from.
        target = max(target, request.wanted * 2)
    allow = permissions.allow_filter(request.streamer_mode)
    buckets = collect_clips(api, request.game, target, started_at, ended_at, language_cache,
                            request.gameplay_only, skip_ids, min_seconds=request.min_seconds,
                            max_seconds=request.max_seconds, allow=allow)
    report_filtered(buckets)

    rank_key = ranker(request.ranking)
    for bucket in (buckets.gameplay, buckets.maybe, buckets.others, buckets.talk):
        bucket.sort(key=rank_key, reverse=True)
    plan = Plan(request, buckets, rank_key)
    plan.manifest = manifest
    if request.min_height:
        apply_quality(plan)

    if plan.missing and request.window_hours in OLDER:
        label, back = OLDER[request.window_hours]
        plan.older_label = label
        say("")
        say("Found %d of %d in the %s. Looking at %s for the other %d..."
            % (len(plan.base), request.wanted, request.window_label.lower(), label,
               plan.missing))
        older_from, older_to = _window(request.window_hours, back)
        older = collect_clips(api, request.game, plan.missing, older_from, older_to,
                              language_cache, request.gameplay_only, skip_ids,
                              min_seconds=request.min_seconds,
                              max_seconds=request.max_seconds, allow=allow)
        pool = older.gameplay + older.maybe
        pool.sort(key=rank_key, reverse=True)
        if request.min_height:
            pool, _low = quality.split(pool, request.min_height, plan.missing,
                                       label="Checking older clips")
        plan.pools["older"] = pool

    if not plan.base and not any(plan.pools.values()):
        _nothing(request, buckets)
        return None
    if plan.missing:
        say("")
        say("Found %d usable clip(s); you asked for %d." % (len(plan.base), request.wanted))
        for _key, label, count in plan.options():
            say("  %-58s +%d" % (label, count))
        if not plan.options():
            say("There are no other clips to fill up with.")
    return plan


def _nothing(request, buckets):
    window = request.window_label.lower()
    say("")
    if buckets.already_had:
        say("Nothing new for '%s' in the %s window." % (request.game_name, window))
        say("All %d clip(s) Twitch has for that window are ones you already"
            % buckets.scanned)
        say("downloaded. Try a longer window, or run this again tomorrow.")
    elif buckets.dropped:
        say("Every clip for '%s' in the %s window looked like talking, not playing."
            % (request.game_name, window))
        say("Try a longer window, or turn the gameplay filter off to keep them.")
    else:
        say("No clips at all for '%s' in the %s window." % (request.game_name, window))
        say("Try a longer window, or a more popular category.")
