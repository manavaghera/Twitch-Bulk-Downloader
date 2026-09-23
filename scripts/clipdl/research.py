"""Game research: turning the stats history into StreamsCharts-style numbers.

Everything here reads stats_db (what the collector recorded) except three
look-ups done live when asked for: Steam's best-sellers per country, YouTube's
trending gaming videos per country, and a game's most-viewed Twitch clips.
"""

import math
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from statistics import median

import pandas as pd

from . import stats_db as db
from .web import WebClient, is_game, mentions, normalize_name, steam_top_sellers

PERIODS = {"live": 0, "24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}
PLATFORMS = ("twitch", "kick", "youtube")
BUCKET = {"live": "15min", "24h": "15min", "7d": "1h", "30d": "6h"}

# Countries whose Steam store charts are read for "where is it popular".
STEAM_COUNTRIES = [
    ("US", "United States"), ("CA", "Canada"), ("BR", "Brazil"), ("MX", "Mexico"),
    ("GB", "United Kingdom"), ("DE", "Germany"), ("FR", "France"), ("ES", "Spain"),
    ("IT", "Italy"), ("PL", "Poland"), ("NL", "Netherlands"), ("SE", "Sweden"),
    ("TR", "Turkey"), ("UA", "Ukraine"), ("JP", "Japan"), ("KR", "South Korea"),
    ("TW", "Taiwan"), ("IN", "India"), ("AU", "Australia"), ("SA", "Saudi Arabia"),
]
YOUTUBE_REGIONS = ["US", "GB", "CA", "AU", "DE", "FR", "ES", "IT", "BR", "MX", "IN", "JP",
                   "KR", "TR", "PL"]

# Where each stream language is mostly watched. Platforms report the language
# a stream is in, never where its viewers are, so this is a hint, not a count.
LANGUAGE_REGIONS = {
    "en": "US, UK, Canada, Australia", "es": "Spain + Latin America",
    "pt": "Brazil (mostly), Portugal", "de": "Germany, Austria, Switzerland",
    "fr": "France, Belgium, Quebec", "ru": "Russia & CIS", "ja": "Japan", "ko": "South Korea",
    "zh": "China, Taiwan, Hong Kong", "tr": "Turkey", "ar": "Middle East & North Africa",
    "it": "Italy", "pl": "Poland", "nl": "Netherlands, Belgium", "sv": "Sweden",
    "uk": "Ukraine", "th": "Thailand", "id": "Indonesia", "vi": "Vietnam", "hi": "India",
    "tl": "Philippines", "cs": "Czechia", "fi": "Finland", "no": "Norway", "da": "Denmark",
}
LANGUAGE_NAMES = {
    "en": "English", "es": "Spanish", "pt": "Portuguese", "de": "German", "fr": "French",
    "ru": "Russian", "ja": "Japanese", "ko": "Korean", "zh": "Chinese", "tr": "Turkish",
    "ar": "Arabic", "it": "Italian", "pl": "Polish", "nl": "Dutch", "sv": "Swedish",
    "uk": "Ukrainian", "th": "Thai", "id": "Indonesian", "vi": "Vietnamese", "hi": "Hindi",
    "tl": "Filipino", "cs": "Czech", "fi": "Finnish", "no": "Norwegian", "da": "Danish",
}


def lang_name(code):
    return LANGUAGE_NAMES.get(code, (code or "other").upper() if len(code or "") <= 3
                              else (code or "other").title())


def names():
    """{game_key: display name}."""
    return {key: (info[0] or key) for key, info in db.game_info().items()}


def since_for(period, now=None):
    now = now or time.time()
    return int(now - PERIODS[period]), int(now)


# ---------------------------------------------------------------------------
# Coverage: how much of a period the collector actually recorded
# ---------------------------------------------------------------------------
def coverage(platforms, period):
    """{platform: (share of the period recorded 0..1, cycles)}."""
    if period == "live":
        return {p: (1.0 if db.latest_ts(p) else 0.0, 1) for p in platforms}
    since, until = since_for(period)
    out = {}
    for platform in platforms:
        seconds, cycles = db.coverage(platform, since, until)
        out[platform] = (min(1.0, seconds / float(PERIODS[period])), cycles)
    return out


def history_span():
    """Seconds between the first and the latest recorded cycle, any platform."""
    row = db.query("SELECT MIN(ts), MAX(ts) FROM runs")
    return (row[0][1] - row[0][0]) if row and row[0][0] else 0


# ---------------------------------------------------------------------------
# Leaderboards
# ---------------------------------------------------------------------------
def live(platforms, games_only=True):
    """The latest cycle: one row per game with viewers, channels, biggest channel."""
    frames = []
    for platform in platforms:
        ts = db.latest_ts(platform)
        if not ts:
            continue
        rows = db.query("SELECT game_key, viewers, channels, top1, median, full FROM"
                        " game_samples WHERE platform = ? AND ts = ?", (platform, ts))
        frames.append(pd.DataFrame(rows, columns=["key", "viewers", "channels", "top1",
                                                  "median", "full"]).assign(platform=platform))
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames)
    out = df.groupby("key").agg(viewers=("viewers", "sum"), channels=("channels", "sum"),
                                top1=("top1", "max")).reset_index()
    split = df.pivot_table(index="key", columns="platform", values="viewers", aggfunc="sum")
    out = out.merge(split, left_on="key", right_index=True, how="left")
    return _finish(out, games_only, sort="viewers")


def leaderboard(platforms, period, games_only=True):
    """Per game over a period: watch hours, hours streamed, peak and average
    viewers and channels, viewers per channel, share, change vs the period before."""
    if period == "live":
        return live(platforms, games_only)
    since, until = since_for(period)
    rows = db.game_totals(platforms, since, until)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    out = df.groupby("key").agg(watch_h=("watch_h", "sum"), airtime_h=("airtime_h", "sum"),
                                avg_viewers=("avg_viewers", "sum"),
                                avg_channels=("avg_channels", "sum"),
                                avg_top5=("avg_top5", "sum")).reset_index()
    split = df.pivot_table(index="key", columns="platform", values="avg_viewers", aggfunc="sum")
    out = out.merge(split, left_on="key", right_index=True, how="left")
    peaks = db.combined_peaks(platforms, since, until)
    out["peak_viewers"] = out["key"].map(lambda k: peaks.get(k, (0, 0))[0])
    out["peak_channels"] = out["key"].map(lambda k: peaks.get(k, (0, 0))[1])
    out["vpc"] = out["avg_viewers"] / out["avg_channels"].where(out["avg_channels"] > 0)

    # The period before, only when it was recorded well enough to compare.
    before = pd.DataFrame(db.game_totals(platforms, since - PERIODS[period], since))
    if not before.empty and before.groupby("platform")["recorded_s"].max().min() \
            >= 0.25 * PERIODS[period]:
        prev = before.groupby("key")["avg_viewers"].sum()
        out["change"] = out.apply(lambda r: (r["avg_viewers"] / prev[r["key"]] - 1) * 100
                                  if prev.get(r["key"], 0) > 0 else None, axis=1)
    else:
        out["change"] = None
    total = out["watch_h"].sum() or 1
    out["share"] = out["watch_h"] / total * 100
    return _finish(out, games_only, sort="watch_h")


def _finish(df, games_only, sort):
    label = names()
    df["name"] = df["key"].map(lambda k: label.get(k, k))
    if games_only:
        df = df[df["name"].map(is_game)]
    df = df.sort_values(sort, ascending=False).reset_index(drop=True)
    df.insert(0, "rank", range(1, len(df) + 1))
    return df


def platform_totals(platforms, period):
    if period == "live":
        out = {}
        for platform in platforms:
            ts = db.latest_ts(platform)
            row = db.query("SELECT viewers, streams FROM runs WHERE platform = ? AND ts = ?",
                           (platform, ts)) if ts else []
            out[platform] = {"viewers": row[0][0] if row else 0,
                             "streams": row[0][1] if row else 0}
        return out
    since, until = since_for(period)
    return db.platform_totals(platforms, since, until)


# ---------------------------------------------------------------------------
# One game
# ---------------------------------------------------------------------------
def game_series(key, platforms, period):
    """Viewers and channels over time, one row per time bucket and platform.
    Cycles where the game was not live count as 0, so dips are real."""
    since = int(time.time() - (PERIODS[period] or 86400))
    runs = pd.DataFrame(db.run_series(platforms, since), columns=["ts", "platform", "interval"])
    if runs.empty:
        return pd.DataFrame()
    rows = pd.DataFrame(db.game_series(key, platforms, since),
                        columns=["ts", "platform", "viewers", "channels", "top1", "median"])
    df = runs.merge(rows, on=["ts", "platform"], how="left").fillna(
        {"viewers": 0, "channels": 0, "top1": 0})
    df["time"] = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert(_local_tz())
    bucketed = (df.set_index("time").groupby("platform")[["viewers", "channels"]]
                .resample(BUCKET[period]).mean().dropna().reset_index())
    return bucketed


def _local_tz():
    return datetime.now(timezone.utc).astimezone().tzinfo


def game_card(key, platforms, period):
    """KPIs for one game plus the per-platform split, from the leaderboard.
    Its rank is among games, or among all categories for "Just Chatting" and co."""
    board = leaderboard(platforms, period, games_only=is_game(names().get(key, key)))
    if board.empty or key not in set(board["key"]):
        return None, board
    row = board[board["key"] == key].iloc[0].to_dict()
    # Complete counts only: a count that hit its cap saw just the biggest channels,
    # so its median would read far too high.
    full = db.query("SELECT median FROM game_samples WHERE game_key = ? AND full = 1"
                    " AND platform IN (%s) AND ts > ? ORDER BY ts DESC LIMIT 12"
                    % ",".join("?" * len(platforms)),
                    (key,) + tuple(platforms) + (int(time.time() - 7 * 86400),))
    row["typical"] = median([m for (m,) in full]) if full else None
    row["of"] = len(board)
    return row, board


def heatmap(key, platforms, days=30):
    """Average viewers, channels and viewers per channel by local weekday x hour."""
    since = int(time.time() - days * 86400)
    runs = pd.DataFrame(db.run_series(platforms, since), columns=["ts", "platform", "interval"])
    if runs.empty:
        return pd.DataFrame()
    rows = pd.DataFrame(db.game_series(key, platforms, since),
                        columns=["ts", "platform", "viewers", "channels", "top1", "median"])
    df = runs.merge(rows, on=["ts", "platform"], how="left").fillna(0)
    per_cycle = df.groupby("ts")[["viewers", "channels"]].sum().reset_index()
    local = pd.to_datetime(per_cycle["ts"], unit="s", utc=True).dt.tz_convert(_local_tz())
    per_cycle["day"] = local.dt.day_name().str[:3]
    per_cycle["hour"] = local.dt.hour
    grid = per_cycle.groupby(["day", "hour"])[["viewers", "channels"]].mean().reset_index()
    grid["vpc"] = grid["viewers"] / grid["channels"].where(grid["channels"] > 0)
    grid["samples"] = per_cycle.groupby(["day", "hour"]).size().values
    return grid


def game_languages(key, platforms, period):
    since = int(time.time() - (PERIODS[period] or 86400 * 2))
    rows = db.languages(platforms, since, key)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["key", "lang", "viewers", "channels"])
    df = df.groupby("lang")[["viewers", "channels"]].sum().reset_index()
    df["share"] = df["viewers"] / df["viewers"].sum() * 100
    df["language"] = df["lang"].map(lang_name)
    df["watched_in"] = df["lang"].map(lambda c: LANGUAGE_REGIONS.get(c, "-"))
    return df.sort_values("share", ascending=False)


def top_channels(key, platforms):
    out = []
    for platform in platforms:
        for rank, channel, display, viewers, lang, title, started, ts in db.top_channels(
                key, platform):
            url = {"twitch": "https://www.twitch.tv/%s", "kick": "https://kick.com/%s",
                   "youtube": "https://www.youtube.com/watch?v=%s"}[platform] % channel
            out.append({"platform": platform.title(), "channel": display or channel,
                        "viewers": viewers, "language": lang_name(lang), "title": title,
                        "live for": _since(started), "url": url})
    return sorted(out, key=lambda r: r["viewers"], reverse=True)


def _since(stamp):
    try:
        start = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return ""
    hours = (datetime.now(timezone.utc) - start).total_seconds() / 3600
    return "%dh %02dm" % (hours, (hours % 1) * 60) if hours >= 0 else ""


# ---------------------------------------------------------------------------
# Opportunities: where a streamer is most likely to be found
# ---------------------------------------------------------------------------
def _log_share(value, biggest):
    return math.log1p(value) / math.log1p(biggest) if value > 0 and biggest > 0 else 0.0


def opportunities(platforms, period, min_viewers=200):
    """Games ranked by how open they are to a new streamer.

    typical  viewers per channel outside the top five - what a channel that
             is not a star gets; the plain average is dragged up by the stars
    top5     share of all viewers the five biggest channels hold; a category
             one streamer owns is not an opening
    score    0-100: typical viewers (45%), audience size (35%), openness (20%)
    """
    board = leaderboard(platforms, period if period != "live" else "24h")
    if board.empty or "avg_viewers" not in board:
        return pd.DataFrame()
    # Ten channels at least: below that, "typical" is one or two people.
    df = board[(board["avg_viewers"] >= min_viewers) & (board["avg_channels"] >= 10)].copy()
    if df.empty:
        return df
    df["typical"] = (df["avg_viewers"] - df["avg_top5"]).clip(lower=0) / \
        (df["avg_channels"] - 5).clip(lower=1)
    df["top5"] = (df["avg_top5"] / df["avg_viewers"] * 100).clip(upper=100)
    top_typical, top_viewers = df["typical"].max(), df["avg_viewers"].max()
    df["score"] = [round(100 * (0.45 * _log_share(t, top_typical)
                                + 0.35 * _log_share(v, top_viewers) + 0.20 * (1 - s / 100)))
                   for t, v, s in zip(df["typical"], df["avg_viewers"], df["top5"])]
    counted = set(db.tail_ratios("twitch", max_age_s=7 * 86400)) | \
        set(db.tail_ratios("kick", max_age_s=7 * 86400))
    df["counted"] = df["key"].map(lambda k: "full count" if k in counted else "big channels only")
    return df.sort_values("score", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Breakouts: what is spiking right now against its own normal
# ---------------------------------------------------------------------------
def breakouts(platforms, min_viewers=300):
    """Games well above their usual level for this time of day.

    Baseline: the median of the same hour (+-1) on the previous 7 days; with
    less history, the average of the previous 24 hours.
    """
    now = time.time()
    latest = {p: db.latest_ts(p) for p in platforms if p != "youtube"}
    latest = {p: ts for p, ts in latest.items() if ts}
    if not latest:
        return pd.DataFrame()
    current = {}
    for platform, ts in latest.items():
        for key, viewers, channels in db.query(
                "SELECT game_key, viewers, channels FROM game_samples WHERE platform = ?"
                " AND ts = ?", (platform, ts)):
            v, c = current.get(key, (0, 0))
            current[key] = (v + viewers, c + channels)
    since = int(now - 8 * 86400)
    keys = [k for k, (v, _c) in current.items() if v >= min_viewers]
    if not keys:
        return pd.DataFrame()
    history = pd.DataFrame(db.query(
        "SELECT ts, game_key, SUM(viewers), SUM(channels) FROM game_samples WHERE platform IN"
        " (%s) AND game_key IN (%s) AND ts > ? AND ts < ? GROUP BY ts, game_key"
        % (",".join("?" * len(latest)), ",".join("?" * len(keys))),
        tuple(latest) + tuple(keys) + (since, min(latest.values()))),
        columns=["ts", "key", "viewers", "channels"])
    all_cycles = db.query("SELECT DISTINCT ts FROM runs WHERE platform IN (%s) AND ts > ?"
                          " AND ts < ?" % ",".join("?" * len(latest)),
                          tuple(latest) + (since, min(latest.values())))
    if history.empty:
        return pd.DataFrame()
    hour_now = datetime.now().hour

    def near_now(stamps):
        hours = pd.to_datetime(pd.Series(stamps), unit="s", utc=True).dt.tz_convert(
            _local_tz()).dt.hour
        return ((hours - hour_now).abs().isin([0, 1, 23]) & (pd.Series(stamps) < now - 20 * 3600)).values

    stamps = [ts for (ts,) in all_cycles]
    same_cycles = {ts for ts, ok in zip(stamps, near_now(stamps)) if ok}
    if len(same_cycles) >= 6:
        base_rows, cycles = history[history["ts"].isin(same_cycles)], len(same_cycles)
    else:
        base_rows = history[history["ts"] > now - 86400]
        cycles = len([ts for ts in stamps if ts > now - 86400]) or 1
    cycles_same = len(same_cycles)
    # A game missing from a cycle was not live: count it as 0, not as missing.
    base_v = base_rows.groupby("key")["viewers"].sum() / cycles
    base_c = base_rows.groupby("key")["channels"].sum() / cycles
    label, info = names(), db.game_info()
    out = []
    for key, (viewers, channels) in current.items():
        name = label.get(key, key)
        if viewers < min_viewers or not is_game(name):
            continue
        bv, bc = base_v.get(key, 0), base_c.get(key, 0)
        first_seen = (info.get(key) or (None,) * 5)[4]
        signals = []
        if bv and viewers / bv >= 1.8:
            signals.append("viewers x%.1f" % (viewers / bv))
        if bc >= 5 and channels / bc >= 1.5:
            signals.append("channels x%.1f" % (channels / bc))
        if not bv and first_seen and now - first_seen < 48 * 3600:
            signals.append("new on the charts")
        if signals:
            out.append({"key": key, "name": name, "viewers": viewers, "usual": round(bv),
                        "channels": channels, "usual_channels": round(bc),
                        "jump": viewers / bv if bv else None, "signals": ", ".join(signals)})
    df = pd.DataFrame(out)
    if not df.empty:
        df = df.sort_values(["jump", "viewers"], ascending=False, na_position="first")
    df.attrs["baseline"] = "same hour, last 7 days" if cycles_same >= 6 else "last 24 hours"
    return df


# ---------------------------------------------------------------------------
# Languages and countries
# ---------------------------------------------------------------------------
def by_language(platforms, period, games_only=True, top=5):
    """{language code: [(name, share of that language's viewers %)]}, biggest languages first."""
    since = int(time.time() - (PERIODS[period] or 86400 * 2))
    rows = db.languages(platforms, since)
    if not rows:
        return []
    df = pd.DataFrame(rows, columns=["key", "lang", "viewers", "channels"])
    label = names()
    df["name"] = df["key"].map(lambda k: label.get(k, k))
    if games_only:
        df = df[df["name"].map(is_game)]
    out = []
    for lang, part in df.groupby("lang"):
        total = part["viewers"].sum()
        best = part.sort_values("viewers", ascending=False).head(top)
        out.append((lang, total, [(n, v / total * 100) for n, v in zip(best["name"],
                                                                      best["viewers"])]))
    return sorted(out, key=lambda item: item[1], reverse=True)


def steam_countries(game_name):
    """[(country, rank on its Steam best-seller top 100 or None)] for one game."""
    web, key = WebClient(), normalize_name(game_name)

    def one(code):
        for rank, (_appid, name) in enumerate(steam_top_sellers(web, code, depth=100), 1):
            if normalize_name(name) == key:
                return rank
        return None

    with ThreadPoolExecutor(max_workers=5) as pool:
        ranks = list(pool.map(one, [code for code, _ in STEAM_COUNTRIES]))
    return [(country, code, rank) for (code, country), rank in zip(STEAM_COUNTRIES, ranks)]


def youtube_countries(game_name, api_key):
    """[(region, videos of the top 50 trending gaming videos that mention it)]."""
    from .web import youtube_trending_gaming
    rows = youtube_trending_gaming(WebClient(), api_key, regions=YOUTUBE_REGIONS, pages=1)
    by_region = {}
    for region, text in rows:
        by_region.setdefault(region, []).append(text)
    return [(region, mentions(by_region.get(region, []), game_name), len(by_region.get(region, [])))
            for region in YOUTUBE_REGIONS]


def _channel_url(clip):
    """The channel page. Display names can be in any script, so the login is
    taken from the clip's own link (twitch.tv/<login>/clip/...) when it has one."""
    url = clip.get("url") or ""
    marker = "twitch.tv/"
    if marker in url and "/clip/" in url:
        return "https://www.twitch.tv/" + url.split(marker, 1)[1].split("/clip/", 1)[0]
    return url


def clip_sources(api, twitch_id, hours):
    """The channels whose clips of this game are watched most, for clip farmers.

    Reads up to 300 of the game's most-viewed clips from the period."""
    end = datetime.now(timezone.utc).replace(microsecond=0)
    start = end - timedelta(hours=hours)
    stamp = "%Y-%m-%dT%H:%M:%SZ"
    clips, cursor = [], None
    for _ in range(3):
        params = {"game_id": twitch_id, "first": 100, "started_at": start.strftime(stamp),
                  "ended_at": end.strftime(stamp)}
        if cursor:
            params["after"] = cursor
        items, cursor = api.get_page("/clips", params)
        clips += items
        if not cursor:
            break
    by_channel = {}
    for clip in clips:
        entry = by_channel.setdefault(clip.get("broadcaster_name") or "?", {
            "channel": clip.get("broadcaster_name"), "clips": 0, "views": 0, "best": None,
            "best_views": -1, "language": lang_name((clip.get("language") or "").lower()),
            "url": _channel_url(clip)})
        views = int(clip.get("view_count") or 0)
        entry["clips"] += 1
        entry["views"] += views
        if views > entry["best_views"]:
            entry["best_views"], entry["best"] = views, clip.get("url")
    rows = sorted(by_channel.values(), key=lambda r: r["views"], reverse=True)
    for row in rows:
        row["per_clip"] = row["views"] / row["clips"]
        row.pop("best_views", None)
    total = sum(int(c.get("view_count") or 0) for c in clips)
    return rows, {"clips": len(clips), "views": total, "channels": len(rows)}
