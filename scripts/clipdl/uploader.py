"""Upload helper: your Shorts to YouTube, with their title files, on a schedule.

Each upload costs 1,600 of the 10,000 free daily units - about six a day -
and is counted in yt_quota before it starts. Scheduled ones go up private
and YouTube publishes them at the chosen time.

Google's rule for new API projects: until the project passes YouTube's API
audit, everything it uploads stays private. The page says so; see
https://support.google.com/youtube/contact/yt_api_form
"""

import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from . import ytauth, yt_quota
from .config import DATA_DIR, HTTP_TIMEOUT, STOP
from .util import load_json, save_json, say

UPLOADS = DATA_DIR / "uploads.json"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
COST = yt_quota.COSTS["videos.insert"]
PRIVACY = {"private": "Private", "unlisted": "Unlisted", "public": "Public"}


def uploaded():
    """{video id: {clip_id, title, game, streamer, uploaded_at, publish_at, file}}."""
    data = load_json(UPLOADS, {})
    return data if isinstance(data, dict) else {}


def uploaded_clips():
    return {entry.get("clip_id") for entry in uploaded().values()}


def read_sidecar(video):
    """The title ideas, description and hashtags from a video's .txt (or empty)."""
    try:
        text = Path(video).with_suffix(".txt").read_text(encoding="utf-8")
    except OSError:
        return {"titles": [], "description": "", "hashtags": []}
    sections, current = {}, None
    for line in text.splitlines():
        if line in ("TITLE OPTIONS", "DESCRIPTION", "HASHTAGS"):
            current = line
            sections[current] = []
        elif current:
            sections[current].append(line)
    titles = [re.sub(r"^\s*\d+\.\s*", "", t) for t in sections.get("TITLE OPTIONS", []) if t.strip()]
    return {"titles": titles, "description": "\n".join(sections.get("DESCRIPTION", [])).strip(),
            "hashtags": " ".join(sections.get("HASHTAGS", [])).split()}


def clean_title(title):
    """YouTube refuses < and > in titles, and more than 100 characters."""
    title = re.sub(r"[<>]", "", title or "").strip()
    return title[:100] or "Clip"


def upload(path, title, description, tags=(), privacy="private", publish_at=None, meta=None,
           upload_url=UPLOAD_URL):
    """Upload one video. Returns its YouTube id; raises ValueError with the reason."""
    path = Path(path)
    if not path.exists():
        raise ValueError("The file is gone: %s" % path)
    if not yt_quota.spend(COST, "videos.insert"):
        raise ValueError("Not enough YouTube units left today (an upload needs %s)."
                         % "{:,}".format(COST))
    status = {"privacyStatus": "private" if publish_at else privacy,
              "selfDeclaredMadeForKids": False}
    if publish_at:
        status["publishAt"] = publish_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    clean_tags, length = [], 0
    for tag in (t.lstrip("#") for t in tags):
        if tag and length + len(tag) < 450:
            clean_tags.append(tag)
            length += len(tag) + 1
    body = {"snippet": {"title": clean_title(title), "description": (description or "")[:4900],
                        "tags": clean_tags, "categoryId": "20"}, "status": status}
    size = path.stat().st_size
    try:
        start = requests.post(upload_url, params={"uploadType": "resumable",
                                                  "part": "snippet,status"}, json=body,
                              headers=dict(ytauth.headers(), **{
                                  "X-Upload-Content-Type": "video/mp4",
                                  "X-Upload-Content-Length": str(size)}),
                              timeout=HTTP_TIMEOUT)
        if start.status_code != 200 or "Location" not in start.headers:
            raise ValueError(_reason(start))
        with path.open("rb") as handle:
            done = requests.put(start.headers["Location"], data=handle,
                                headers={"Content-Type": "video/mp4",
                                         "Content-Length": str(size)}, timeout=900)
    except requests.RequestException as error:
        raise ValueError("The upload broke off: %s" % error)
    if done.status_code not in (200, 201):
        raise ValueError(_reason(done))
    video_id = done.json().get("id")
    records = uploaded()
    records[video_id] = dict(meta or {}, title=body["snippet"]["title"], file=str(path),
                             uploaded_at=time.time(), privacy=status["privacyStatus"],
                             publish_at=status.get("publishAt"))
    save_json(UPLOADS, records)
    return video_id


def _reason(response):
    try:
        error = response.json()["error"]
        reason = (error.get("errors") or [{}])[0].get("reason", "")
        if reason == "uploadLimitExceeded":
            return "YouTube's daily upload limit for this channel is reached."
        if "quota" in reason.lower():
            yt_quota.exhausted()
        return "YouTube said: %s" % error.get("message", response.status_code)
    except (ValueError, KeyError, TypeError):
        return "YouTube said HTTP %s" % response.status_code


def upload_all(items, privacy, first_slot=None, every_hours=0):
    """Upload [{path, title, description, tags, meta}] one after the other, the
    first at `first_slot` (or now) and each next one `every_hours` later."""
    done, failed = 0, 0
    for i, item in enumerate(items):
        if STOP.is_set():
            say("Stopped - %d uploaded." % done)
            break
        slot = None
        if first_slot:
            slot = datetime.fromtimestamp(first_slot.timestamp() + i * every_hours * 3600,
                                          tz=timezone.utc)
        say("[%d/%d] Uploading %s%s" % (i + 1, len(items), Path(item["path"]).name,
                                        " - goes live %s" % slot.astimezone().strftime(
                                            "%a %d %b %H:%M") if slot else ""))
        try:
            video_id = upload(item["path"], item["title"], item["description"], item["tags"],
                              privacy, slot, item.get("meta"))
        except ValueError as error:
            failed += 1
            say("  Failed: %s" % error)
            if "units" in str(error) or "limit" in str(error):
                break
            continue
        done += 1
        say("  Done: https://youtube.com/shorts/%s" % video_id)
    say("Uploaded %d of %d%s." % (done, len(items), ", %d failed" % failed if failed else ""))
    return {"done": done, "failed": failed}
