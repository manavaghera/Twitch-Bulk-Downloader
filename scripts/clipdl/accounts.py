"""Who may use the web page: a fixed list of accounts, made by the owner only.

There is no sign-up. Accounts are added from the command line on the PC that
runs the page (scripts\\manage_users.py) and kept in data/users.json - which
holds salted PBKDF2 hashes, never the passwords themselves.

When hosted, accounts can come from Streamlit secrets instead, as a table of
login ID -> hash made with `manage_users.py hash`:

    [users]
    alice = "pbkdf2_sha256$600000$...$..."

WHERE THE LOGIN APPLIES
    "site"       (default) nothing on the page shows until you sign in
    "downloads"  anyone can look at the trend research, but downloading
                 clips (and the finished downloads) needs a sign-in
Set it with the CLIPDL_LOGIN environment variable / secret, or as
"login_required_for" in data/config.json. With no accounts at all the page
stays open, exactly as before.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from datetime import datetime, timezone

from .config import CONFIG_FILE, DATA_DIR
from .util import load_json, save_json

USERS_FILE = DATA_DIR / "users.json"
ITERATIONS = 600_000            # OWASP's current advice for PBKDF2-SHA256
MIN_PASSWORD = 8
SCOPES = ("site", "downloads")

# Guessing protection: this many wrong passwords for one ID within the window
# locks that ID for LOCK_SECONDS. Kept in memory for the life of the server.
MAX_FAILURES = 5
FAILURE_WINDOW = 15 * 60
LOCK_SECONDS = 5 * 60
MAX_TRACKED_IDS = 5000

# "Keep me signed in": a signed token in a browser cookie, good for this many
# days, tied to the account's current password - changing the password or
# removing the account ends every such sign-in. Signed with a random key kept
# in data/ (or CLIPDL_SESSION_SECRET), which never leaves this PC.
SESSION_DAYS = 7
SECRET_FILE = DATA_DIR / "session_secret"
REVOKED_FILE = DATA_DIR / "revoked_sessions.json"
_failures = {}                  # login id -> [timestamps of recent failures]
_failures_lock = threading.Lock()


def normalize(login_id):
    return (login_id or "").strip().lower()


def hash_password(password, iterations=ITERATIONS):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return "pbkdf2_sha256$%d$%s$%s" % (iterations, base64.b64encode(salt).decode(),
                                       base64.b64encode(digest).decode())


def _matches(stored, password):
    try:
        scheme, iterations, salt, digest = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                     base64.b64decode(salt), int(iterations))
    except (ValueError, TypeError, AttributeError):
        return False
    return hmac.compare_digest(actual, expected)


# A real hash to check against when the ID does not exist, so a wrong ID takes
# as long as a wrong password and the timing gives nothing away.
_DUMMY = hash_password(secrets.token_hex(8))


# -- the account list ---------------------------------------------------------
def _file_users():
    data = load_json(USERS_FILE, {})
    users = data.get("users") if isinstance(data, dict) else None
    return users if isinstance(users, dict) else {}


def all_users(extra=None):
    """{login id: stored hash} from users.json plus any hosted `extra` table."""
    users = {normalize(k): (v.get("hash") if isinstance(v, dict) else v)
             for k, v in _file_users().items()}
    for login_id, stored in (extra or {}).items():
        users[normalize(login_id)] = str(stored)
    return {k: v for k, v in users.items() if k and v}


def login_scope():
    wanted = os.environ.get("CLIPDL_LOGIN", "").strip().lower()
    if not wanted:
        config = load_json(CONFIG_FILE, {})
        wanted = str((config or {}).get("login_required_for") or "").strip().lower() \
            if isinstance(config, dict) else ""
    return wanted if wanted in SCOPES else "site"


def check(login_id, password, extra=None):
    """(login id, None) when the pair is right, else (None, reason to show)."""
    login_id = normalize(login_id)
    now = time.time()
    with _failures_lock:
        recent = [t for t in _failures.get(login_id, []) if now - t < FAILURE_WINDOW]
        _failures[login_id] = recent
        if len(recent) >= MAX_FAILURES and now - recent[-1] < LOCK_SECONDS:
            wait = int(LOCK_SECONDS - (now - recent[-1])) // 60 + 1
            return None, "Too many wrong tries. Try again in %d minute(s)." % wait
    stored = all_users(extra).get(login_id)
    if _matches(stored or _DUMMY, password or "") and stored:
        with _failures_lock:
            _failures.pop(login_id, None)
        return login_id, None
    with _failures_lock:
        if len(_failures) > MAX_TRACKED_IDS:
            # Someone is trying lots of made-up IDs: forget the stale ones so
            # the list cannot grow without end.
            for key in [k for k, times in _failures.items()
                        if not times or now - times[-1] > FAILURE_WINDOW]:
                del _failures[key]
        _failures.setdefault(login_id, []).append(now)
    return None, "Wrong ID or password."


# -- staying signed in ----------------------------------------------------------
def _secret():
    configured = os.environ.get("CLIPDL_SESSION_SECRET", "").strip()
    if configured:
        return configured.encode("utf-8")
    try:
        key = SECRET_FILE.read_bytes()
        if len(key) >= 32:
            return key
    except OSError:
        pass
    key = secrets.token_bytes(32)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SECRET_FILE.write_bytes(key)
    return key


def _fingerprint(stored):
    return hashlib.sha256(stored.encode("utf-8")).hexdigest()[:16]


def _sign(raw):
    return hmac.new(_secret(), raw.encode("ascii"), hashlib.sha256).hexdigest()


def issue_token(login_id, extra=None, days=SESSION_DAYS):
    """A "keep me signed in" token for this account, or None if there is no such account."""
    login_id = normalize(login_id)
    stored = all_users(extra).get(login_id)
    if not stored:
        return None
    body = {"u": login_id, "e": int(time.time() + days * 86400), "f": _fingerprint(stored),
            "n": secrets.token_hex(8)}
    raw = base64.urlsafe_b64encode(json.dumps(body, separators=(",", ":")).encode("utf-8"))
    raw = raw.decode("ascii").rstrip("=")
    return raw + "." + _sign(raw)


def _read_token(token):
    try:
        raw, signature = str(token).split(".")
        if not hmac.compare_digest(_sign(raw), signature):
            return None
        body = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except (ValueError, TypeError, UnicodeError):
        return None
    return body if isinstance(body, dict) else None


def _revoked():
    data = load_json(REVOKED_FILE, {})
    return data if isinstance(data, dict) else {}


def verify_token(token, extra=None):
    """The login id a token signs in, or None when it is forged, expired, signed
    out, or the account's password has changed since."""
    body = _read_token(token)
    if not body or float(body.get("e", 0)) < time.time():
        return None
    stored = all_users(extra).get(normalize(body.get("u")))
    if not stored or not hmac.compare_digest(_fingerprint(stored), str(body.get("f", ""))):
        return None
    if str(body.get("n")) in _revoked():
        return None
    return normalize(body["u"])


def revoke_token(token):
    """Sign this token out for good (the cookie is deleted too, but a copy would not work)."""
    body = _read_token(token)
    if not body:
        return
    now = time.time()
    revoked = {n: e for n, e in _revoked().items() if float(e) > now}   # expired ones can go
    revoked[str(body.get("n"))] = body.get("e", now)
    save_json(REVOKED_FILE, revoked)


# -- changing the list (command line only) -------------------------------------
def set_user(login_id, password):
    login_id = normalize(login_id)
    if not login_id or any(c.isspace() for c in login_id):
        raise ValueError("The ID cannot be empty or contain spaces.")
    if len(password) < MIN_PASSWORD:
        raise ValueError("Use a password of at least %d characters." % MIN_PASSWORD)
    users = _file_users()
    users[login_id] = {"hash": hash_password(password),
                       "updated": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    save_json(USERS_FILE, {"users": users})
    return login_id


def remove_user(login_id):
    users = _file_users()
    if users.pop(normalize(login_id), None) is None:
        return False
    save_json(USERS_FILE, {"users": users})
    return True


def file_user_ids():
    return sorted(_file_users())
