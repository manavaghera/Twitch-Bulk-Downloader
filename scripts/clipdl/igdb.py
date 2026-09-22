"""IGDB: the game database Twitch owns, used to tie every other source together.

IGDB knows each game's Steam app id, its Wikipedia article and its release
date, and Twitch itself tags categories with IGDB ids. That makes it the one
place that can say "Steam app 730, Twitch's 'Counter-Strike' and the
'Counter-Strike 2' Wikipedia article are the same game" without guessing from
names. It also publishes how often each game's page is visited, which is a
read on what people are looking up right now.

It logs in with the same Twitch Client ID and app token the rest of the
project already uses, so there is nothing new to set up.
"""

import requests

from .config import HTTP_TIMEOUT, STOP
from .util import chunked

IGDB = "https://api.igdb.com/v4/"
STEAM_SOURCE = 1        # external_game_source id for Steam
VISITS = 1              # popularity_type id for "Visits" on IGDB


class IGDBClient:
    """POSTs Apicalypse queries to IGDB. Every failure returns [] so runs go on."""

    def __init__(self, twitch_api):
        self.twitch = twitch_api
        self.session = requests.Session()

    def query(self, endpoint, body):
        for attempt in range(1, 4):
            if STOP.is_set():
                return []
            headers = {"Client-ID": self.twitch.client_id,
                       "Authorization": "Bearer %s" % self.twitch.token()}
            try:
                response = self.session.post(IGDB + endpoint, headers=headers,
                                             data=body.encode("utf-8"),
                                             timeout=HTTP_TIMEOUT)
            except requests.RequestException:
                STOP.wait(attempt * 2)
                continue
            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError:
                    return []
            if response.status_code == 401 and attempt == 1:
                self.twitch.token(force=True)
                continue
            if response.status_code == 429 or response.status_code >= 500:
                # IGDB allows 4 requests a second; a short pause is enough.
                STOP.wait(attempt)
                continue
            return []
        return []


def steam_to_igdb(client, appids):
    """{steam appid: igdb game id}."""
    found = {}
    for batch in chunked(sorted(set(appids)), 200):
        uids = ",".join('"%s"' % appid for appid in batch)
        rows = client.query("external_games",
                            "fields game,uid; where external_game_source = %d & uid = (%s);"
                            " limit 500;" % (STEAM_SOURCE, uids))
        for row in rows:
            if row.get("uid") and row.get("game"):
                found.setdefault(str(row["uid"]), int(row["game"]))
    return found


def game_details(client, igdb_ids):
    """{igdb id: {name, released, wiki: {lang: url}, steam, hypes}} for a set of games.

    `released` is a unix timestamp of the first release on any platform, or
    None for a game with no date yet. `steam` is its Steam app id, if any.
    """
    details = {}
    ids = sorted({int(i) for i in igdb_ids if i})
    for batch in chunked(ids, 250):
        rows = client.query(
            "games",
            "fields name,first_release_date,hypes,websites.url,websites.type,"
            "external_games.uid,external_games.external_game_source;"
            " where id = (%s); limit 500;" % ",".join(str(i) for i in batch))
        for row in rows:
            wiki = [site.get("url") for site in row.get("websites") or []
                    if "wikipedia.org" in (site.get("url") or "")]
            steam = next((str(ext.get("uid")) for ext in row.get("external_games") or []
                          if ext.get("external_game_source") == STEAM_SOURCE
                          and ext.get("uid")), None)
            details[int(row["id"])] = {
                "name": row.get("name") or "",
                "released": row.get("first_release_date"),
                "wiki": wiki,
                "steam": steam,
                "hypes": int(row.get("hypes") or 0),   # follows before release
            }
    return details


def most_visited(client, limit=60):
    """[(igdb id, share of visits)] - the games IGDB's visitors look up most.

    IGDB recomputes this daily, so it moves as fast as attention does: a game
    that launched this week is here long before it climbs a player chart.
    """
    rows = client.query("popularity_primitives",
                        "fields game_id,value; where popularity_type = %d;"
                        " sort value desc; limit %d;" % (VISITS, limit))
    return [(int(row["game_id"]), float(row.get("value") or 0))
            for row in rows if row.get("game_id")]
