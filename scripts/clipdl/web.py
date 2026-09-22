"""Public web sources for the trend research: Steam, Wikipedia, Kick, YouTube.

None of these need an account, except YouTube, which needs a free API key and
is skipped without one. Each one is optional: if a site is
down or changes shape, the run carries on without it and says so, rather than
losing every other signal because one of them failed.

  STEAM MOST PLAYED   Valve's own chart of the top games by daily peak players,
                      with each game's rank the week before - so a climb, or a
                      brand-new entry, is visible directly.

  STEAM TOP SELLERS   the best-seller chart as the Steam store shows it in one
                      country. Asking in the US, the UK, Germany, France and so
                      on is the most direct read on "popular in the US and
                      Europe" that exists without paying for data.

  WIKIPEDIA           daily page views for a game's article in English, German
                      and French. People look a game up when they hear about it,
                      so a jump here is the internet at large paying attention,
                      not just Steam or Twitch.

  KICK                live viewers per category on Kick, the other big
                      streaming site, read from the list its browse page uses.

  YOUTUBE             the most popular gaming videos in the US, UK, Germany and
                      France, counted per game by name.
"""

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote, urlparse

import requests

from .config import DATA_DIR, HTTP_TIMEOUT, STOP
from .util import chunked, load_json, save_json

USER_AGENT = "TwitchTrendResearch/1.0 (personal research script; python-requests)"
STEAM_APPS_FILE = DATA_DIR / "steam_apps.json"   # appid -> {name, type}

# The Western store charts asked for. Country codes as the Steam store uses them.
CHART_COUNTRIES = [
    ("US", "United States"), ("GB", "United Kingdom"), ("DE", "Germany"),
    ("FR", "France"), ("ES", "Spain"), ("IT", "Italy"), ("PL", "Poland"),
    ("SE", "Sweden"),
]
CHART_DEPTH = 25           # how far down each country's chart to read
MOST_PLAYED_DEPTH = 60     # how far down the most-played chart to read

# Wikipedia editions read, standing in for the US/UK plus the two biggest
# non-English European audiences.
WIKI_LANGUAGES = ("en", "de", "fr")


class WebClient:
    """A small requests wrapper: one session, a real User-Agent, a few retries.

    Wikipedia asks every script to identify itself, and Steam is quicker to
    throttle anonymous-looking traffic, hence the explicit User-Agent.
    """

    def __init__(self, attempts=4):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.attempts = attempts

    @staticmethod
    def _retry_after(response, attempt):
        """Seconds a 429 asks us to wait, within reason."""
        try:
            return max(1.0, min(30.0, float(response.headers.get("Retry-After"))))
        except (TypeError, ValueError):
            return min(attempt * 3.0, 15.0)

    def get_json(self, url, params=None):
        """GET and parse JSON. Returns None on any failure - callers degrade."""
        for attempt in range(1, self.attempts + 1):
            if STOP.is_set():
                return None
            try:
                response = self.session.get(url, params=params, timeout=HTTP_TIMEOUT)
            except requests.RequestException:
                STOP.wait(attempt * 2)
                continue
            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError:
                    return None
            if response.status_code == 404:
                return None
            if response.status_code == 429:
                # Wikipedia in particular says exactly how long to back off.
                STOP.wait(self._retry_after(response, attempt))
                continue
            if response.status_code >= 500:
                STOP.wait(min(attempt * 3, 15))
                continue
            return None
        return None


def normalize_name(name):
    """Fold a game name so the same game matches across sites.

    Steam writes "HELLDIVERS(TM) 2", Twitch "HELLDIVERS 2", IGDB "Helldivers 2".
    Dropping case, trademark signs and punctuation makes all three one key.
    """
    text = (name or "").lower().replace("™", "").replace("®", "").replace("©", "")
    text = text.replace("&", " and ")
    # Kick writes "Grand Theft Auto V (GTA)": a bracketed tail is a nickname.
    text = re.sub(r"\s*\([^)]*\)\s*$", "", text)
    # "Red Dead Redemption II" on one site is "Red Dead Redemption 2" on another.
    words = [ROMAN.get(word, word) for word in re.split(r"[^a-z0-9]+", text) if word]
    key = "".join(words)
    return NAME_ALIASES.get(key, key)


ROMAN = {"ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6", "vii": "7",
         "viii": "8", "ix": "9"}

# Games the sites genuinely name differently, after folding. Left: any
# spelling; right: the one key they should all share.
NAME_ALIASES = {
    "counterstrike2": "counterstrike",
    "pubgbattlegrounds": "pubg",
    "playerunknownsbattlegrounds": "pubg",
    "battlegrounds": "pubg",
    "grandtheftautovenhanced": "grandtheftauto5",
    "grandtheftauto5enhanced": "grandtheftauto5",    # after "V" became "5"
    "grandtheftautogta5": "grandtheftauto5",
    "gta5": "grandtheftauto5",
    "easportsfc27": "eafc27",
    "fc27": "eafc27",
    "tomclancysrainbow6siege": "rainbow6siege",
    "tomclancysrainbowsixsiege": "rainbow6siege",
    "rainbowsixsiege": "rainbow6siege",
    "rainbowsixsiegex": "rainbow6siege",
    "garenafreefire": "freefire",
    "callofdutywarzone": "warzone",
}

# Streaming categories that are not games. Folded with normalize_name.
NON_GAMES = {
    "justchatting", "irl", "music", "art", "specialevents", "asmr",
    "talkshowsandpodcasts", "slots", "slotsandcasino", "virtualcasino", "sports",
    "poolshottubsandbeaches", "travelandoutdoors", "foodanddrink",
    "scienceandtechnology", "softwareandgamedevelopment", "makersandcrafting",
    "gamesanddemos", "crypto", "cryptoandtrading", "politics",
    "animalsaquariumsandzoos", "fitnessandhealth", "beautyandbodyart",
    "chatroulette", "justsleeping", "musicstations", "alwayson",
    "coworkingandstudying", "imonlysleeping", "writingandreading", "djs",
    "tabletoprpgs", "gambling", "news", "dancing", "workout", "cooking",
    "lifestyle", "horseracing", "casino", "poker", "stocksandbonds",
}


def is_game(name):
    return normalize_name(name) not in NON_GAMES


# ---------------------------------------------------------------------------
# Steam
# ---------------------------------------------------------------------------
def steam_most_played(web, depth=MOST_PLAYED_DEPTH):
    """Valve's most-played chart: [{appid, rank, last_week_rank, peak}]."""
    payload = web.get_json(
        "https://api.steampowered.com/ISteamChartsService/GetMostPlayedGames/v1/")
    ranks = ((payload or {}).get("response") or {}).get("ranks") or []
    rows = []
    for row in ranks[:depth]:
        if row.get("appid"):
            rows.append({
                "appid": str(row["appid"]),
                "rank": int(row.get("rank") or 0),
                # 0 means it was not on the chart a week ago: a new entry.
                "last_week_rank": int(row.get("last_week_rank") or 0),
                "peak": int(row.get("peak_in_game") or 0),
            })
    return rows


_APP_ID = re.compile(r"/apps/(\d+)/")


def steam_top_sellers(web, country, depth=CHART_DEPTH):
    """One country's Steam best-seller chart, games only: [(appid, name)]."""
    payload = web.get_json("https://store.steampowered.com/search/results/", {
        "filter": "topsellers", "cc": country, "l": "english", "json": 1,
        "count": depth, "category1": 998,   # 998 = games, not hardware or software
    })
    rows = []
    for item in (payload or {}).get("items") or []:
        match = _APP_ID.search(item.get("logo") or "")
        if match:
            rows.append((match.group(1), item.get("name") or ""))
    return rows


def steam_app_names(web, appids):
    """{appid: {"name", "type"}} for apps no other source could name.

    Only used for the handful of chart entries IGDB does not know, so the
    one-app-per-request store endpoint is fine. Results are cached on disk
    because an app's name and type never change.
    """
    cache = load_json(STEAM_APPS_FILE, {})
    if not isinstance(cache, dict):
        cache = {}
    missing = [appid for appid in appids if appid not in cache]
    for appid in missing:
        payload = web.get_json("https://store.steampowered.com/api/appdetails",
                               {"appids": appid, "filters": "basic", "l": "english"})
        entry = (payload or {}).get(appid) or {}
        data = entry.get("data") if entry.get("success") else None
        if data:
            cache[appid] = {"name": data.get("name") or "", "type": data.get("type") or ""}
    if missing:
        save_json(STEAM_APPS_FILE, cache)
    return {appid: cache[appid] for appid in appids if appid in cache}


# ---------------------------------------------------------------------------
# Wikipedia
# ---------------------------------------------------------------------------
def wiki_title_from_url(url):
    """('en', 'Valorant') from 'https://en.wikipedia.org/wiki/Valorant', else None."""
    parsed = urlparse(url or "")
    host = parsed.netloc.lower()
    if not host.endswith(".wikipedia.org") or not parsed.path.startswith("/wiki/"):
        return None
    language = host.split(".")[0]
    if language in ("www", "m"):
        return None
    title = unquote(parsed.path[len("/wiki/"):]).replace("_", " ")
    return (language, title) if title else None


def wiki_find_titles(web, names):
    """{name: English article title} for games no other source linked.

    Tries "<name> (video game)" before "<name>" so "Rust" lands on the game,
    not the programming language, and refuses disambiguation pages. A plain
    "<name>" page is only taken when Wikipedia's own short description says it
    is a game: otherwise "Dressmaker" reads the views of the sewing trade and
    "Permafrost" those of frozen soil.
    """
    found = {}
    candidates = {}
    for name in names:
        # Titles are case-sensitive, and Steam and Twitch like capitals:
        # "WARDOGS" has to be asked for as "Wardogs".
        spellings = [name, name.title()] if name.isupper() else [name]
        for spelling in spellings:
            candidates.setdefault(spelling + " (video game)", name)
            candidates.setdefault(spelling, name)
    for batch in chunked(list(candidates), 50):
        payload = web.get_json("https://en.wikipedia.org/w/api.php", {
            "action": "query", "titles": "|".join(batch), "redirects": 1,
            "prop": "pageprops|description", "ppprop": "disambiguation", "format": "json",
        })
        query = (payload or {}).get("query") or {}
        # Follow the normalised/redirected spelling back to what we asked for.
        renamed = {}
        for step in (query.get("normalized") or []) + (query.get("redirects") or []):
            renamed[step.get("to")] = renamed.get(step.get("from"), step.get("from"))
        exists = {}
        for page in (query.get("pages") or {}).values():
            if "missing" in page or "disambiguation" in (page.get("pageprops") or {}):
                continue
            asked = renamed.get(page.get("title"), page.get("title"))
            if asked.endswith(" (video game)") or is_game_description(page.get("description")):
                exists[asked] = page.get("title")
        for asked, name in candidates.items():
            if asked in exists and name not in found:
                # "(video game)" is listed first, so it wins when both exist.
                if asked.endswith(" (video game)") or name + " (video game)" not in exists:
                    found[name] = exists[asked]
    return found


GAME_WORDS = ("game", "mmo", "esport")


def is_game_description(text):
    """True for short descriptions like "2024 video game" or "MMORPG"."""
    text = (text or "").lower()
    return any(word in text for word in GAME_WORDS) and "game show" not in text


def wiki_descriptions(web, titles):
    """{title as asked: Wikipedia short description} for English articles."""
    found = {}
    for batch in chunked(sorted(set(titles)), 50):
        payload = web.get_json("https://en.wikipedia.org/w/api.php", {
            "action": "query", "titles": "|".join(batch), "redirects": 1,
            "prop": "description", "format": "json", "formatversion": 2})
        query = (payload or {}).get("query") or {}
        asked = {title: title for title in batch}
        for step in (query.get("normalized") or []) + (query.get("redirects") or []):
            asked[step.get("to")] = asked.get(step.get("from"), step.get("from"))
        for page in query.get("pages") or []:
            if "missing" not in page:
                found[asked.get(page.get("title"), page.get("title"))] =                     page.get("description") or ""
    return found


def wiki_other_languages(web, english_titles, languages=("de", "fr")):
    """{english title: {lang: title}} via Wikipedia's own interlanguage links."""
    links = {title: {} for title in english_titles}
    for language in languages:
        for batch in chunked(list(english_titles), 50):
            payload = web.get_json("https://en.wikipedia.org/w/api.php", {
                "action": "query", "titles": "|".join(batch), "prop": "langlinks",
                "lllang": language, "lllimit": 500, "format": "json",
            })
            for page in (((payload or {}).get("query") or {}).get("pages") or {}).values():
                for link in page.get("langlinks") or []:
                    if page.get("title") in links and link.get("*"):
                        links[page["title"]][language] = link["*"]
    return links


def wiki_weekly_views(web, articles):
    """{(lang, title): (this week, the week before)} in daily page views."""
    return {article: windows[7] for article, windows in wiki_views(web, articles).items()}


def wiki_views(web, articles, windows=(7,)):
    """{(lang, title): {days: (last `days` days, the `days` before)}} in page views.

    Uses the MediaWiki API's pageviews property, which returns the daily views
    of 50 articles per request - a few requests per language instead of one per
    article, which is the difference between seconds and minutes. The window
    ends yesterday, the last full day Wikimedia has published. Wikipedia keeps
    60 days here, so the longest window is 30 days against the 30 before.
    """
    longest = min(max(windows), 30)
    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
    days = [(yesterday - timedelta(days=offset)).isoformat() for offset in range(2 * longest)]
    spans = {n: (set(days[:n]), set(days[n:2 * n])) for n in windows if n <= longest}

    by_language = {}
    for language, title in articles:
        by_language.setdefault(language, []).append(title)

    results = {}
    for language, titles in by_language.items():
        for batch in chunked(sorted(set(titles)), 50):
            # redirects=1: "Baldur's Gate III" is only a redirect to the real
            # article, and the redirect page itself gets a handful of views.
            params = {"action": "query", "titles": "|".join(batch), "prop": "pageviews",
                      "pvipdays": min(60, 2 * longest + 1), "redirects": 1, "format": "json",
                      "formatversion": 2}
            asked = {title: title for title in batch}
            while True:
                payload = web.get_json("https://%s.wikipedia.org/w/api.php" % language, params)
                query = (payload or {}).get("query") or {}
                for step in (query.get("normalized") or []) + (query.get("redirects") or []):
                    asked[step.get("to")] = asked.get(step.get("from"), step.get("from"))
                for page in query.get("pages") or []:
                    views = page.get("pageviews") or {}
                    if not views:
                        continue
                    title = asked.get(page.get("title"), page.get("title"))
                    results[(language, title)] = {
                        n: _window_sums(views, recent_days, older_days)
                        for n, (recent_days, older_days) in spans.items()}
                more = (payload or {}).get("continue")
                if not more:
                    break
                params.update(more)
    return results


def _window_sums(views, recent_days, older_days):
    recent = sum(v or 0 for d, v in views.items() if d in recent_days)
    # An article younger than the older window cannot be compared.
    had_older = any(v is not None for d, v in views.items() if d in older_days)
    older = sum(v or 0 for d, v in views.items() if d in older_days)
    return recent, older if had_older else 0


# ---------------------------------------------------------------------------
# Kick
# ---------------------------------------------------------------------------
def kick_categories(web, pages=3):
    """Kick's categories by live viewers right now: [(name, viewers)].

    This is the list kick.com's own browse page loads. It is not a documented
    API, so it may change without notice; the run simply goes on without Kick
    if it does.
    """
    rows = []
    for page in range(1, pages + 1):
        payload = web.get_json("https://kick.com/api/v1/subcategories",
                               {"limit": 100, "page": page})
        data = (payload or {}).get("data") or []
        rows.extend((item.get("name") or "", int(item.get("viewers") or 0))
                    for item in data if item.get("name"))
        if not data or not (payload or {}).get("next_page_url"):
            break
    return rows


# ---------------------------------------------------------------------------
# YouTube (optional - needs a free API key in data/config.json)
# ---------------------------------------------------------------------------
YOUTUBE_REGIONS = ("US", "GB", "DE", "FR")


def youtube_trending_gaming(web, api_key, regions=YOUTUBE_REGIONS, pages=2):
    """Titles + tags of the most popular gaming videos per region: [(region, text)].

    videos.list with chart=mostPopular costs 1 quota unit per page, so a whole
    run spends about 8 of the 10,000 free daily units.
    """
    rows = []
    for region in regions:
        token = None
        for _ in range(pages):
            params = {"part": "snippet", "chart": "mostPopular", "videoCategoryId": 20,
                      "regionCode": region, "maxResults": 50, "key": api_key}
            if token:
                params["pageToken"] = token
            payload = web.get_json("https://www.googleapis.com/youtube/v3/videos", params)
            if not payload:
                break
            for video in payload.get("items") or []:
                snippet = video.get("snippet") or {}
                text = " ".join([snippet.get("title") or ""] + (snippet.get("tags") or []))
                rows.append((region, text.lower()))
            token = payload.get("nextPageToken")
            if not token:
                break
    return rows


def mentions(texts, name):
    """How many of `texts` mention a game by name, as whole words."""
    clean = (name or "").lower().replace("\u2122", "").replace("\u00ae", "").strip()
    if len(clean) < 3:
        return 0
    pattern = re.compile(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(clean))
    return sum(1 for text in texts if pattern.search(text))
