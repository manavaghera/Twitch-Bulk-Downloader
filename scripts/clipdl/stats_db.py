"""The stats history: what was live, on which platform, every collection cycle.

No streaming site publishes its past. Twitch, Kick and YouTube only say who is
live *right now*, so sites like StreamsCharts record that every few minutes and
build watch hours, peaks and averages from the recordings. This file is that
recording, kept in data/stats.db (SQLite):

  runs          one row per platform per cycle: when, how long it stands for
  game_samples  per game per cycle: viewers, live channels, the biggest channel
  lang_samples  per game, per stream language, once an hour
  live_channels the top channels of each game in the latest cycle only

From those, for any period and any mix of platforms:

  watch hours      viewers x time, summed over the samples
  hours streamed   live channels x time ("airtime")
  peak / average   viewers and channels, averaged over the recorded time
  viewers/channel  average viewers / average channels

Numbers cover the time the collector was running; `coverage()` says how much
of a period that is, and the page shows it next to every figure.
"""

import sqlite3
import time
from contextlib import contextmanager

from .config import DATA_DIR

DB_FILE = DATA_DIR / "stats.db"
RETENTION_DAYS = 35
FULL_DETAIL_DAYS = 7         # every snapshot kept; older ones thinned to hourly
HOUR = 3600

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    ts INTEGER, platform TEXT, interval_s INTEGER, streams INTEGER, viewers INTEGER,
    games INTEGER, seconds REAL, PRIMARY KEY (ts, platform));
CREATE TABLE IF NOT EXISTS game_samples (
    ts INTEGER, platform TEXT, game_key TEXT, viewers INTEGER, channels INTEGER,
    seen INTEGER, top1 INTEGER, top5 INTEGER, median REAL,
    full INTEGER DEFAULT 0,                 -- 1 every channel counted, 2 count hit its cap
    PRIMARY KEY (ts, platform, game_key));
CREATE INDEX IF NOT EXISTS game_samples_key ON game_samples (game_key, ts);
CREATE TABLE IF NOT EXISTS lang_samples (
    ts INTEGER, platform TEXT, game_key TEXT, lang TEXT, viewers INTEGER, channels INTEGER,
    PRIMARY KEY (ts, platform, game_key, lang));
CREATE TABLE IF NOT EXISTS games (
    game_key TEXT PRIMARY KEY, name TEXT, twitch_id TEXT, kick_slug TEXT, box_art TEXT,
    first_seen INTEGER, last_seen INTEGER);
CREATE TABLE IF NOT EXISTS tail (
    platform TEXT, game_key TEXT, ratio REAL, ts INTEGER, PRIMARY KEY (platform, game_key));
CREATE TABLE IF NOT EXISTS live_channels (
    platform TEXT, game_key TEXT, rank INTEGER, channel TEXT, display TEXT, viewers INTEGER,
    language TEXT, title TEXT, started_at TEXT, ts INTEGER,
    PRIMARY KEY (platform, game_key, rank));
CREATE TABLE IF NOT EXISTS tracked (game_key TEXT PRIMARY KEY, name TEXT, since INTEGER);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS game_terms (
    platform TEXT, game_key TEXT, term TEXT, channels INTEGER, viewers INTEGER,
    PRIMARY KEY (platform, game_key, term));
"""

# Columns added after the first release, for databases made before them.
MIGRATIONS = {"game_samples": [("hist", "TEXT")]}


@contextmanager
def connect():
    """A short-lived connection. SQLite in WAL mode lets the page read while
    the collector writes, from other threads or another process."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB_FILE), timeout=30)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(SCHEMA)
        _migrate(con)
        yield con
        con.commit()
    finally:
        con.close()


_migrated = False


def _migrate(con):
    global _migrated
    if _migrated:
        return
    for table, columns in MIGRATIONS.items():
        have = {row[1] for row in con.execute("PRAGMA table_info(%s)" % table)}
        for name, kind in columns:
            if name not in have:
                con.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, name, kind))
    _migrated = True


# -- meta ------------------------------------------------------------------------
def get_meta(key, default=None):
    with connect() as con:
        row = con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_meta(key, value):
    with connect() as con:
        con.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, str(value)))


def claim_cycle(key, min_gap_s):
    """(may run, when the previous cycle ran or 0). Atomic across processes, so
    the page's collector and a scheduled one never record the same minute twice."""
    now = int(time.time())
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        previous = int(float(row[0])) if row else 0
        if previous and now - previous < min_gap_s:
            return False, previous
        con.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, str(now)))
    return True, previous


# -- writing ---------------------------------------------------------------------
def write_cycle(ts, platform, interval_s, run, games, langs, channels, names):
    """Store one platform's sample.

    run      {streams, viewers, games, seconds}
    games    [(game_key, viewers, channels, seen, top1, top5, median, full, hist)]
    langs    [(game_key, lang, viewers, channels)] - may be empty
    channels {game_key: [(channel, display, viewers, language, title, started_at)]}
    names    {game_key: {name, twitch_id, kick_slug, box_art}}
    """
    with connect() as con:
        con.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?)",
                    (ts, platform, interval_s, run["streams"], run["viewers"], run["games"],
                     run["seconds"]))
        con.executemany("INSERT OR REPLACE INTO game_samples (ts, platform, game_key, viewers,"
                        " channels, seen, top1, top5, median, full, hist)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        [(ts, platform) + tuple(row) for row in games])
        if langs:
            con.executemany("INSERT OR REPLACE INTO lang_samples VALUES (?,?,?,?,?,?)",
                            [(ts, platform) + tuple(row) for row in langs])
        con.execute("DELETE FROM live_channels WHERE platform = ?", (platform,))
        con.executemany("INSERT INTO live_channels VALUES (?,?,?,?,?,?,?,?,?,?)",
                        [(platform, key, rank) + tuple(row) + (ts,)
                         for key, rows in channels.items() for rank, row in enumerate(rows, 1)])
        for key, info in names.items():
            con.execute("""INSERT INTO games VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(game_key) DO UPDATE SET last_seen = excluded.last_seen,
                  name = CASE WHEN excluded.twitch_id IS NOT NULL THEN excluded.name
                              ELSE COALESCE(games.name, excluded.name) END,
                  twitch_id = COALESCE(excluded.twitch_id, games.twitch_id),
                  kick_slug = COALESCE(excluded.kick_slug, games.kick_slug),
                  box_art = COALESCE(excluded.box_art, games.box_art)""",
                        (key, info.get("name"), info.get("twitch_id"), info.get("kick_slug"),
                         info.get("box_art"), ts, ts))


def write_terms(platform, rows):
    """The latest tags and title words per game: [(game_key, term, channels, viewers)]."""
    with connect() as con:
        con.execute("DELETE FROM game_terms WHERE platform = ?", (platform,))
        con.executemany("INSERT OR REPLACE INTO game_terms VALUES (?,?,?,?,?)",
                        [(platform,) + tuple(row) for row in rows])


def backfill_channels(platform, ratios, since):
    """Re-estimate the channel counts of earlier snapshots that had no full-count
    ratio yet, so a game's first full count does not look like a sudden surge."""
    with connect() as con:
        con.executemany("UPDATE game_samples SET channels = MAX(seen, CAST(seen * ? + 0.5 AS"
                        " INTEGER)) WHERE platform = ? AND game_key = ? AND full = 0 AND"
                        " channels = seen AND ts >= ?",
                        [(ratio, platform, key, since) for key, ratio in ratios.items()])


def set_box_art(found):
    """{game_key: (box art template or "", twitch id or None)} - "" means none exists."""
    with connect() as con:
        con.executemany("UPDATE games SET box_art = ?, twitch_id = COALESCE(twitch_id, ?)"
                        " WHERE game_key = ?",
                        [(art or "", tid, key) for key, (art, tid) in found.items()])


def icons():
    """{game_key: Twitch box art template} for every game that has one."""
    return dict(query("SELECT game_key, box_art FROM games WHERE box_art IS NOT NULL"
                      " AND box_art != ''"))


def tail_ratios(platform, max_age_s=3 * 86400):
    """{game_key: full channel count / channels seen in the broad sample}."""
    with connect() as con:
        rows = con.execute("SELECT game_key, ratio FROM tail WHERE platform = ? AND ts >= ?"
                           " AND ratio IS NOT NULL",
                           (platform, int(time.time()) - max_age_s)).fetchall()
    return dict(rows)


def save_tail(platform, ratios, ts):
    with connect() as con:
        con.executemany("INSERT OR REPLACE INTO tail VALUES (?,?,?,?)",
                        [(platform, key, ratio, ts) for key, ratio in ratios.items()])


def last_full(platform):
    """{game_key: when it was last fully counted}."""
    with connect() as con:
        rows = con.execute("SELECT game_key, ts FROM tail WHERE platform = ?",
                           (platform,)).fetchall()
    return dict(rows)


def prune():
    """Drop history past RETENTION_DAYS, and thin anything older than
    FULL_DETAIL_DAYS to one snapshot an hour (see compact)."""
    cutoff = int(time.time()) - RETENTION_DAYS * 86400
    with connect() as con:
        for table in ("runs", "game_samples", "lang_samples"):
            con.execute("DELETE FROM %s WHERE ts < ?" % table, (cutoff,))
    compact(int(time.time()) - FULL_DETAIL_DAYS * 86400)


def compact(before):
    """Merge the snapshots of every hour before `before` into one that stands
    for the whole hour: viewers and channels become the hour's time-weighted
    averages, so watch hours and averages stay exact while the file stays
    small. Only peaks lose detail: for old days they are the best hourly
    average, not the best 15 minutes."""
    with connect() as con:
        rows = con.execute("SELECT platform, ts, interval_s FROM runs WHERE ts < ?"
                           " ORDER BY platform, ts", (before,)).fetchall()
        hours = {}
        for platform, ts, interval in rows:
            hours.setdefault((platform, ts // 3600), []).append((ts, interval))
        for (platform, _hour), stamps in hours.items():
            if len(stamps) < 2:
                continue
            keep, drop = stamps[0][0], [ts for ts, _i in stamps[1:]]
            total = float(sum(i for _ts, i in stamps))
            marks = ",".join("?" * len(stamps))
            params = (platform,) + tuple(ts for ts, _i in stamps)
            games = con.execute(
                "SELECT g.game_key, SUM(g.viewers * r.interval_s), SUM(g.channels * r.interval_s),"
                " SUM(g.seen * r.interval_s), SUM(g.top1 * r.interval_s),"
                " SUM(g.top5 * r.interval_s), AVG(g.median), MAX(g.full),"
                " MAX(CASE WHEN g.ts = ? THEN g.hist END)"
                " FROM game_samples g JOIN runs r ON r.ts = g.ts AND r.platform = g.platform"
                " WHERE g.platform = ? AND g.ts IN (%s) GROUP BY g.game_key" % marks,
                (keep,) + params).fetchall()
            run = con.execute("SELECT SUM(viewers * interval_s), SUM(streams * interval_s),"
                              " MAX(games), SUM(seconds) FROM runs WHERE platform = ? AND"
                              " ts IN (%s)" % marks, params).fetchone()
            con.execute("DELETE FROM game_samples WHERE platform = ? AND ts IN (%s)" % marks,
                        params)
            con.executemany("INSERT INTO game_samples (ts, platform, game_key, viewers, channels,"
                            " seen, top1, top5, median, full, hist)"
                            " VALUES (?,?,?,?,?,?,?,?,?,?,?)", [
                                (keep, platform, key, round(v / total), round(c / total),
                                 round(sn / total), round(t1 / total), round(t5 / total), med,
                                 full, hist)
                                for key, v, c, sn, t1, t5, med, full, hist in games])
            con.execute("UPDATE runs SET interval_s = ?, viewers = ?, streams = ?, games = ?,"
                        " seconds = ? WHERE platform = ? AND ts = ?",
                        (int(total), round(run[0] / total), round(run[1] / total), run[2],
                         run[3], platform, keep))
            con.executemany("UPDATE OR IGNORE lang_samples SET ts = ? WHERE platform = ?"
                            " AND ts = ?", [(keep, platform, ts) for ts in drop])
            for table in ("runs", "lang_samples"):
                con.executemany("DELETE FROM %s WHERE platform = ? AND ts = ?" % table,
                                [(platform, ts) for ts in drop])


# -- tracked games -----------------------------------------------------------------
def tracked():
    with connect() as con:
        return con.execute("SELECT game_key, name, since FROM tracked ORDER BY since").fetchall()


def set_tracked(game_key, name, on):
    with connect() as con:
        if on:
            con.execute("INSERT OR IGNORE INTO tracked VALUES (?,?,?)",
                        (game_key, name, int(time.time())))
        else:
            con.execute("DELETE FROM tracked WHERE game_key = ?", (game_key,))


# -- reading -----------------------------------------------------------------------
def query(sql, params=()):
    with connect() as con:
        return con.execute(sql, params).fetchall()


def _in(platforms):
    return ",".join("?" * len(platforms))


def latest_ts(platform):
    row = query("SELECT MAX(ts) FROM runs WHERE platform = ?", (platform,))
    return row[0][0] if row and row[0][0] else None


def coverage(platform, since, until=None):
    """(recorded seconds, cycles) for one platform between two times."""
    until = until or int(time.time())
    row = query("SELECT COALESCE(SUM(interval_s), 0), COUNT(*) FROM runs "
                "WHERE platform = ? AND ts > ? AND ts <= ?", (platform, since, until))
    return row[0]


def game_totals(platforms, since, until=None):
    """Per game and platform over a period, as dicts:
    key, platform, watch_h, airtime_h, peak_viewers, peak_channels, avg_viewers,
    avg_channels, avg_top5, recorded_s."""
    until = until or int(time.time())
    out = []
    for platform in platforms:
        recorded, _cycles = coverage(platform, since, until)
        if not recorded:
            continue
        rows = query(
            "SELECT g.game_key, SUM(g.viewers * r.interval_s), SUM(g.channels * r.interval_s),"
            " MAX(g.viewers), MAX(g.channels), SUM(g.top5 * r.interval_s)"
            " FROM game_samples g JOIN runs r ON r.ts = g.ts AND r.platform = g.platform"
            " WHERE g.platform = ? AND g.ts > ? AND g.ts <= ? GROUP BY g.game_key",
            (platform, since, until))
        for key, vs, cs, peak_v, peak_c, top5s in rows:
            out.append({"key": key, "platform": platform, "watch_h": vs / HOUR,
                        "airtime_h": cs / HOUR, "peak_viewers": peak_v, "peak_channels": peak_c,
                        "avg_viewers": vs / recorded, "avg_channels": cs / recorded,
                        "avg_top5": (top5s or 0) / recorded, "recorded_s": recorded})
    return out


def combined_peaks(platforms, since, until=None):
    """{game_key: (peak viewers, peak channels)} with platforms added up per cycle."""
    until = until or int(time.time())
    rows = query(
        "SELECT game_key, MAX(v), MAX(c) FROM (SELECT ts, game_key, SUM(viewers) v,"
        " SUM(channels) c FROM game_samples WHERE platform IN (%s) AND ts > ? AND ts <= ?"
        " GROUP BY ts, game_key) GROUP BY game_key" % _in(platforms),
        tuple(platforms) + (since, until))
    return {key: (v, c) for key, v, c in rows}


def platform_totals(platforms, since, until=None):
    """{platform: {watch_h, avg_viewers, peak_viewers, recorded_s, cycles}}."""
    until = until or int(time.time())
    out = {}
    for platform in platforms:
        row = query("SELECT COALESCE(SUM(viewers * interval_s), 0), MAX(viewers),"
                    " COALESCE(SUM(interval_s), 0), COUNT(*) FROM runs"
                    " WHERE platform = ? AND ts > ? AND ts <= ?", (platform, since, until))[0]
        out[platform] = {"watch_h": row[0] / HOUR, "peak_viewers": row[1] or 0,
                         "avg_viewers": row[0] / row[2] if row[2] else 0,
                         "recorded_s": row[2], "cycles": row[3]}
    return out


def game_series(game_key, platforms, since):
    """[(ts, platform, viewers, channels, top1, median)] for one game."""
    return query("SELECT ts, platform, viewers, channels, top1, median FROM game_samples"
                 " WHERE game_key = ? AND platform IN (%s) AND ts > ? ORDER BY ts"
                 % _in(platforms), (game_key,) + tuple(platforms) + (since,))


def run_series(platforms, since):
    """[(ts, platform, interval_s)] - every cycle, so gaps and zeros show honestly."""
    return query("SELECT ts, platform, interval_s FROM runs WHERE platform IN (%s) AND ts > ?"
                 " ORDER BY ts" % _in(platforms), tuple(platforms) + (since,))


def languages(platforms, since, game_key=None):
    """[(game_key, lang, viewer-samples summed, channel-samples summed)]."""
    extra = " AND game_key = ?" if game_key else ""
    params = tuple(platforms) + (since,) + ((game_key,) if game_key else ())
    return query("SELECT game_key, lang, SUM(viewers), SUM(channels) FROM lang_samples"
                 " WHERE platform IN (%s) AND ts > ?%s GROUP BY game_key, lang"
                 % (_in(platforms), extra), params)


def game_info():
    """{game_key: (name, twitch_id, kick_slug, box_art, first_seen)}."""
    return {row[0]: row[1:] for row in
            query("SELECT game_key, name, twitch_id, kick_slug, box_art, first_seen FROM games")}


def top_channels(game_key, platform, limit=10):
    return query("SELECT rank, channel, display, viewers, language, title, started_at, ts"
                 " FROM live_channels WHERE game_key = ? AND platform = ? ORDER BY rank LIMIT ?",
                 (game_key, platform, limit))
