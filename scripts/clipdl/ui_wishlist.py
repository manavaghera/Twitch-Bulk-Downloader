"""The Steam wishlist part of the Trend Research page: upcoming games and their odds."""

import streamlit as st

from . import ui_theme
from .wishlist import VERDICTS, WINDOWS

WHEN = {"all": "All", "30": "Out within 30 days", "90": "Within 90 days",
        "dated": "Has a date", "tba": "No date yet"}
VERDICT_ORDER = list(VERDICTS)


def _matches(game, when):
    bucket = game.release_bucket()
    if when == "30":
        return bucket == "30"
    if when == "90":
        return bucket in ("30", "90")
    if when == "dated":
        return game.release is not None
    if when == "tba":
        return game.release is None
    return True


def _rank_move(game, days):
    change = game.rank_change(days)
    if change is None:
        return "–"
    if change == float("inf"):
        return "🆕 new"
    return "▲ %d" % change if change > 0 else "▼ %d" % -change if change < 0 else "="


def _pct(change):
    if change is None:
        return None
    return 999 if change == float("inf") else round(change * 100)


def _out_in(game):
    days = game.days_until
    if days is None:
        return None
    return max(days, 0)


def _history_note(days, history_days):
    if history_days < days / 2.0:
        return ("Rank changes need history: the chart is saved every time research runs, "
                "so %d-day wishlist movement shows up after runs spread over %d+ days. "
                "Wikipedia trends are live already." % (days, max(1, days // 2)))
    return None


def _move_chip(game, days):
    change = game.rank_change(days)
    if change is None:
        return None
    if change == float("inf"):
        return ("🆕 new on chart", "cs-move up")
    if change > 0:
        return ("▲ %d in %dd" % (change, days), "cs-move up")
    if change < 0:
        return ("▼ %d in %dd" % (-change, days), "cs-move down")
    return ("= same place", "cs-move")


def _countdown(game):
    days = game.days_until
    if days is None:
        return {"tba": "date not announced", "soon": "no date yet"}.get(
            game.precision, "exact day not set")
    if days < 0:
        return "released"
    return "out today" if days == 0 else "in %d day%s" % (days, "" if days == 1 else "s")


def render_top(games, days):
    """The top 10 or 20 of Steam's most-wishlisted chart, in chart order."""
    st.markdown("##### 🏆 Most wishlisted on Steam right now")
    size = st.segmented_control("Show", (10, 20, 50, 100), format_func="Top {}".format,
                                default=10, required=True, key="wl_top",
                                label_visibility="collapsed")
    rows = []
    for game in sorted(games, key=lambda g: g.rank)[:size]:
        verdict = game.verdict(days)
        chips = [(VERDICTS[verdict], "cs-tag wl-" + verdict)]
        move = _move_chip(game, days)
        if move:
            chips.append(move)
        sub = " · ".join(part for part in (", ".join(game.tags[:3]), game.publisher) if part)
        rows.append({"pos": game.rank, "name": game.name, "url": game.store_url,
                     "art": game.art, "sub": sub, "chips": chips,
                     "when": game.release_text(), "left": _countdown(game),
                     "bar": game.score(days)})
    ui_theme.leaderboard(rows)


def render(found):
    st.markdown("#### 🎁 Upcoming: Steam wishlists")
    if not found or not found.get("games"):
        st.warning("Steam's wishlist chart did not answer this run. Try the research again "
                   "in a few minutes.")
        return
    games = found["games"]
    ui_theme.note("Steam's top 100 most wishlisted unreleased games - when they come out, "
                  "how the wishlist rank and public interest are moving, and whether it looks "
                  "like a boom. Steam keeps wishlist <b>counts</b> private, so the chart "
                  "<b>rank</b> is the wishlist signal.")

    days = st.segmented_control("Compare over", WINDOWS, format_func="Last {} days".format,
                                default=7, required=True, key="wl_window")
    note = _history_note(days, found.get("history_days", 0))
    if note:
        st.caption("ℹ️ " + note)

    render_top(games, days)
    st.space("small")
    st.markdown("##### 🔎 Boom watch")
    when = st.segmented_control("Release", list(WHEN), format_func=WHEN.get,
                                default="all", required=True, key="wl_when")

    shown = [g for g in games if _matches(g, when)]
    if not shown:
        ui_theme.empty_state("🗓️", "Nothing here", "No wishlisted game matches that release "
                             "filter right now.")
        return

    counts = {key: sum(1 for g in shown if g.verdict(days) == key) for key in VERDICTS}
    metric_cols = st.columns(4)
    metric_cols[0].metric("Boom likely", counts["boom"])
    metric_cols[1].metric("Heating up", counts["heating"])
    metric_cols[2].metric("Losing steam", counts["cooling"])
    metric_cols[3].metric("Out in 30 days", sum(1 for g in shown if g.release_bucket() == "30"))

    best = sorted(shown, key=lambda g: (VERDICT_ORDER.index(g.verdict(days)), -g.score(days)))
    ui_theme.game_cards([{
        "rank": g.rank, "name": g.name, "art": g.art, "tag": VERDICTS[g.verdict(days)],
        "tag_class": "wl-" + g.verdict(days), "bar": g.score(days),
        "left": "Hype %d" % g.score(days), "right": g.release_text(), "why": g.why(days),
    } for g in best[:5]])
    st.caption("Card number = place on Steam's most-wishlisted chart. Hype score 0–100 blends "
               "wishlist rank, IGDB hypes and Wikipedia reach, nudged by which way they move.")

    rows = [{
        "Wishlist #": g.rank, " ": g.art or None, "Game": g.name, "Verdict": VERDICTS[g.verdict(days)],
        "Hype": g.score(days), "Releases": g.release_text(), "Days to go": _out_in(g),
        "Rank move": _rank_move(g, days), "Wikipedia views": g.wiki_views(days) or None,
        "Wikipedia %": _pct(g.wiki_growth(days)), "IGDB hypes": g.hypes or None,
        "Genre": ", ".join(g.tags[:3]), "Publisher": g.publisher, "Steam": g.store_url,
    } for g in sorted(shown, key=lambda g: g.rank)]
    st.dataframe(rows, hide_index=True, width="stretch", column_config={
        " ": st.column_config.ImageColumn(" ", width="small"),
        "Hype": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%d"),
        "Wikipedia views": st.column_config.NumberColumn(
            "Wiki views (%dd)" % days, format="localized"),
        "Wikipedia %": st.column_config.NumberColumn(
            "Wiki vs prior %dd" % days, format="%+d%%"),
        "Rank move": st.column_config.TextColumn("Rank move (%dd)" % days),
        "Steam": st.column_config.LinkColumn(display_text="store ↗"),
    })
    with st.expander("How the verdict works", icon=":material/info:"):
        st.markdown(
            "- **🔥 Boom likely** - near the top of the wishlist chart with big reach, and not "
            "sliding.\n"
            "- **🚀 Heating up** - already sizeable and climbing: the rank is rising or "
            "Wikipedia readers are growing.\n"
            "- **📈 Strong** - big, but not moving much either way.\n"
            "- **🧊 Losing steam** - the rank is dropping or interest is falling.\n"
            "- **👀 Watch** - lower on the chart; worth a look if it starts climbing.\n\n"
            "It is a rule of thumb from public signals, not a guarantee. Release dates are "
            "Steam's own and move often; *2027* means Steam only gives the year.")
