"""More game research: plain-language summaries, movers, "where would I rank?",
language gaps, a game's channel sizes, and its tags and title words.

Everything reads stats_db. Where a figure rests on a partial count, the result
says so, so the page can say so too.
"""

import math
import time

import pandas as pd

from . import research as rs
from . import stats_db as db
from .stats_bucket import HIST_EDGES, LANGUAGE_TAGS
from .web import is_game

HIST_LABELS = ["0", "1", "2", "3–4", "5–9", "10–24", "25–49", "50–99", "100–249", "250–999",
               "1,000+"]


def icons():
    """{game_key: Twitch box art template}."""
    return db.icons()


def _latest(platform):
    return db.latest_ts(platform)


def _now_rows(platforms):
    """{game_key: {viewers, channels}} summed over the latest snapshot of each platform."""
    out = {}
    for platform in platforms:
        ts = _latest(platform)
        if not ts:
            continue
        for key, viewers, channels in db.query(
                "SELECT game_key, viewers, channels FROM game_samples WHERE platform = ?"
                " AND ts = ?", (platform, ts)):
            row = out.setdefault(key, {"viewers": 0, "channels": 0})
            row["viewers"] += viewers
            row["channels"] += channels
    return out


# ---------------------------------------------------------------------------
# Movers: biggest changes vs about a day ago
# ---------------------------------------------------------------------------
def movers(platforms, min_viewers=1000, top=8):
    """(gainers, losers, label). Compares the latest snapshot with the one closest
    to 24 hours earlier, or with the oldest one at least 3 hours old."""
    platforms = [p for p in platforms if p != "youtube"]
    now, current, before = time.time(), {}, {}
    label = None
    for platform in platforms:
        latest = _latest(platform)
        if not latest:
            continue
        old = db.query("SELECT ts FROM runs WHERE platform = ? AND ts <= ? ORDER BY"
                       " ABS(ts - ?) LIMIT 1", (platform, latest - 3 * 3600, latest - 86400))
        if not old:
            continue
        ref = old[0][0]
        hours = (latest - ref) / 3600
        label = "24 hours ago" if hours >= 22 else "%d hours ago" % round(hours)
        for store, ts in ((current, latest), (before, ref)):
            for key, viewers in db.query("SELECT game_key, viewers FROM game_samples WHERE"
                                         " platform = ? AND ts = ?", (platform, ts)):
                store[key] = store.get(key, 0) + viewers
    if not label:
        return pd.DataFrame(), pd.DataFrame(), None
    names = rs.names()
    rows = []
    for key in set(current) | set(before):
        now_v, then_v = current.get(key, 0), before.get(key, 0)
        name = names.get(key, key)
        if max(now_v, then_v) < min_viewers or not is_game(name):
            continue
        rows.append({"key": key, "name": name, "now": now_v, "before": then_v,
                     "change": now_v - then_v,
                     "pct": (now_v / then_v - 1) * 100 if then_v else None})
    df = pd.DataFrame(rows)
    if df.empty:
        return df, df, label
    gainers = df[df["change"] > 0].sort_values("change", ascending=False).head(top)
    losers = df[df["change"] < 0].sort_values("change").head(top)
    return gainers, losers, label


# ---------------------------------------------------------------------------
# Where would I rank?
# ---------------------------------------------------------------------------
def _hist(text):
    try:
        counts = [int(x) for x in (text or "").split(",")]
    except ValueError:
        return None
    return counts if len(counts) == len(HIST_EDGES) else None


def channels_above(counts, n):
    """Channels with more than n viewers, from the size buckets (spread evenly
    inside the bucket that n falls in)."""
    above = 0.0
    for i, count in enumerate(counts):
        low = HIST_EDGES[i]
        high = HIST_EDGES[i + 1] if i + 1 < len(HIST_EDGES) else None
        if low > n:
            above += count
        elif high is not None and low <= n < high and high - low > 1:
            above += count * (high - 1 - n) / float(high - low)
    return above


def streamer_fit(platform, my_viewers, min_audience=500, games_only=True):
    """Where a channel with `my_viewers` viewers would sit in each game's
    directory (sorted by viewers) right now, best spots first.

    exact     every channel above you was counted: you stream at or above the
              smallest channel the broad pass reaches, or the game was counted in full
    open      the game's viewers outside its single biggest channel - the people
              actually browsing the category rather than following one star
    Games with fewer than 5 channels, or where one channel holds most of the
    viewers, are left out: a top spot there is next to one streamer's audience.
    """
    ts = _latest(platform)
    if not ts:
        return pd.DataFrame()
    cutoff = float(db.get_meta("cutoff_" + platform, 0) or 0)
    names, rows = rs.names(), []
    for key, viewers, channels, hist, full, top1 in db.query(
            "SELECT game_key, viewers, channels, hist, full, top1 FROM game_samples WHERE"
            " platform = ? AND ts = ? AND viewers >= ?", (platform, ts, min_audience)):
        counts = _hist(hist)
        name = names.get(key, key)
        if counts is None or (games_only and not is_game(name)):
            continue
        if channels < 5 or (top1 or 0) > 0.6 * viewers or viewers - (top1 or 0) < min_audience:
            continue
        exact = full == 1 or my_viewers >= cutoff
        position = int(round(channels_above(counts, my_viewers))) + 1
        rows.append({"key": key, "name": name, "position": position, "exact": exact,
                     "channels": channels, "viewers": viewers, "open": viewers - (top1 or 0),
                     "tier": 0 if position <= 12 else 1 if position <= 30 else
                     2 if position <= 100 else 3})
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    # Best: near the top of a directory lots of people browse.
    return df.sort_values(["tier", "open"], ascending=[True, False]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Language gaps
# ---------------------------------------------------------------------------
def language_gap(platforms, period, lang, min_viewers=200, games_only=True):
    """Games whose `lang` audience is big compared with the channels streaming in it.

    gap = share of the game's viewers watching `lang` streams divided by the share
    of its channels streaming in `lang`; 2.0 means twice the audience per channel.
    """
    since = int(time.time() - (rs.PERIODS[period] or 2 * 86400))
    rows = db.languages(list(platforms), since)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["key", "lang", "viewers", "channels"])
    cycles = db.query("SELECT COUNT(DISTINCT ts) FROM lang_samples WHERE platform IN (%s) AND"
                      " ts > ?" % ",".join("?" * len(platforms)), tuple(platforms) + (since,))
    cycles = max(cycles[0][0], 1)
    totals = df.groupby("key")[["viewers", "channels"]].sum()
    mine = df[df["lang"] == lang].set_index("key")
    names = rs.names()
    out = []
    for key, row in mine.iterrows():
        total = totals.loc[key]
        name = names.get(key, key)
        avg = row["viewers"] / cycles
        # Two channels at least: one big streamer alone is not a gap, it is a star.
        if avg < min_viewers or row["channels"] / cycles < 2 or \
                (games_only and not is_game(name)):
            continue
        viewer_share = row["viewers"] / total["viewers"]
        channel_share = row["channels"] / total["channels"]
        out.append({"key": key, "name": name, "viewers": avg,
                    "channels": row["channels"] / cycles, "viewer_share": viewer_share * 100,
                    "channel_share": channel_share * 100, "gap": viewer_share / channel_share,
                    "per_channel": row["viewers"] / row["channels"]})
    df = pd.DataFrame(out)
    if df.empty:
        return df
    df["score"] = df["gap"] * df["viewers"].map(lambda v: math.log10(max(v, 10)))
    return df.sort_values("score", ascending=False).reset_index(drop=True)


def languages_seen(platforms, period):
    since = int(time.time() - (rs.PERIODS[period] or 2 * 86400))
    rows = db.query("SELECT lang, SUM(viewers) FROM lang_samples WHERE platform IN (%s) AND ts > ?"
                    " GROUP BY lang ORDER BY 2 DESC" % ",".join("?" * len(platforms)),
                    tuple(platforms) + (since,))
    return [lang for lang, _v in rows if lang and lang != "other"]


# ---------------------------------------------------------------------------
# One game: channel sizes, tags and title words
# ---------------------------------------------------------------------------
def channel_sizes(key, platform):
    """(DataFrame of size bucket -> channels, complete?, smallest size counted) for
    one platform's latest snapshot. One platform at a time: each reaches down to a
    different channel size, and mixing them would hide the small channels."""
    ts = _latest(platform)
    row = db.query("SELECT hist, full FROM game_samples WHERE platform = ? AND ts = ? AND"
                   " game_key = ?", (platform, ts, key)) if ts else []
    counts = _hist(row[0][0]) if row else None
    if counts is None:
        return pd.DataFrame(), False, 0
    complete = row[0][1] == 1
    cutoff = 0 if complete else float(db.get_meta("cutoff_" + platform, 0) or 0)
    df = pd.DataFrame({"size": HIST_LABELS, "channels": counts, "low": list(HIST_EDGES)})
    # Keep the bucket the cutoff falls in, which is only partly counted.
    df = df[[high > cutoff for high in list(HIST_EDGES[1:]) + [10 ** 9]]]
    df = df[(df["low"] > 0) | (df["channels"] > 0)]
    return df.drop(columns="low"), complete, cutoff


def game_terms(key, platforms):
    """(tags [(tag, channels)], words [(word, channels)], share of viewers on Drops streams)."""
    rows = db.query("SELECT term, SUM(channels), SUM(viewers) FROM game_terms WHERE game_key = ?"
                    " AND platform IN (%s) GROUP BY term ORDER BY 2 DESC"
                    % ",".join("?" * len(platforms)), (key,) + tuple(platforms))
    drops = sum(v for term, _c, v in rows if term == "@drops")
    now = _now_rows(platforms).get(key, {}).get("viewers", 0)
    tags = [(t[1:], c) for t, c, _v in rows if t.startswith("#")
            and t[1:].lower() not in LANGUAGE_TAGS and "drop" not in t.lower()][:12]
    words = [(t, c) for t, c, _v in rows if not t.startswith(("#", "@"))][:12]
    return tags, words, (drops / now * 100) if now else 0.0


def drops_leaders(platforms, top=5, min_share=40):
    """Games where most of the audience watches Drops-enabled streams right now."""
    now = _now_rows(platforms)
    rows = db.query("SELECT game_key, SUM(viewers) FROM game_terms WHERE term = '@drops' AND"
                    " platform IN (%s) GROUP BY game_key" % ",".join("?" * len(platforms)),
                    tuple(platforms))
    names, out = rs.names(), []
    for key, drops in rows:
        viewers = now.get(key, {}).get("viewers", 0)
        name = names.get(key, key)
        if viewers >= 2000 and is_game(name) and drops / viewers * 100 >= min_share:
            out.append((name, key, drops / viewers * 100, viewers))
    return sorted(out, key=lambda r: r[3], reverse=True)[:top]


# ---------------------------------------------------------------------------
# Plain-language summaries
# ---------------------------------------------------------------------------
def insights(platforms, period, games_only=True):
    """[(emoji, sentence with <b> tags)] - what is going on, in a few lines."""
    fmt = _fmt
    out = []
    board = rs.leaderboard(list(platforms), period, games_only)
    where = " + ".join(p.title() if p != "youtube" else "YouTube" for p in platforms)
    if not board.empty:
        top = board.iloc[0]
        if period == "live":
            total = board["viewers"].sum()
            out.append(("📺", "<b>%s</b> people are watching %s on %s right now."
                        % (fmt(total), "games" if games_only else "streams", where)))
            out.append(("👑", "<b>%s</b> leads with <b>%s</b> viewers - %d%% of everyone "
                        "watching." % (top["name"], fmt(top["viewers"]),
                                       round(top["viewers"] / total * 100))))
        else:
            out.append(("⏱️", "<b>%s</b> hours watched on %s in the recorded part of the last "
                        "%s." % (fmt(board["watch_h"].sum()), where, period)))
            out.append(("👑", "<b>%s</b> leads with <b>%s</b> watch hours and %s average "
                        "viewers." % (top["name"], fmt(top["watch_h"]), fmt(top["avg_viewers"]))))
    gainers, _losers, label = movers(platforms)
    if label and not gainers.empty:
        g = gainers.iloc[0]
        out.append(("🚀", "Biggest jump since %s: <b>%s</b>, %s → %s viewers." % (
            label, g["name"], fmt(g["before"]), fmt(g["now"]))))
    spikes = rs.breakouts(list(platforms))
    if not spikes.empty and spikes["jump"].notna().any():
        s = spikes.dropna(subset=["jump"]).iloc[0]
        out.append(("⚡", "<b>%s</b> is at %.1fx its usual audience for this hour." % (
            s["name"], s["jump"])))
    drops = drops_leaders(platforms, top=1)
    if drops:
        name, _key, share, _v = drops[0]
        out.append(("🎁", "Drops are running on <b>%s</b>: %d%% of its viewers watch "
                    "Drops-enabled streams, which inflates its numbers for now." % (name, share)))
    return out


def game_story(name, card, period, platforms, typical=None):
    """One or two sentences that say what the numbers on a game page mean."""
    fmt = _fmt
    where = " + ".join(p.title() if p != "youtube" else "YouTube" for p in platforms)
    if period == "live":
        text = ("<b>%s</b> has <b>%s</b> people watching right now on %s - #%d of %d. "
                "<b>%s</b> channels are live" % (name, fmt(card.get("viewers")), where,
                                                 card["rank"], card["of"],
                                                 fmt(card.get("channels"))))
    else:
        text = ("Over the last %s, <b>%s</b> averaged <b>%s</b> viewers (peak %s) and was "
                "watched for %s hours - #%d of %d. About <b>%s</b> channels were live at a "
                "time" % (period, name, fmt(card.get("avg_viewers")),
                          fmt(card.get("peak_viewers")), fmt(card.get("watch_h")),
                          card["rank"], card["of"], fmt(card.get("avg_channels"))))
    if typical is not None:
        text += ", and half of them have <b>%s</b> viewer%s or fewer." % (
            fmt(typical), "" if typical == 1 else "s")
    else:
        text += "."
    change = card.get("change")
    if change is not None and not pd.isna(change):
        text += " Average viewers are <b>%s %d%%</b> on the period before." % (
            "up" if change >= 0 else "down", abs(round(change)))
    return text


def _fmt(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "–"
    value = float(value)
    for size, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= size:
            return ("%.1f" % (value / size)).rstrip("0").rstrip(".") + suffix
    return "%d" % round(value)
