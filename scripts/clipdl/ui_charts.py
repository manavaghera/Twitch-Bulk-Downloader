"""Charts for the research pages, in one consistent style.

Colours come from a validated colour-blind-safe palette (dark mode, checked
against the page's card colour): each platform keeps its own colour on every
chart, and compared games take the slots in the order they were picked.
One y-axis per chart; two measures of different size get two charts.
"""

import altair as alt
import streamlit as st

# Fixed per entity, never by rank - a filter must not repaint the survivors.
PLATFORM_COLORS = {"Twitch": "#3987e5", "Kick": "#d95926", "YouTube": "#199e70"}
SERIES = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181"]
# One hue, dark (near nothing) to light (a lot), for the page's dark surface.
SEQUENTIAL = ["#0d366b", "#184f95", "#256abf", "#3987e5", "#6da7ec", "#9ec5f4"]
SURFACE = "#17171C"
INK_MUTED = "#8C8C99"
GRID = "#26262d"
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _style(chart, height):
    return (chart.properties(height=height, background="transparent")
            .configure_view(strokeWidth=0)
            .configure_axis(labelColor=INK_MUTED, titleColor=INK_MUTED, gridColor=GRID,
                            domainColor=GRID, tickColor=GRID, labelFontSize=11,
                            titleFontSize=11, titleFontWeight=500)
            .configure_legend(labelColor="#DEDEE3", titleColor=INK_MUTED, orient="top",
                              labelFontSize=12, symbolType="circle")
            .configure_text(color="#DEDEE3"))


def show(chart, height=280):
    st.altair_chart(_style(chart, height), width="stretch", theme=None)


def lines(df, x, y, color, domain, colors, y_title, y_format=",.0f", legend=True):
    """Lines over time with a crosshair + tooltip on hover. legend=False when the
    page draws its own legend (with game icons) above the chart."""
    scale = alt.Scale(domain=domain, range=colors)
    hover = alt.selection_point(fields=[x], nearest=True, on="pointerover", empty=False,
                                clear="pointerout")
    base = alt.Chart(df).encode(x=alt.X("%s:T" % x, title=None, axis=alt.Axis(labelOverlap=True)))
    drawn = base.mark_line(strokeWidth=2, interpolate="monotone").encode(
        y=alt.Y("%s:Q" % y, title=y_title, axis=alt.Axis(format="~s")),
        color=alt.Color("%s:N" % color, scale=scale, title=None,
                        legend=alt.Legend() if legend else None))
    points = base.mark_point(size=70, filled=True, stroke=SURFACE, strokeWidth=2).encode(
        y="%s:Q" % y, color=alt.Color("%s:N" % color, scale=scale, legend=None),
        opacity=alt.condition(hover, alt.value(1), alt.value(0)))
    rule = base.mark_rule(color=INK_MUTED, strokeWidth=1).encode(
        opacity=alt.condition(hover, alt.value(0.6), alt.value(0)),
        tooltip=[alt.Tooltip("%s:T" % x, title="Time", format="%a %d %b, %H:%M")] + [
            alt.Tooltip("%s:Q" % name, title=name, format=y_format) for name in domain]
    ).transform_pivot(color, value=y, groupby=[x]).add_params(hover)
    return drawn + points + rule


def stacked_bars(df, value, label, value_title, order, platforms=None):
    """Horizontal bars per game, split by platform, 2px gaps between segments.
    The legend lists only the platforms shown; each keeps its own colour."""
    shown = platforms or list(PLATFORM_COLORS)
    return alt.Chart(df).mark_bar(stroke=SURFACE, strokeWidth=2, height={"band": 0.72}).encode(
        y=alt.Y("%s:N" % label, sort=order, title=None, axis=alt.Axis(labelLimit=180)),
        x=alt.X("%s:Q" % value, title=value_title, axis=alt.Axis(format="~s")),
        color=alt.Color("Platform:N", title=None,
                        scale=alt.Scale(domain=shown,
                                        range=[PLATFORM_COLORS[p] for p in shown])),
        order=alt.Order("Platform:N"),
        tooltip=[alt.Tooltip("%s:N" % label, title="Game"), "Platform:N",
                 alt.Tooltip("%s:Q" % value, title=value_title, format=",.0f")])


def bars(df, value, label, value_title, fmt=",.0f"):
    """One series of horizontal bars: rounded data ends, anchored at zero."""
    return alt.Chart(df).mark_bar(color=SERIES[0], cornerRadiusEnd=4,
                                  height={"band": 0.7}).encode(
        y=alt.Y("%s:N" % label, sort="-x", title=None, axis=alt.Axis(labelLimit=200)),
        x=alt.X("%s:Q" % value, title=value_title),
        tooltip=[alt.Tooltip("%s:N" % label), alt.Tooltip("%s:Q" % value, format=fmt)])


def heatmap(df, value, title, fmt=",.0f"):
    """Weekday x hour grid, one sequential hue; empty cells stay empty."""
    return alt.Chart(df).mark_rect(stroke=SURFACE, strokeWidth=2, cornerRadius=3).encode(
        x=alt.X("hour:O", title="Hour of day (your time)", axis=alt.Axis(labelAngle=0)),
        y=alt.Y("day:N", sort=DAYS, title=None),
        color=alt.Color("%s:Q" % value, title=title, scale=alt.Scale(range=SEQUENTIAL),
                        legend=alt.Legend(orient="right", format="~s")),
        tooltip=[alt.Tooltip("day:N", title="Day"), alt.Tooltip("hour:O", title="Hour"),
                 alt.Tooltip("viewers:Q", title="Avg viewers", format=",.0f"),
                 alt.Tooltip("channels:Q", title="Avg channels", format=",.0f"),
                 alt.Tooltip("vpc:Q", title="Viewers per channel", format=",.1f"),
                 alt.Tooltip("samples:Q", title="Samples")])


def scatter(df):
    """Opportunity map: channels (competition) vs viewers per typical channel."""
    base = alt.Chart(df).encode(
        x=alt.X("avg_channels:Q", title="Live channels (competition)",
                scale=alt.Scale(type="log"), axis=alt.Axis(format="~s")),
        y=alt.Y("typical:Q", title="Viewers per channel outside the top 5",
                scale=alt.Scale(type="log"), axis=alt.Axis(format="~s")))
    dots = base.mark_circle(color=SERIES[0], opacity=0.75, stroke=SURFACE, strokeWidth=2).encode(
        size=alt.Size("avg_viewers:Q", title="Avg viewers", scale=alt.Scale(range=[40, 900]),
                      legend=None),
        tooltip=[alt.Tooltip("name:N", title="Game"),
                 alt.Tooltip("score:Q", title="Opportunity"),
                 alt.Tooltip("avg_viewers:Q", title="Avg viewers", format=",.0f"),
                 alt.Tooltip("avg_channels:Q", title="Avg channels", format=",.0f"),
                 alt.Tooltip("typical:Q", title="Typical viewers/channel", format=",.1f"),
                 alt.Tooltip("top5:Q", title="Top-5 share %", format=".0f")])
    labels = base.transform_filter(alt.datum.label).mark_text(
        align="left", dx=9, dy=-6, fontSize=11, color="#DEDEE3").encode(text="label:N")
    return dots + labels


def total(df, x, y, y_title):
    """One series over time: a soft area, a 2px line, the peak marked and
    labelled, and a crosshair + tooltip on hover. The simplest honest read."""
    peak = df.loc[[df[y].idxmax()]].assign(label=lambda d: "Peak " + d[y].map(_short))
    hover = alt.selection_point(fields=[x], nearest=True, on="pointerover", empty=False,
                                clear="pointerout")
    base = alt.Chart(df).encode(x=alt.X("%s:T" % x, title=None, axis=alt.Axis(labelOverlap=True)))
    area = base.mark_area(color=SERIES[0], opacity=0.18, interpolate="monotone").encode(
        y=alt.Y("%s:Q" % y, title=y_title, axis=alt.Axis(format="~s")))
    line = base.mark_line(color=SERIES[0], strokeWidth=2, interpolate="monotone").encode(y="%s:Q" % y)
    dot = alt.Chart(peak).mark_point(size=80, filled=True, color=SERIES[0], stroke=SURFACE,
                                     strokeWidth=2).encode(x="%s:T" % x, y="%s:Q" % y)
    text = alt.Chart(peak).mark_text(dy=-12, fontSize=11, fontWeight=600, color="#DEDEE3").encode(
        x="%s:T" % x, y="%s:Q" % y, text="label:N")
    rule = base.mark_rule(color=INK_MUTED, strokeWidth=1).encode(
        opacity=alt.condition(hover, alt.value(0.6), alt.value(0)),
        tooltip=[alt.Tooltip("%s:T" % x, title="Time", format="%a %d %b, %H:%M"),
                 alt.Tooltip("%s:Q" % y, title=y_title, format=",.0f")]).add_params(hover)
    return area + line + dot + text + rule


def sizes(df):
    """How many channels have how many viewers: one vertical bar per size bucket."""
    return alt.Chart(df).mark_bar(color=SERIES[0], cornerRadiusEnd=4, width={"band": 0.75}).encode(
        x=alt.X("size:N", sort=list(df["size"]), title="Viewers a channel has",
                axis=alt.Axis(labelAngle=0)),
        y=alt.Y("channels:Q", title="Channels", axis=alt.Axis(format="~s")),
        tooltip=[alt.Tooltip("size:N", title="Viewers"),
                 alt.Tooltip("channels:Q", title="Channels", format=",")])


def _short(value):
    for size, suffix in ((1e6, "M"), (1e3, "K")):
        if abs(value) >= size:
            return ("%.1f" % (value / size)).rstrip("0").rstrip(".") + suffix
    return "%d" % value
