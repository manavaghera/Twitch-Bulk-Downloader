# Twitch Bulk Downloader · Clip Studio

Find the games blowing up in the US and Europe this week, then bulk-download their best
Twitch clips as ready-to-edit videos or vertical Shorts.

It runs as a web page on your own PC (Streamlit) or as two command-line tools.

## Features

**Clip downloader**
- Top clips of any Twitch game/category from the last 24 hours, 7 days or 30 days,
  ranked by views or by views-per-hour ("trending now").
- English channels only; talking/caster/watch-party clips filtered out; repeats of the
  same moment skipped; clips you already have are never fetched twice.
- 16:9 videos, 1080×1920 vertical Shorts (blurred background or centre crop), or both.
- Up to 1080p, or the maximum each clip has (up to 4K). Optional one-file `.zip`.

**Trend research**
- Most popular and getting-popular games this week, US and Europe, from Steam charts
  (8 countries), Steam most-played, IGDB, Wikipedia page views, Twitch, Kick and
  (optionally) YouTube, with a confidence rating per game.
- **Steam wishlists**: the top 10/20 most-wishlisted upcoming games, release dates and
  countdowns, 7- and 30-day movement, and a boom verdict for each.
- RPM estimates by country and by game audience.

**Access control**
- Optional ID/password sign-in with owner-created accounts only (no sign-up), for the
  whole page or just for downloads.

## Requirements

- Windows 10/11 (built and tested there; macOS/Linux should work but are untested)
- Python 3.10+
- A free Twitch app: Client ID + Client Secret from
  [dev.twitch.tv/console](https://dev.twitch.tv/console)
  (Register Your Application → OAuth Redirect URL `http://localhost` → Category
  *Application Integration* → Confidential)
- ffmpeg, only for Shorts (`winget install Gyan.FFmpeg`; otherwise the bundled
  `imageio-ffmpeg` is used)

## Quick start

```bat
git clone https://github.com/manavaghera/Twitch-Bulk-Downloader.git
cd Twitch-Bulk-Downloader
scripts\start_web_ui.bat
```

The first run creates a private Python environment in `.venv` and installs the
requirements; then the page opens at <http://localhost:8501>. Paste your Twitch Client ID
and Secret in the sidebar once - they are saved to `data/config.json` on your PC only.

Manual start on any OS:

```bash
python -m venv .venv
.venv/bin/pip install -r scripts/requirements.txt      # Windows: .venv\Scripts\pip
.venv/bin/streamlit run scripts/web_app.py             # run from the project folder
```

Command-line versions of the two tools:

```bash
python scripts/twitch_clip_downloader.py
python scripts/twitch_trends.py
```

## Sign-in (optional)

With no accounts, the page is open to whoever can reach it - fine on your own PC.
To require a sign-in, create accounts from the project folder (passwords are typed
hidden and stored only as salted PBKDF2 hashes in `data/users.json`):

```bash
.venv\Scripts\python.exe scripts\manage_users.py add alice
.venv\Scripts\python.exe scripts\manage_users.py list
```

By default the whole page needs a sign-in. To protect only downloads, add
`"login_required_for": "downloads"` to `data/config.json` (or set `CLIPDL_LOGIN=downloads`).

## Optional: YouTube signal

Add a free YouTube Data API v3 key to `data/config.json` as `"youtube_api_key"`
(or the `YOUTUBE_API_KEY` environment variable) to include YouTube's trending gaming
videos in the research. Uses about 8 of the 10,000 free daily quota units per run.

## Hosting

Set these as environment variables or in `.streamlit/secrets.toml` (never commit it):

| Setting | |
|---|---|
| `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET` | required |
| `CLIPDL_HOSTED=1` | hides local-only buttons (folder picker, open folder) |
| `[users]` table | accounts: `alice = "<hash>"`, hash from `manage_users.py hash` |
| `CLIPDL_LOGIN` | `site` (default) or `downloads` |
| `YOUTUBE_API_KEY` | optional |

A hosted page with no accounts (and no legacy `APP_PASSWORD`) stays locked by design,
so strangers cannot run downloads on your server. Set `CLIPDL_PUBLIC=1` only if you
really want it open. Downloads run on the server; hosted users get them as a `.zip`.

## What stays on your machine

Everything under `data/` (credentials, token cache, accounts, run history, caches) and
`downloads/` is git-ignored and never leaves your PC.

## Notes

- Steam keeps wishlist counts private; the wishlist rank on Steam's public
  "most wishlisted" chart is the signal used, and rank movement builds up as research
  runs over the following days.
- Popularity and verdicts are estimates from public signals, not guarantees.
- Clips belong to their creators. Respect Twitch's Terms of Service and the streamers'
  rights when you reuse them.
