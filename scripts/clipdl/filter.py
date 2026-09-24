"""Searching Twitch, then deciding which clips are worth keeping."""

import re
from datetime import datetime, timezone
from difflib import SequenceMatcher

from .config import (DUPLICATE_TITLE_MINUTES, DUPLICATE_TITLE_RATIO,
                     DUPLICATE_VOD_SECONDS, LANG_FILE, MAX_PAGES, PAGE_SIZE,
                     SKIP_DUPLICATES, TARGET_LANGUAGE, VELOCITY_FLOOR_HOURS,
                     VOD_FILE)
from .util import load_json, save_json, say

# ---------------------------------------------------------------------------
# The gameplay filter
#
# Twitch can tell us a clip's category but not what happens inside it, so a
# "Valorant" clip is just as likely to be a caster desk, a watch party or a
# streamer reading patch notes as it is someone playing. We cannot look at the
# video, but three pieces of free text read it surprisingly well:
#
#   1. the clip title           - "ACE!!!" vs "reacting to the new patch"
#   2. the title of the stream  - "ranked grind" vs "VCT WATCH PARTY"; this one
#      the clip was cut from      judges a whole broadcast at once
#   3. the channel name         - an esports broadcast channel is commentary by
#                                 default, and play only when it says so
#
# Each rule below is (regex, weight). Gameplay words add, talking words take
# away, and the total decides the verdict:
#
#      score >=  1   ->  "gameplay"   downloaded first
#      score <= -1   ->  "talk"       never downloaded
#      anything else ->  "maybe"      only used to top up a short run
#
# To tune the filter, add words to these lists - nothing else has to change.
# ---------------------------------------------------------------------------
def _rules(pairs):
    """Compile (pattern, weight) pairs once, case-insensitively."""
    return [(re.compile(pattern, re.IGNORECASE), weight) for pattern, weight in pairs]


# Words in a CLIP title that mean someone is playing.
PLAY_TITLE_RULES = _rules([
    (r"\bace\b", 2), (r"\bclutch", 2), (r"\b1v[2-5]\b", 2), (r"\b[3-9]k\b", 2),
    (r"victory royale", 2), (r"play of the game", 2), (r"team ?wipe", 2),
    (r"trick ?shot", 2), (r"no ?scope", 2), (r"\bpentakill\b", 2), (r"\bgoal\b", 2),
    (r"\bheadshot", 2), (r"wall ?bang", 2), (r"\baerial\b", 2), (r"\bmusty\b", 2),
    (r"flip reset", 2), (r"ceiling shot", 2), (r"\b(triple|double) tap\b", 2),
    (r"\bflick", 1), (r"\bdemo\b", 1), (r"\bsave[sd]?\b", 1), (r"\bkill[sd]?\b", 1),
    (r"\bfrag", 1), (r"\bult(imate)?\b", 1), (r"\bcombo\b", 1), (r"\bcarr(y|ies|ied)\b", 1),
    (r"\bsnipe[sd]?\b", 1), (r"\b360\b", 1), (r"\bjumpscare\b", 1), (r"\bcracked\b", 1),
    (r"\bwhiff", 1), (r"\bmiss(es|ed)\b", 1), (r"\boutplay", 1), (r"\b1v1\b", 1),
    (r"\bwins?\b", 1), (r"\bwon\b", 1), (r"\bgg\b", 1), (r"\bspeedrun", 1),
    (r"\brespawn", 1), (r"\blobby\b", 1), (r"\bspawn\b", 1), (r"\bboss\b", 1),
    (r"\brage ?quit\b", 1), (r"\bbuild battle\b", 1), (r"\bclutch(ed|es)?\b", 1),
])

# Words in a CLIP title that mean someone is talking, reacting or presenting.
TALK_TITLE_RULES = _rules([
    (r"watch ?part", 2), (r"co-?stream", 2), (r"\bcocast", 2), (r"patch ?notes?", 2),
    (r"\breact(s|ed|ing|ion)?\b", 2), (r"\binterview", 2), (r"post[- ]?match", 2),
    (r"\banalyst", 2), (r"\bcaster", 2), (r"press conference", 2), (r"\bpodcast", 2),
    (r"q ?& ?a\b", 2), (r"tier ?list", 2), (r"\bannounce(s|d|ment)?\b", 2),
    (r"\broster\b", 2), (r"\bsign(s|ed|ing) (with|for|to)\b", 2),
    (r"\bretir(e|es|ed|ement)\b", 2), (r"\bdrama\b", 2), (r"\bapolog", 2),
    (r"\bexpos(e|es|ed|ing)\b", 2), (r"\bresponds? to\b", 2), (r"\btalk(s|ing)? about\b", 2),
    (r"\bexplain(s|ing)?\b", 2), (r"story ?time", 2), (r"\brant\b", 2),
    (r"just chatting", 2), (r"\bdebate\b", 2), (r"\bnews\b", 2), (r"\bopinion", 2),
    (r"thoughts on", 2), (r"\bteaser\b", 2),
    (r"\bupdate\b", 1), (r"\bmeta\b", 1), (r"\breview", 1), (r"\btutorial\b", 1),
    (r"\bguide\b", 1), (r"\breading\b", 1), (r"\breveal(s|ed)?\b", 1), (r"\bstats\b", 1),
])

# Words in the STREAM title. These describe a whole broadcast, so a match
# applies to every clip cut from it and is weighted accordingly.
STREAM_TALK_RULES = _rules([
    (r"watch ?part", 3), (r"co-?stream", 3), (r"\bcocast", 3), (r"just chatting", 3),
    (r"\bpodcast", 3), (r"\breact(s|ing|ion)?\b", 3), (r"\bwatching\b", 3),
    (r"patch ?notes?", 3), (r"\binterview", 3), (r"q ?& ?a\b", 3), (r"tier ?list", 3),
    (r"\birl\b", 2), (r"\btalk(ing)?\b", 2), (r"\bnews\b", 2), (r"\breview", 2),
])

# Words in the STREAM title that mean the streamer was in a game.
STREAM_PLAY_RULES = _rules([
    (r"\branked\b", 1), (r"\bgrind", 1), (r"\bscrim", 1), (r"\bcustoms?\b", 1),
    (r"\bplaying\b", 1), (r"\bunrated\b", 1), (r"\bcomp(etitive)?\b", 1),
    (r"solo ?q", 1), (r"\bduo\b", 1), (r"\bqueue\b", 1), (r"\bmatchmaking\b", 1),
    (r"road to\b", 1), (r"warm ?up", 1), (r"\bchallenge\b", 1), (r"\bpubs?\b", 1),
])

# A tournament on screen, in either title: the picture is as likely to be the
# desk as the match, so we want proof of play before calling it gameplay.
ESPORTS_CONTEXT = re.compile(
    r"\b(vct|rlcs|owcs|algs|fncs|lcs|lec|lck|lpl|msi|iem|esl|blast|major|"
    r"invitational|championship|champions tour|grand ?finals?|playoffs?|"
    r"qualifier|group stage|map [1-5]|bo[35])\b", re.IGNORECASE)

# Channels that exist to broadcast rather than to play: same reasoning.
BROADCAST_CHANNEL = re.compile(
    r"(e-?sports?|^valorant|^rocketleague|^riot|^esl|^blast|^ogaming|"
    r"^twitchrivals|official|_tv$)", re.IGNORECASE)


def score_text(text, rules):
    """Total the weights of every rule that matches, and note the words hit."""
    total, hits = 0, []
    if not text:
        return 0, hits
    for pattern, weight in rules:
        found = pattern.search(text)
        if found:
            total += weight
            hits.append(found.group(0).strip().lower())
    return total, hits


def classify_clip(clip, stream_title):
    """Judge one clip: ('gameplay' | 'maybe' | 'talk', short reason)."""
    title = clip.get("title") or ""
    channel = clip.get("broadcaster_name") or ""

    play, _ = score_text(title, PLAY_TITLE_RULES)
    talk, talk_hits = score_text(title, TALK_TITLE_RULES)
    stream_play, _ = score_text(stream_title, STREAM_PLAY_RULES)
    stream_talk, stream_hits = score_text(stream_title, STREAM_TALK_RULES)

    score = play + stream_play - talk - stream_talk

    # On a tournament broadcast the default picture is the caster desk, so ask
    # for clearer proof before calling a clip gameplay. A real in-game moment
    # ("ACE", "5K", "TRIPLE TAP") still clears the higher bar, while a title
    # that says nothing either way lands in "maybe" instead of being thrown
    # out: a watch-along clip is a coin flip, not a talking clip.
    watching_esports = (BROADCAST_CHANNEL.search(channel)
                        or ESPORTS_CONTEXT.search(title)
                        or ESPORTS_CONTEXT.search(stream_title or ""))
    keep_at = 2 if watching_esports else 1

    if score >= keep_at:
        return "gameplay", ""
    if score <= -1:
        reason = ", ".join(dict.fromkeys(talk_hits + stream_hits))[:60]
        return "talk", reason or "esports broadcast, no sign of play"
    return "maybe", ""


def resolve_stream_titles(api, clips, vod_cache):
    """Look up the stream title behind any clip whose VOD we have not asked about."""
    unknown = sorted({
        clip["video_id"] for clip in clips
        if clip.get("video_id") and clip["video_id"] not in vod_cache
    })
    if not unknown:
        return 0
    vod_cache.update(api.video_titles(unknown))
    # VODs expire after a couple of months; remember the misses too, so a rerun
    # does not ask Twitch about the same dead ids all over again.
    for video_id in unknown:
        vod_cache.setdefault(video_id, "")
    return len(unknown)


# ---------------------------------------------------------------------------
# Finding clips, and filtering them by the streamer's channel language
# ---------------------------------------------------------------------------
def resolve_languages(api, clips, language_cache):
    """Fill in any broadcaster languages we do not already know."""
    unknown = sorted({
        clip["broadcaster_id"] for clip in clips
        if clip.get("broadcaster_id") and clip["broadcaster_id"] not in language_cache
    })
    if not unknown:
        return 0
    language_cache.update(api.channel_languages(unknown))
    # Channels Twitch did not return (banned, renamed, deleted) still need an
    # entry, otherwise every later page would look them up again.
    for broadcaster_id in unknown:
        language_cache.setdefault(broadcaster_id, "unknown")
    return len(unknown)


def save_language_cache(language_cache):
    """Persist the cache, minus the 'unknown' rows, which are worth retrying."""
    save_json(LANG_FILE, {
        key: value for key, value in language_cache.items() if value and value != "unknown"
    })


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------
def clip_rank(clip):
    """Sort key for 'most popular first': views, then the newer clip wins ties."""
    return (clip.get("view_count") or 0, clip.get("created_at") or "")


def clip_seconds(clip):
    """How long the clip runs, or None when Twitch did not say.

    Twitch reports this as a float, so a 28.5-second clip is normal.
    """
    try:
        seconds = float(clip.get("duration"))
    except (TypeError, ValueError):
        return None
    return seconds if seconds > 0 else None


def length_verdict(clip, min_seconds, max_seconds):
    """'ok', 'short' or 'long'. An unknown length counts as ok, never dropped."""
    seconds = clip_seconds(clip)
    if seconds is None:
        return "ok"
    if seconds < min_seconds:
        return "short"
    if seconds > max_seconds:
        return "long"
    return "ok"


def clip_made_at(clip):
    """When the clip was made, as a UTC datetime, or None if Twitch did not say."""
    try:
        return datetime.strptime(clip.get("created_at") or "",
                                 "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def clip_age_hours(clip, now):
    """How long ago the clip was made, in hours, or None if Twitch did not say."""
    made = clip_made_at(clip)
    return None if made is None else (now - made).total_seconds() / 3600.0


def velocity_rank(clip, now):
    """Sort key for 'trending now': views per hour since the clip was made.

    Raw views favour whatever has had the longest to accumulate them, so over a
    week a tired six-day-old clip outranks one from this morning that is going
    off. Dividing by age fixes that. The floor in the settings keeps a clip made
    minutes ago from dividing by nearly zero and winning by default; views break
    ties so two clips at the same rate order sensibly.
    """
    hours = clip_age_hours(clip, now)
    if hours is None:
        hours = VELOCITY_FLOOR_HOURS
    views = clip.get("view_count") or 0
    return (views / max(hours, VELOCITY_FLOOR_HOURS), views)


def ranker(ranking, now=None):
    """The sort key for a ranking mode, ready to hand to list.sort()."""
    if ranking == "trending":
        moment = now or datetime.now(timezone.utc)
        return lambda clip: velocity_rank(clip, moment)
    return clip_rank


# ---------------------------------------------------------------------------
# Near-duplicate detection
#
# A good highlight gets clipped by half the chat at once, so a search comes
# back with the same ten seconds five times over under five different titles.
# Two signals catch nearly all of it:
#
#   1. vod_offset - where in the stream the clip starts. Two clips from one VOD
#      that begin within a few seconds of each other are the same moment. This
#      is exact, and it is what catches most of them.
#   2. the title  - when Twitch gives no VOD position (the VOD expired, or the
#      clip was taken from a live view), fall back to two clips from the same
#      channel, made minutes apart, whose titles read nearly the same.
# ---------------------------------------------------------------------------
def normalise_title(title):
    """Lower case, letters and digits only, so punctuation and emoji stop mattering."""
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", (title or "").lower()).split())


class DuplicateIndex:
    """Remembers the clips kept so far, and recognises a repeat of one."""

    def __init__(self):
        self.by_vod = {}      # video_id -> [start offset in seconds, ...]
        self.by_channel = {}  # broadcaster_id -> [(normalised title, made at), ...]

    def duplicate_of(self, clip):
        """A short reason when this clip repeats one already kept, else None."""
        video_id = clip.get("video_id") or ""
        offset = clip.get("vod_offset")
        if video_id and offset is not None:
            for kept in self.by_vod.get(video_id, ()):
                if abs(kept - offset) <= DUPLICATE_VOD_SECONDS:
                    return "same moment as a clip already kept"
            return None  # the VOD position is trustworthy, so do not guess further

        title = normalise_title(clip.get("title"))
        made = clip_made_at(clip)
        if not title or made is None:
            return None
        for kept_title, kept_made in self.by_channel.get(clip.get("broadcaster_id"), ()):
            if abs((kept_made - made).total_seconds()) > DUPLICATE_TITLE_MINUTES * 60:
                continue
            if SequenceMatcher(None, title, kept_title).ratio() >= DUPLICATE_TITLE_RATIO:
                return "near-identical title from the same channel"
        return None

    def remember(self, clip):
        video_id = clip.get("video_id") or ""
        offset = clip.get("vod_offset")
        if video_id and offset is not None:
            self.by_vod.setdefault(video_id, []).append(offset)
            return
        title = normalise_title(clip.get("title"))
        made = clip_made_at(clip)
        if title and made is not None:
            self.by_channel.setdefault(clip.get("broadcaster_id"), []).append((title, made))


class ClipBuckets:
    """Everything a search found, split by how the gameplay filter read it."""

    def __init__(self):
        self.gameplay = []  # English channel, clearly someone playing
        self.maybe = []     # English channel, the text did not say either way
        self.others = []    # non-English channel, kept only to top up
        self.dropped = []   # (title, reason) for clips the filter threw out
        self.talk = []      # those clips themselves, English or not, offered only on request
        self.duplicates = []  # (title, reason) for repeats of a clip already kept
        # English clips of the wrong length. Held back rather than binned, so a
        # run that falls short can offer them instead of simply being short.
        self.out_of_range = []
        self.too_short = 0
        self.too_long = 0

        # Counts used by the shortfall report when a run cannot be filled.
        self.scanned = 0        # unique clips Twitch returned for the window
        self.already_had = 0    # of those, ones an earlier run already fetched
        self.not_allowed = 0    # skipped by the permission list
        self.exhausted = False  # True once Twitch has no more pages to give
        self.hit_page_cap = False

    def total_kept(self):
        return len(self.gameplay) + len(self.maybe) + len(self.others)


def collect_clips(api, game, wanted, started_at, ended_at, language_cache,
                  gameplay_only=True, skip_ids=frozenset(), dedupe=SKIP_DUPLICATES,
                  min_seconds=0, max_seconds=0, allow=None):
    """Page through /helix/clips until we have `wanted` gameplay clips.

    Twitch hands back clips most-viewed-first, so every bucket keeps that
    order and the best clips are always the ones that get downloaded.

    `skip_ids` holds clips earlier runs already downloaded. They are dropped
    before the filter ever sees them and paging simply carries on, so asking
    for 100 always means 100 clips you do not have yet - not 80 you already
    keep plus 20 new ones.

    `dedupe` throws out a second clip of a moment already kept. Because pages
    arrive most-viewed-first, the copy kept is the one that caught on, and the
    also-rans behind it are the ones dropped.

    `min_seconds` / `max_seconds` set the length worth editing with; 0 for
    either turns that end of the check off. Clips outside the range are set
    aside in `out_of_range` rather than binned, so the caller can offer them
    when the run would otherwise come up short.
    """
    buckets = ClipBuckets()
    duplicates = DuplicateIndex()
    seen = set()
    cursor = None
    pages = 0
    lookups = 0
    vod_lookups = 0

    vod_cache = load_json(VOD_FILE, {}) if gameplay_only else {}
    if not isinstance(vod_cache, dict):
        vod_cache = {}

    say("")
    say("Searching the top clips for '%s'..." % game.get("name", "?"))
    while len(buckets.gameplay) < wanted and pages < MAX_PAGES:
        params = {
            "game_id": game["id"],
            "first": PAGE_SIZE,
            "started_at": started_at,
            "ended_at": ended_at,
        }
        if cursor:
            params["after"] = cursor

        batch, cursor = api.get_page("/clips", params)
        pages += 1
        if not batch:
            buckets.exhausted = True
            break

        # Twitch occasionally repeats a clip across page boundaries.
        fresh = []
        for clip in batch:
            clip_id = clip.get("id")
            if not clip_id or clip_id in seen:
                continue
            seen.add(clip_id)
            if clip_id in skip_ids:
                # Already on an earlier run's books. Dropping it here rather
                # than after the filter also saves the language and VOD lookups.
                buckets.already_had += 1
                continue
            if allow is not None and not allow(clip):
                buckets.not_allowed += 1        # the permission list says no
                continue
            fresh.append(clip)

        lookups += resolve_languages(api, fresh, language_cache)
        if gameplay_only:
            vod_lookups += resolve_stream_titles(api, fresh, vod_cache)

        for clip in fresh:
            language = language_cache.get(clip.get("broadcaster_id", ""), "unknown")
            if gameplay_only:
                stream_title = vod_cache.get(clip.get("video_id") or "", "")
                verdict, reason = classify_clip(clip, stream_title)
            else:
                verdict, reason = "gameplay", ""

            if verdict == "talk":
                buckets.dropped.append((clip.get("title") or "", reason))
                buckets.talk.append(clip)
                continue

            # Only clips worth keeping go into the duplicate index, so a
            # rejected clip never masks a good one of the same moment.
            if dedupe:
                repeat = duplicates.duplicate_of(clip)
                if repeat:
                    buckets.duplicates.append((clip.get("title") or "", repeat))
                    continue
                duplicates.remember(clip)

            if language != TARGET_LANGUAGE:
                buckets.others.append(clip)
                continue

            # Length is checked after language so the clips held back for a
            # top-up are all ones the user would otherwise have accepted.
            if min_seconds or max_seconds:
                length = length_verdict(clip, min_seconds or 0,
                                        max_seconds or float("inf"))
                if length != "ok":
                    buckets.out_of_range.append(clip)
                    if length == "short":
                        buckets.too_short += 1
                    else:
                        buckets.too_long += 1
                    continue

            if verdict == "gameplay":
                buckets.gameplay.append(clip)
            else:
                buckets.maybe.append(clip)

        # Whatever this page set aside, named so the counts on screen add up.
        extra = "".join([
            (", %d already had" % buckets.already_had) if buckets.already_had else "",
            (", %d repeats" % len(buckets.duplicates)) if buckets.duplicates else "",
            (", %d wrong length" % len(buckets.out_of_range)) if buckets.out_of_range else "",
        ])
        if gameplay_only:
            say("  page %d: %d scanned, %d gameplay, %d maybe, %d talk skipped%s (target %d)"
                % (pages, len(seen), len(buckets.gameplay), len(buckets.maybe),
                   len(buckets.dropped), extra, wanted))
        else:
            say("  page %d: %d clips scanned, %d English found%s (target %d)"
                % (pages, len(seen), len(buckets.gameplay), extra, wanted))

        if not cursor:
            buckets.exhausted = True
            break  # no more pages in this window

    if lookups:
        save_language_cache(language_cache)
    if vod_lookups:
        save_json(VOD_FILE, vod_cache)
    buckets.scanned = len(seen)
    if pages >= MAX_PAGES and len(buckets.gameplay) < wanted:
        buckets.hit_page_cap = True
        say("  Stopped after %d pages to avoid hammering the API." % MAX_PAGES)
    return buckets
