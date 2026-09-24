"""Signing in to your own YouTube channel (Google OAuth), for the channel
results and the upload helper.

An API key reads public data; your channel's uploads and uploading need your
permission. Google's way for a program on your own PC ("installed app"):

  1. In Google Cloud (the project of your YouTube API key): APIs & Services >
     OAuth consent screen, now called Google Auth Platform > Get started, with
     Audience "External". Then Audience > Test users > add yourself, and
     Clients > Create client > Desktop app > Download JSON.
  2. Give that JSON to the page (My channel > Connect). It stays in data/.
  3. Press Connect: Google's sign-in opens in your browser; after you allow it,
     Google sends the browser back to a one-time address on this PC
     (http://127.0.0.1:<port>), which is how the page receives the permission.

The permission (a refresh token) is kept in data/youtube_token.json, on this
PC only; Disconnect deletes it and tells Google to forget it.
"""

import base64
import hashlib
import json
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from .config import DATA_DIR, HTTP_TIMEOUT
from .util import load_json, save_json

CLIENT_FILE = DATA_DIR / "youtube_client.json"
TOKEN_FILE = DATA_DIR / "youtube_token.json"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
SCOPES = ["https://www.googleapis.com/auth/youtube.readonly",
          "https://www.googleapis.com/auth/youtube.upload"]
WAIT_SECONDS = 300

_state = {"status": "idle", "url": "", "error": "", "started": 0.0}
_lock = threading.Lock()


# -- the OAuth client (the JSON from Google Cloud) -------------------------------------
def save_client(raw):
    """Store the downloaded client JSON. Returns a problem to show, or None."""
    try:
        data = json.loads(raw)
    except ValueError:
        return "That is not the JSON file Google Cloud gives you."
    client = data.get("installed") or data.get("web") or {}
    if not client.get("client_id") or not client.get("client_secret"):
        return "No client_id / client_secret in that file."
    if "installed" not in data:
        return ("That is a 'Web application' client. Make one of type 'Desktop app' - only "
                "that kind may sign in through this PC.")
    save_json(CLIENT_FILE, {"client_id": client["client_id"],
                            "client_secret": client["client_secret"]})
    return None


def client():
    data = load_json(CLIENT_FILE, {})
    return data if isinstance(data, dict) and data.get("client_id") else None


def connected():
    token = load_json(TOKEN_FILE, {})
    return bool(isinstance(token, dict) and token.get("refresh_token"))


def status():
    with _lock:
        return dict(_state)


# -- signing in ------------------------------------------------------------------------
def _pkce():
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge.decode().rstrip("=")


def start_connect(open_browser=True, auth_url=AUTH_URL, token_url=TOKEN_URL):
    """Begin the sign-in in the background. Returns the Google address to open."""
    config = client()
    if config is None:
        raise ValueError("Add the OAuth client JSON first.")
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(16)
    received = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            query = parse_qs(urlparse(self.path).query)
            if query.get("state", [""])[0] != state:
                self.send_response(400)
                self.end_headers()
                return
            received.update({k: v[0] for k, v in query.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(("<h2>%s</h2><p>You can close this tab and go back to Clip "
                              "Studio.</p>" % ("Connected." if "code" in received else
                                               "Not connected.")).encode("utf-8"))

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    redirect = "http://127.0.0.1:%d" % server.server_port
    url = auth_url + "?" + urlencode({
        "client_id": config["client_id"], "redirect_uri": redirect, "response_type": "code",
        "scope": " ".join(SCOPES), "state": state, "code_challenge": challenge,
        "code_challenge_method": "S256", "access_type": "offline", "prompt": "consent"})

    def wait():
        server.timeout = 1
        deadline = time.time() + WAIT_SECONDS
        try:
            while not received and time.time() < deadline:
                server.handle_request()
        finally:
            server.server_close()
        if "code" not in received:
            _set("failed", error=received.get("error") or "no answer from Google in time")
            return
        try:
            response = requests.post(token_url, data={
                "code": received["code"], "client_id": config["client_id"],
                "client_secret": config["client_secret"], "redirect_uri": redirect,
                "grant_type": "authorization_code", "code_verifier": verifier},
                timeout=HTTP_TIMEOUT)
            token = response.json()
        except (requests.RequestException, ValueError) as error:
            _set("failed", error=str(error)[:200])
            return
        if "refresh_token" not in token:
            _set("failed", error=token.get("error_description") or token.get("error")
                 or "Google gave no permission")
            return
        token["expires_at"] = time.time() + int(token.get("expires_in", 3600)) - 60
        save_json(TOKEN_FILE, token)
        _set("connected")

    _set("waiting", url=url)
    threading.Thread(target=wait, name="youtube-sign-in", daemon=True).start()
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:                   # the page shows the link as well
            pass
    return url


def _set(state, url="", error=""):
    with _lock:
        _state.update({"status": state, "url": url, "error": error, "started": time.time()})


def disconnect():
    """Forget the permission here, and ask Google to revoke it."""
    token = load_json(TOKEN_FILE, {})
    if isinstance(token, dict) and token.get("refresh_token"):
        try:
            requests.post(REVOKE_URL, params={"token": token["refresh_token"]},
                          timeout=HTTP_TIMEOUT)
        except requests.RequestException:
            pass
    TOKEN_FILE.unlink(missing_ok=True)
    _set("idle")


# -- calling YouTube as you ------------------------------------------------------------
def access_token(token_url=TOKEN_URL):
    """A fresh access token, refreshed when needed. Raises ValueError when not connected."""
    token = load_json(TOKEN_FILE, {})
    config = client()
    if not isinstance(token, dict) or not token.get("refresh_token") or not config:
        raise ValueError("Not connected to YouTube - press Connect on the My channel page.")
    if token.get("access_token") and time.time() < float(token.get("expires_at", 0)):
        return token["access_token"]
    try:
        fresh = requests.post(token_url, data={
            "client_id": config["client_id"], "client_secret": config["client_secret"],
            "refresh_token": token["refresh_token"], "grant_type": "refresh_token"},
            timeout=HTTP_TIMEOUT).json()
    except (requests.RequestException, ValueError) as error:
        raise ValueError("Could not reach Google: %s" % error)
    if "access_token" not in fresh:
        raise ValueError("Google took the permission back (%s) - press Connect again. Apps "
                         "left in 'Testing' lose it after 7 days."
                         % (fresh.get("error") or "no token"))
    token.update(access_token=fresh["access_token"],
                 expires_at=time.time() + int(fresh.get("expires_in", 3600)) - 60)
    save_json(TOKEN_FILE, token)
    return token["access_token"]


def headers():
    return {"Authorization": "Bearer %s" % access_token()}
