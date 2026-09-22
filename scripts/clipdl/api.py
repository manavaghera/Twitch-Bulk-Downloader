"""The Twitch Helix client: tokens, retries and the handful of endpoints used."""

import threading
import time

import requests

from .config import (CONFIG_FILE, HELIX, HTTP_ATTEMPTS, HTTP_TIMEOUT, OAUTH_URL,
                     STOP, TOKEN_FILE)
from .util import chunked, load_json, save_json, say

# ---------------------------------------------------------------------------
# Twitch Helix API client
# ---------------------------------------------------------------------------
class TwitchError(RuntimeError):
    """A Twitch problem worth showing the user in plain language."""


class TwitchAPI:
    """Thin wrapper around the endpoints this script needs.

    Handles the client-credentials login, caches the app access token on disk,
    renews it on expiry or on any HTTP 401, and retries on 429/5xx/network hiccups.
    """

    def __init__(self, client_id, client_secret):
        self.client_id = client_id
        self.client_secret = client_secret
        self.session = requests.Session()
        self._token = None
        self._expires_at = 0.0
        self._token_lock = threading.Lock()
        self._load_cached_token()

    # -- token handling -----------------------------------------------------
    def _load_cached_token(self):
        """Reuse a token from a previous run if it is still valid."""
        cached = load_json(TOKEN_FILE, None)
        if not isinstance(cached, dict):
            return
        # A token belongs to one app, so ignore a cache from other credentials.
        if cached.get("client_id") != self.client_id:
            return
        token = cached.get("access_token")
        expires_at = float(cached.get("expires_at") or 0)
        if token and expires_at > time.time() + 60:
            self._token = token
            self._expires_at = expires_at

    def _fetch_token(self):
        """Client credentials flow: swap id+secret for an app access token."""
        say("Getting a Twitch app access token...")
        try:
            response = self.session.post(
                OAUTH_URL,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "grant_type": "client_credentials",
                },
                timeout=HTTP_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise TwitchError(
                "Could not reach Twitch to log in (%s).\n"
                "  Check your internet connection, VPN or firewall and try again." % exc
            )

        if response.status_code in (400, 401, 403):
            raise TwitchError(
                "Twitch rejected your Client ID / Client Secret (HTTP %s).\n"
                "  * Check for a stray space or a missing character.\n"
                "  * Generate a new secret at https://dev.twitch.tv/console.\n"
                "  * Delete %s to be asked for the credentials again."
                % (response.status_code, CONFIG_FILE)
            )
        if response.status_code != 200:
            raise TwitchError(
                "Twitch returned HTTP %s while issuing a token: %s"
                % (response.status_code, response.text[:200])
            )

        try:
            payload = response.json()
        except ValueError:
            raise TwitchError("Twitch sent an unreadable login reply. Try again shortly.")

        token = payload.get("access_token")
        if not token:
            raise TwitchError("Twitch did not return an access token. Try again shortly.")

        # Renew a minute early so a long run never uses a token mid-expiry.
        lifetime = int(payload.get("expires_in") or 3600)
        self._token = token
        self._expires_at = time.time() + max(lifetime - 60, 60)
        save_json(TOKEN_FILE, {
            "client_id": self.client_id,
            "access_token": token,
            "expires_at": self._expires_at,
        })

    def token(self, force=False):
        """Return a usable token, fetching a new one when needed."""
        with self._token_lock:
            if force or not self._token or time.time() >= self._expires_at:
                self._fetch_token()
            return self._token

    def _headers(self):
        return {
            "Client-ID": self.client_id,
            "Authorization": "Bearer " + self.token(),
        }

    # -- request plumbing ---------------------------------------------------
    @staticmethod
    def _retry_after(response):
        """How long to wait after an HTTP 429, from whichever header is present."""
        header = response.headers.get("Retry-After")
        if header:
            try:
                return max(1.0, min(float(header), 120.0))
            except ValueError:
                pass
        # Helix normally sends Ratelimit-Reset as a unix timestamp instead.
        reset = response.headers.get("Ratelimit-Reset")
        if reset:
            try:
                return max(1.0, min(float(reset) - time.time(), 120.0))
            except ValueError:
                pass
        return 5.0

    @staticmethod
    def _describe(response):
        """Turn an error response into a sentence a human can act on."""
        try:
            message = (response.json() or {}).get("message", "")
        except ValueError:
            message = ""
        message = message or response.text[:200] or "no details given"
        if response.status_code == 400:
            return "Twitch rejected the request (HTTP 400): %s" % message
        if response.status_code in (401, 403):
            return ("Twitch refused the request (HTTP %s): %s\n"
                    "  Your credentials may have been revoked - delete %s and re-enter them."
                    % (response.status_code, message, CONFIG_FILE))
        if response.status_code == 404:
            return "Twitch could not find that (HTTP 404): %s" % message
        return "Twitch API error HTTP %s: %s" % (response.status_code, message)

    def _pause(self, attempt, reason):
        """Exponential backoff between retries, interruptible with Ctrl+C."""
        if attempt >= HTTP_ATTEMPTS:
            return
        wait = min(2 ** attempt, 30)
        say("  %s - retrying in %d s (attempt %d of %d)."
            % (reason, wait, attempt + 1, HTTP_ATTEMPTS))
        STOP.wait(wait)

    def get(self, path, params=None):
        """GET a Helix endpoint and return the parsed JSON body."""
        url = HELIX + path
        refreshed = False
        last_error = "unknown error"
        for attempt in range(1, HTTP_ATTEMPTS + 1):
            if STOP.is_set():
                raise TwitchError("Cancelled.")
            try:
                response = self.session.get(
                    url, headers=self._headers(), params=params, timeout=HTTP_TIMEOUT
                )
            except requests.RequestException as exc:
                last_error = "Network problem (%s)" % exc
                self._pause(attempt, last_error)
                continue

            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError:
                    last_error = "Twitch sent a reply that was not JSON"
                    self._pause(attempt, last_error)
                    continue

            # An expired or revoked token: get a new one and try once more.
            if response.status_code == 401 and not refreshed:
                refreshed = True
                say("  Access token no longer accepted - requesting a fresh one.")
                self.token(force=True)
                continue

            if response.status_code == 429:
                wait = self._retry_after(response)
                say("  Twitch rate limit reached; waiting %.0f s." % wait)
                STOP.wait(wait)
                continue

            if response.status_code >= 500:
                last_error = "Twitch server error (HTTP %s)" % response.status_code
                self._pause(attempt, last_error)
                continue

            # 400/403/404 and friends will not improve by retrying.
            raise TwitchError(self._describe(response))

        raise TwitchError("Gave up after %d tries. Last problem: %s"
                          % (HTTP_ATTEMPTS, last_error))

    def get_page(self, path, params):
        """GET one page and return (items, next_cursor)."""
        payload = self.get(path, params)
        items = payload.get("data") or []
        cursor = (payload.get("pagination") or {}).get("cursor")
        return items, cursor

    # -- endpoints we use ---------------------------------------------------
    def top_games(self, count=20):
        """GET /helix/games/top - the categories with the most viewers now."""
        return self.get("/games/top", {"first": max(1, min(count, 100))}).get("data") or []

    def top_games_deep(self, count=100):
        """GET /helix/games/top, following pages until `count` games are in hand."""
        games, cursor = [], None
        while len(games) < count:
            params = {"first": min(100, count - len(games))}
            if cursor:
                params["after"] = cursor
            batch, cursor = self.get_page("/games/top", params)
            if not batch:
                break
            games.extend(batch)
            if not cursor:
                break
        return games[:count]

    def live_streams(self, game_id, sample=100):
        """GET /helix/streams for one game - a sample of who is live right now.

        Twitch returns these biggest-first, so a single page is a fair read on
        where a game's audience is: the long tail of tiny streams barely moves
        a viewer-weighted share.
        """
        return self.get(
            "/streams", {"game_id": game_id, "first": max(1, min(sample, 100))}
        ).get("data") or []

    def game_by_name(self, name):
        """GET /helix/games?name= - exact (case-insensitive) name match, or None."""
        data = self.get("/games", {"name": name}).get("data") or []
        return data[0] if data else None

    def search_categories(self, query, limit=10):
        """GET /helix/search/categories - fuzzy 'did you mean' suggestions."""
        return self.get(
            "/search/categories", {"query": query, "first": max(1, min(limit, 100))}
        ).get("data") or []

    def channel_languages(self, broadcaster_ids):
        """GET /helix/channels in batches of 100 -> {broadcaster_id: language}.

        Get Channel Information is the endpoint that exposes
        `broadcaster_language`; /helix/users does not carry a language field at
        all, which is why the clip's own data is not enough here.
        """
        languages = {}
        for batch in chunked(list(broadcaster_ids), 100):
            # requests turns a list value into repeated query parameters,
            # i.e. ?broadcaster_id=1&broadcaster_id=2 ... which is what Twitch wants.
            rows = self.get("/channels", {"broadcaster_id": batch}).get("data") or []
            for row in rows:
                broadcaster_id = row.get("broadcaster_id")
                if broadcaster_id:
                    languages[broadcaster_id] = (row.get("broadcaster_language") or "").lower()
        return languages


    def video_titles(self, video_ids):
        """GET /helix/videos in batches of 100 -> {video_id: stream title}.

        A clip carries the id of the VOD it was cut from, and that VOD keeps the
        title the streamer had up at the time. That one line is what separates
        "VCT WATCH PARTY" from "ranked grind" for clips whose own title is
        ambiguous, and Twitch gives 100 of them per request.
        """
        titles = {}
        for batch in chunked(list(video_ids), 100):
            try:
                rows = self.get("/videos", {"id": batch}).get("data") or []
            except TwitchError:
                # Stream titles are a bonus signal, never a reason to stop a run.
                continue
            for row in rows:
                if row.get("id"):
                    titles[row["id"]] = row.get("title") or ""
        return titles
