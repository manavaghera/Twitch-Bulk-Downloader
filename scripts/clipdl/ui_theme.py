"""The look of the web page: one stylesheet plus small HTML pieces.

Colours and fonts live in .streamlit/config.toml; this file adds what a theme
cannot: the header banner, step badges, game cards and a few layout touches.
Nothing here changes what a run does.
"""

from html import escape

import streamlit as st

CSS = """
<style>
.block-container { padding-top: 2.2rem; padding-bottom: 4rem; max-width: 1320px; }
[data-testid="stHeader"] { background: transparent; }

/* ---- header banner ---------------------------------------------------- */
.cs-hero {
  position: relative; overflow: hidden; border-radius: 18px; padding: 26px 30px;
  background:
    radial-gradient(900px 260px at 0% 0%, rgba(145,70,255,.38), transparent 60%),
    radial-gradient(700px 240px at 100% 100%, rgba(0,214,143,.12), transparent 60%),
    linear-gradient(135deg, #1b1330 0%, #121218 70%);
  border: 1px solid rgba(145,70,255,.28); margin-bottom: 6px;
}
.cs-hero h1 { font-size: 2.1rem; font-weight: 800; margin: 0; padding: 0;
  letter-spacing: -.02em; line-height: 1.15; }
.cs-hero h1 span { background: linear-gradient(90deg, #bf94ff, #9146FF);
  -webkit-background-clip: text; background-clip: text; color: transparent; }
.cs-hero p { color: #ADADB8; margin: 6px 0 14px; font-size: .98rem; max-width: 760px; }
.cs-chips { display: flex; flex-wrap: wrap; gap: 8px; }
.cs-chip { display: inline-flex; align-items: center; gap: 7px; font-size: .8rem;
  font-weight: 600; padding: 5px 11px; border-radius: 999px;
  background: rgba(255,255,255,.06); border: 1px solid rgba(255,255,255,.09); color: #DEDEE3; }
.cs-dot { width: 8px; height: 8px; border-radius: 50%; background: #6b6b76; }
.cs-dot.ok { background: #00D68F; box-shadow: 0 0 0 3px rgba(0,214,143,.18); }
.cs-dot.bad { background: #FF5C5C; box-shadow: 0 0 0 3px rgba(255,92,92,.18); }
.cs-dot.live { background: #FF5C5C; animation: cs-pulse 1.4s infinite; }
@keyframes cs-pulse { 0% { box-shadow: 0 0 0 0 rgba(255,92,92,.55); }
  70% { box-shadow: 0 0 0 8px rgba(255,92,92,0); } 100% { box-shadow: 0 0 0 0 rgba(255,92,92,0); } }

/* ---- main tabs ----------------------------------------------------------- */
[data-testid="stTabs"] [data-baseweb="tab-list"] { gap: 6px; border-bottom: 1px solid #24242B; }
[data-testid="stTabs"] button[data-baseweb="tab"] { padding: 10px 16px; border-radius: 10px 10px 0 0; }
[data-testid="stTabs"] button[data-baseweb="tab"] p { font-weight: 600; font-size: .95rem; }
[data-testid="stTabs"] button[aria-selected="true"] { background: rgba(145,70,255,.10); }

/* ---- cards --------------------------------------------------------------- */
[class*="st-key-card"] { background: linear-gradient(180deg, #17171C, #141418);
  border-radius: 14px; }
[class*="st-key-card"]:hover { border-color: #34343d; }
.cs-step { display: flex; align-items: center; gap: 12px; margin: 2px 0 4px; }
.cs-step .n { flex: none; width: 30px; height: 30px; border-radius: 9px; display: grid;
  place-items: center; font-weight: 800; font-size: .9rem; color: #fff;
  background: linear-gradient(135deg, #a970ff, #772ce8); box-shadow: 0 4px 14px rgba(145,70,255,.35); }
.cs-step .t { font-weight: 700; font-size: 1.05rem; line-height: 1.2; }
.cs-step .h { color: #8C8C99; font-size: .82rem; margin-top: 1px; }
.cs-note { color: #8C8C99; font-size: .84rem; line-height: 1.45; }
.cs-note b { color: #CFCFD6; font-weight: 600; }

/* Columns wrap instead of squashing when the window is narrow. */
[class*="st-key-card"] [data-testid="stHorizontalBlock"] { flex-wrap: wrap; row-gap: .6rem; }
[class*="st-key-card"] [data-testid="stColumn"] { min-width: 300px; }
[class*="st-key-card_progress"] [data-testid="stColumn"] { min-width: 120px; }
.st-key-dl_layout > div > [data-testid="stHorizontalBlock"] { flex-wrap: wrap; }
.st-key-dl_layout > div > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:first-child {
  min-width: min(100%, 460px); }
.st-key-dl_layout > div > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:last-child {
  min-width: min(100%, 290px); }

/* ---- sticky run summary -------------------------------------------------- */
[data-testid="stColumn"]:has(.cs-summary-anchor) { position: sticky; top: 3.6rem;
  align-self: flex-start; }
.cs-sum-game { display: flex; gap: 14px; align-items: center; margin-bottom: 4px; }
.cs-sum-game img, .cs-art-ph { width: 64px; height: 86px; border-radius: 8px; object-fit: cover;
  flex: none; box-shadow: 0 6px 18px rgba(0,0,0,.45); }
.cs-art-ph { display: grid; place-items: center; font-weight: 800; font-size: 1.3rem;
  color: #fff; background: linear-gradient(135deg, #3a2a5c, #1f1f27); }
.cs-sum-game .name { font-weight: 800; font-size: 1.12rem; line-height: 1.2; }
.cs-sum-game .sub { color: #8C8C99; font-size: .82rem; margin-top: 3px; }
.cs-rows { margin: 10px 0 2px; }
.cs-row { display: grid; grid-template-columns: 76px 1fr; gap: 10px; padding: 7px 0;
  border-top: 1px dashed #26262d; font-size: .86rem; }
.cs-row .k { color: #8C8C99; }
.cs-row .v { color: #EFEFF1; font-weight: 600; overflow-wrap: anywhere; }

/* ---- game cards (trend picks) ------------------------------------------- */
.cs-cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap: 12px; }
.cs-card { position: relative; border-radius: 14px; overflow: hidden; background: #17171C;
  border: 1px solid #26262d; transition: transform .15s ease, border-color .15s ease; }
.cs-card:hover { transform: translateY(-2px); border-color: rgba(145,70,255,.55); }
.cs-card .art { height: 118px; background-size: cover; background-position: center 25%;
  position: relative; }
.cs-card .art::after { content: ""; position: absolute; inset: 0;
  background: linear-gradient(180deg, rgba(0,0,0,0) 30%, #17171C 100%); }
.cs-card .rank { position: absolute; top: 8px; left: 8px; z-index: 1; font-weight: 800;
  font-size: .78rem; padding: 3px 8px; border-radius: 7px; background: rgba(0,0,0,.65); }
.cs-card .body { padding: 2px 12px 12px; }
.cs-card .name { font-weight: 700; font-size: .98rem; line-height: 1.25; margin-bottom: 6px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cs-card .why { color: #8C8C99; font-size: .76rem; line-height: 1.35; margin-top: 7px;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.cs-bar { height: 6px; border-radius: 99px; background: #26262d; overflow: hidden; }
.cs-bar i { display: block; height: 100%; border-radius: 99px;
  background: linear-gradient(90deg, #772ce8, #bf94ff); }
.cs-meta { display: flex; justify-content: space-between; font-size: .74rem; color: #ADADB8;
  margin-top: 5px; }
.cs-tag { display: inline-block; font-size: .7rem; font-weight: 700; padding: 2px 7px;
  border-radius: 6px; letter-spacing: .02em; }
.cs-tag.rising, .cs-tag.new { background: rgba(0,214,143,.14); color: #3ee6ad; }
.cs-tag.cooling { background: rgba(91,164,255,.14); color: #8cc0ff; }
.cs-tag.steady, .cs-tag.quiet { background: rgba(255,255,255,.07); color: #ADADB8; }
.cs-tag.wl-boom { background: rgba(255,138,61,.16); color: #ffab6e; }
.cs-tag.wl-heating { background: rgba(0,214,143,.14); color: #3ee6ad; }
.cs-tag.wl-strong { background: rgba(145,70,255,.16); color: #c8a6ff; }
.cs-tag.wl-cooling { background: rgba(91,164,255,.14); color: #8cc0ff; }
.cs-tag.wl-watch { background: rgba(255,255,255,.07); color: #ADADB8; }

/* ---- leaderboard (ranked rows) ------------------------------------------- */
.cs-board { display: flex; flex-direction: column; gap: 8px; }
.cs-lb { display: grid; grid-template-columns: 44px 124px minmax(0, 1fr) auto; gap: 14px;
  align-items: center; padding: 10px 14px 10px 10px; border-radius: 12px; background: #17171C;
  border: 1px solid #26262d; transition: border-color .15s ease; }
.cs-lb:hover { border-color: rgba(145,70,255,.55); }
.cs-lb .pos { font-weight: 800; font-size: 1.35rem; text-align: center; color: #ADADB8; }
.cs-lb.top3 .pos { color: #bf94ff; }
.cs-lb img, .cs-lb .ph { width: 124px; height: 58px; border-radius: 8px; object-fit: cover;
  background: linear-gradient(135deg, #3a2a5c, #1f1f27); }
.cs-lb .nm { font-weight: 700; font-size: .98rem; white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis; }
.cs-lb .nm a { color: inherit; text-decoration: none; }
.cs-lb .nm a:hover { color: #bf94ff; }
.cs-lb .sub { color: #8C8C99; font-size: .78rem; margin-top: 2px; white-space: nowrap;
  overflow: hidden; text-overflow: ellipsis; }
.cs-lb .chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px; align-items: center; }
.cs-lb .side { text-align: right; min-width: 118px; }
.cs-lb .when { font-weight: 700; font-size: .9rem; }
.cs-lb .left { color: #8C8C99; font-size: .76rem; margin-top: 2px; }
.cs-lb .hype { display: flex; align-items: center; gap: 8px; margin-top: 7px; justify-content: flex-end;
  font-size: .76rem; color: #ADADB8; }
.cs-lb .hype .cs-bar { width: 70px; }
.cs-move { font-size: .72rem; font-weight: 700; padding: 2px 7px; border-radius: 6px;
  background: rgba(255,255,255,.07); color: #ADADB8; }
.cs-move.up { background: rgba(0,214,143,.14); color: #3ee6ad; }
.cs-move.down { background: rgba(255,92,92,.14); color: #ff8a8a; }
@media (max-width: 640px) {
  .cs-lb { grid-template-columns: 30px minmax(0, 1fr); }
  .cs-lb img, .cs-lb .ph { display: none; }
  .cs-lb .side { grid-column: 2; text-align: left; }
  .cs-lb .hype { justify-content: flex-start; }
}

/* ---- empty states -------------------------------------------------------- */
.cs-empty { text-align: center; padding: 34px 20px; border: 1px dashed #2f2f37;
  border-radius: 14px; color: #8C8C99; }
.cs-empty .i { font-size: 2rem; }
.cs-empty .t { color: #EFEFF1; font-weight: 700; font-size: 1.05rem; margin: 6px 0 4px; }

/* ---- widgets ------------------------------------------------------------- */
[data-testid="stMetric"] { background: #16161B; border: 1px solid #26262d; border-radius: 12px;
  padding: 12px 16px; }
[data-testid="stMetricLabel"] p { color: #8C8C99; font-weight: 600; font-size: .8rem; }
[data-testid="stMetricValue"] { font-weight: 800; }
[data-testid="stBaseButton-primary"], [data-testid="stBaseButton-primaryFormSubmit"] {
  background: linear-gradient(135deg, #a970ff, #772ce8); border: 0; font-weight: 700;
  box-shadow: 0 6px 20px rgba(145,70,255,.30); }
[data-testid="stBaseButton-primary"]:hover, [data-testid="stBaseButton-primaryFormSubmit"]:hover {
  filter: brightness(1.1); }
[data-testid="stBaseButton-primary"]:disabled { background: #1d1d23; color: #6b6b76;
  border: 1px solid #2a2a31; box-shadow: none; }
[data-testid="stSidebar"] [data-testid="stSidebarUserContent"] { padding-top: 1.2rem; }
.cs-brand { display: flex; align-items: center; gap: 10px; margin-bottom: 18px; }
.cs-brand .logo { width: 36px; height: 36px; border-radius: 10px; display: grid; place-items: center;
  font-size: 1.15rem; background: linear-gradient(135deg, #a970ff, #772ce8); }
.cs-brand .w { font-weight: 800; font-size: 1.1rem; }
.cs-brand .s { color: #8C8C99; font-size: .75rem; }
.cs-side-status { display: flex; align-items: center; gap: 10px; padding: 11px 13px;
  border-radius: 11px; background: #18181d; border: 1px solid #26262d; font-size: .88rem; }
.cs-side-status b { font-weight: 700; }
.cs-side-status .sub { color: #8C8C99; font-size: .76rem; }
</style>
"""


def apply():
    st.html(CSS)


def html(markup):
    st.html(markup)


def hero(title, subtitle, chips):
    """The banner at the top. chips: [(text, dot)] with dot in ok/bad/live/''."""
    chip_html = "".join('<span class="cs-chip"><span class="cs-dot %s"></span>%s</span>'
                        % (dot, escape(text)) for text, dot in chips)
    html('<div class="cs-hero"><h1>%s</h1><p>%s</p><div class="cs-chips">%s</div></div>'
         % (title, escape(subtitle), chip_html))


def step(number, title, hint=""):
    html('<div class="cs-step"><div class="n">%s</div><div><div class="t">%s</div>%s</div></div>'
         % (number, escape(title), '<div class="h">%s</div>' % escape(hint) if hint else ""))


def note(text):
    """A small grey explanation. `text` may hold <b> tags; escape anything else first."""
    html('<div class="cs-note">%s</div>' % text)


def empty_state(icon, title, text):
    html('<div class="cs-empty"><div class="i">%s</div><div class="t">%s</div>%s</div>'
         % (icon, escape(title), escape(text)))


def box_art(template, width=144, height=192):
    """A Twitch box_art_url template filled in at a given size, or ''."""
    return (template or "").replace("{width}", str(width)).replace("{height}", str(height))


def initials(name):
    words = [w for w in (name or "?").replace(":", " ").split() if w[:1].isalnum()]
    return "".join(w[0] for w in words[:2]).upper() or "?"


def art_tag(url, name):
    if url:
        return '<img src="%s" alt="">' % escape(url, quote=True)
    return '<div class="cs-art-ph">%s</div>' % escape(initials(name))


def summary(game_name, art_url, subtitle, rows):
    """The 'your run' card body: cover, name and key/value rows."""
    row_html = "".join('<div class="cs-row"><span class="k">%s</span><span class="v">%s</span></div>'
                       % (escape(k), escape(str(v))) for k, v in rows)
    html('<span class="cs-summary-anchor"></span>'
         '<div class="cs-sum-game">%s<div><div class="name">%s</div><div class="sub">%s</div>'
         '</div></div><div class="cs-rows">%s</div>'
         % (art_tag(art_url, game_name), escape(game_name), escape(subtitle), row_html))


def game_cards(cards):
    """A grid of game cards. cards: dicts with rank, name, art, tag, tag_class,
    bar (0-100), left, right, why."""
    out = []
    for card in cards:
        art = card.get("art")
        style = ("background-image:url('%s')" % escape(art, quote=True) if art else
                 "background:linear-gradient(135deg,#3a2a5c,#1f1f27)")
        out.append(
            '<div class="cs-card"><div class="art" style="%s"><span class="rank">#%d</span></div>'
            '<div class="body"><div class="name" title="%s">%s</div>'
            '<span class="cs-tag %s">%s</span>'
            '<div style="margin-top:9px" class="cs-bar"><i style="width:%d%%"></i></div>'
            '<div class="cs-meta"><span>%s</span><span>%s</span></div>'
            '<div class="why">%s</div></div></div>'
            % (style, card["rank"], escape(card["name"], quote=True), escape(card["name"]),
               card.get("tag_class", ""), escape(card.get("tag", "")),
               max(2, min(100, int(card.get("bar", 0)))), escape(card.get("left", "")),
               escape(card.get("right", "")), escape(card.get("why", ""))))
    html('<div class="cs-cards">%s</div>' % "".join(out))


def leaderboard(rows):
    """Ranked rows. rows: dicts with pos, name, url, art, sub, chips [(text, class)],
    when, left, bar (0-100)."""
    out = []
    for row in rows:
        art = ('<img src="%s" alt="">' % escape(row["art"], quote=True) if row.get("art")
               else '<div class="ph"></div>')
        chips = "".join('<span class="%s">%s</span>' % (cls, escape(text))
                        for text, cls in row.get("chips", []))
        out.append(
            '<div class="cs-lb%s"><div class="pos">%d</div>%s'
            '<div style="min-width:0"><div class="nm"><a href="%s" target="_blank" '
            'rel="noopener">%s</a></div><div class="sub">%s</div><div class="chips">%s</div></div>'
            '<div class="side"><div class="when">%s</div><div class="left">%s</div>'
            '<div class="hype">Hype %d<div class="cs-bar"><i style="width:%d%%"></i></div></div>'
            '</div></div>'
            % (" top3" if row["pos"] <= 3 else "", row["pos"], art,
               escape(row.get("url", "#"), quote=True), escape(row["name"]),
               escape(row.get("sub", "")), chips, escape(row.get("when", "")),
               escape(row.get("left", "")), row.get("bar", 0),
               max(2, min(100, int(row.get("bar", 0))))))
    html('<div class="cs-board">%s</div>' % "".join(out))
