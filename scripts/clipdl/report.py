"""Everything printed once the searching and downloading is done."""

import time

from .config import MANIFEST_FILE, MAX_HEIGHT, MAX_PAGES, STOP, quality_text
from .util import describe_height, human_size, say

# ---------------------------------------------------------------------------
# Final report
# ---------------------------------------------------------------------------
def _examples(label, entries, limit=5):
    """One heading plus a few examples, so a filter can be sanity-checked."""
    if not entries:
        return
    say("")
    say(label % len(entries))
    for title, reason in entries[:limit]:
        say("   - %s   [%s]" % (title[:60], reason))
    if len(entries) > limit:
        say("   ... and %d more." % (len(entries) - limit))


def report_filtered(buckets):
    """Show what the filters threw out, with a few examples to check."""
    _examples("Filter    : skipped %d clip(s) that read as talking, not playing:",
              buckets.dropped)
    _examples("Repeats   : skipped %d clip(s) of a moment already kept:",
              buckets.duplicates)
    if getattr(buckets, "not_allowed", 0):
        say("")
        say("Permission: skipped %d clip(s) from streamers your permission list rules out."
            % buckets.not_allowed)
    if buckets.out_of_range:
        say("")
        say("Length    : set aside %d clip(s) of the wrong length "
            "(%d too short, %d too long)."
            % (len(buckets.out_of_range), buckets.too_short, buckets.too_long))
        say("            They are offered as a top-up if this run falls short.")


def report_shortfall(wanted, chosen, buckets, game_name, window_label,
                     gameplay_only, history_mode):
    """Explain a run that could not reach the number the user asked for.

    Asking for 500 and getting 132 is not a failure worth guessing about, so
    account for every clip the window held: how many Twitch had, how many were
    already downloaded, how many were new, and how many are simply missing.
    """
    missing = wanted - len(chosen)
    if missing <= 0:
        return

    window = window_label.lower()
    say("")
    say("=" * 66)
    say("NOT ENOUGH CLIPS")
    say("=" * 66)
    say("  You asked for              : %d clip(s)" % wanted)
    say("  Clips in the %-14s: %d found in total" % (window, buckets.scanned))
    if buckets.already_had:
        say("  Already downloaded before  : %d  (skipped - you still have these)"
            % buckets.already_had)
    say("  New clips this run         : %d" % len(chosen))
    say("  Missing                    : %d" % missing)

    # Where the rest of the window went, so the numbers above add up. A bucket
    # can have been dipped into for a top-up, so only what was left is counted.
    taken = {clip.get("id") for clip in chosen}
    def unused(bucket):
        return sum(1 for clip in bucket if clip.get("id") not in taken)

    leftovers = []
    if gameplay_only and buckets.dropped:
        leftovers.append("%d read as talking, not gameplay" % len(buckets.dropped))
    if buckets.duplicates:
        leftovers.append("%d were repeats of the same moment" % len(buckets.duplicates))
    if unused(buckets.out_of_range):
        leftovers.append("%d were the wrong length" % unused(buckets.out_of_range))
    if unused(buckets.others):
        leftovers.append("%d from non-English channels" % unused(buckets.others))
    if leftovers:
        say("")
        say("  The rest of the window was filtered out: %s." % ", ".join(leftovers))

    say("")
    if buckets.hit_page_cap:
        say("  Why: the search stopped at the %d-page safety cap before running" % MAX_PAGES)
        say("       out of clips, so there may be more further down.")
        say("  Try: raising MAX_PAGES in the settings block near the top of this file.")
    elif buckets.exhausted and buckets.already_had >= missing:
        say("  Why: %s does not have %d clips in the %s that you have not"
            % (game_name, wanted, window))
        say("       already downloaded - you hold most of this window already.")
        say("  Try: a longer window, or come back tomorrow for the new ones.")
    elif buckets.exhausted:
        say("  Why: Twitch has no more clips for %s in the %s - the whole"
            % (game_name, window))
        say("       window has been scanned and that is everything it holds.")
        say("  Try: a longer time window, or a busier category.")
    else:
        say("  Why: the search ran out of clips before reaching your number.")
        say("  Try: a longer time window.")

    if gameplay_only and buckets.dropped:
        say("  Also: answering 'n' to the gameplay question keeps the %d talking"
            % len(buckets.dropped))
        say("        clip(s) the filter threw out.")


def report(jobs, manifest, folder, started_monotonic, max_height=MAX_HEIGHT):
    """Print the end-of-run summary."""
    downloaded = skipped = failed = cancelled = 0
    run_bytes = 0
    failures = []
    heights = {}
    for job in jobs:
        entry = manifest.data["clips"].get(job.clip_id) or {}
        # No entry at all means the job never ran, which only happens after Ctrl+C.
        status = entry.get("status") or ("cancelled" if STOP.is_set() else "failed")
        if status == "downloaded":
            downloaded += 1
            run_bytes += int(entry.get("bytes") or 0)
            height = int(entry.get("height") or 0)
            heights[height] = heights.get(height, 0) + 1
        elif status == "skipped-exists":
            skipped += 1
        elif status == "cancelled":
            cancelled += 1
        else:
            failed += 1
            failures.append((job.path.name, entry.get("reason", "unknown")))

    # Size of everything in the game folder, not just this run - the 16:9
    # videos and the Shorts beside them (a Shorts-only run keeps only those).
    folder_bytes, counts = 0, {}
    for kind, pattern in (("video", "*.mp4"), ("Short", "Shorts/*.mp4")):
        for path in folder.glob(pattern):
            try:
                folder_bytes += path.stat().st_size
                counts[kind] = counts.get(kind, 0) + 1
            except OSError:
                pass
    in_folder = " + ".join("%d %s%s" % (n, kind, "" if n == 1 else "s")
                           for kind, n in counts.items()) or "no videos"

    elapsed = time.monotonic() - started_monotonic
    say("")
    say("=" * 66)
    say("SUMMARY")
    say("=" * 66)
    say("  Downloaded this run : %d  (%s)" % (downloaded, human_size(run_bytes)))
    if heights:
        # Twitch only has the quality the streamer broadcast at, so a run asking
        # for 1080p is a mix. Spelling it out saves wondering why some look soft.
        tally = ", ".join("%d at %s" % (count, describe_height(height))
                          for height, count in sorted(heights.items(), reverse=True))
        say("  Quality             : %s  (asked for %s)" % (tally, quality_text(max_height)))
    say("  Already had         : %d" % skipped)
    say("  Skipped / failed    : %d" % failed)
    if cancelled:
        say("  Cancelled           : %d" % cancelled)
    say("  Folder              : %s" % folder)
    say("  Clips in folder     : %s, %s on disk" % (in_folder, human_size(folder_bytes)))
    say("  Time taken          : %d min %d s" % (int(elapsed // 60), int(elapsed % 60)))
    say("  Manifest            : %s" % MANIFEST_FILE)

    if failures:
        say("")
        say("  Clips that could not be downloaded (deleted, sub-only, region locked):")
        for name, reason in failures[:15]:
            say("    - %s  ->  %s" % (name, reason))
        if len(failures) > 15:
            say("    ... and %d more, all listed in the manifest." % (len(failures) - 15))
