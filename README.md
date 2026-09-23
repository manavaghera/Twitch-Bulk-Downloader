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

**Game research** (StreamsCharts-style, across Twitch, Kick and YouTube)
- Plain-language overview: what is happening right now, the most watched games with their
  icons, platform split and change, biggest movers - and a full table for every number
  (watch hours, peak/average viewers and streamers, viewers per streamer, share, change)
  for live now / 24 hours / 7 days / 30 days.
- Game page: a one-paragraph summary, the typical (median) streamer, viewers and streamers
  over time, who is streaming it, how big its channels are, which languages watch it,
  Steam best-seller rank in 20 countries, the best hours to stream it, whose clips get
  watched, and the tags and title words streamers use (with Twitch Drops detection).
- Where would I rank?: enter your usual viewers - see the games where you'd sit on the
  first screen of the directory right now.
- Best games to stream: where an ordinary streamer (not a top-5 star) still gets viewers.
- Spikes & movers: games far above their normal for this hour, and the biggest gainers.
- Languages: games whose audience in your language outnumbers its streamers.
- Compare up to 5 games; game icons (Twitch category art) throughout the app.

**Creator tools**
- **Clip radar** - the clips taking off right now across the top 25-100 games, ranked by
  views per hour, with ads and cheat spam filtered out. Pick and download in two clicks.
- **Streamers** - follow the channels you clip: live now, when they usually stream, their
  official schedule, their best clips this week. Keep a **permission list** (Allowed / Not
  sure / Don't use) that every download respects, and mark each streamer's **camera spot**
  with a live preview.
- **Shorts that are ready to post** - three looks (blurred background, centre crop, and
  **facecam on top + gameplay below**), optional **burned-in captions** made on your PC
  (free speech to text, no API), and a **.txt of title ideas, a description with credit,
  and hashtags** (including the game's trending tags) beside every video.
- **Release calendar** - upcoming launches from Steam wishlists on a month calendar with
  countdowns, and an **.ics file** that puts them in Google Calendar / Outlook / iPhone
  with a reminder a few days before.
- **YouTube Shorts demand** - on each game page: how many views Shorts of that game got
  this week, a typical Short's views, and the top ones (needs the YouTube key).
- **Autopilot** - every day: pick the rising games (or your own list, or the clip radar),
  download their best new clips, make the Shorts and title files, and leave a report.
  Runs from the page or on a daily schedule in Windows Task Scheduler.

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

## Game research data

No platform publishes its past, so - like StreamsCharts - the app records who is live
every 15 minutes into `data/stats.db` and builds watch hours, peaks and averages from
that. History starts the first time you open Game research and fills 24 h / 7 d / 30 d
as it runs; the page shows how much of each period is recorded. Every 15 minutes it reads
the top 10,000 Twitch streams and ~1,000 Kick streams, and counts every channel of a few
big games in rotation (and of any game you *track*). History older than 7 days is thinned
to hourly; everything older than 35 days is dropped.

The page records while it is open. To record around the clock without it:

```bash
.venv\Scripts\python.exe scripts\collect_stats.py
```

(or `--once`, scheduled every 15 minutes in Windows Task Scheduler). Running both at once
is safe. Kick is read from the list its own website uses, which is not an official API and
may change.

## Captions and the Autopilot

Captions use faster-whisper, installed with the requirements; the speech model (~150 MB)
downloads on first use and runs on the CPU (about a second per clip). The Autopilot saves
into `<download folder>\Autopilot\<date>`; scheduled runs log to `datautopilot.log`.
Run it by hand with:

```bash
.venv\Scripts\python.exe scriptsutopilot.py
```

## Optional: YouTube

A free YouTube Data API v3 key adds YouTube's trending gaming videos to Trend research and
live YouTube gaming streams to Game research.

1. Open <https://console.cloud.google.com> and sign in with any Google account.
2. Top bar → project picker → **New project** → any name → **Create**, then select it.
3. **APIs & Services → Library** → search **YouTube Data API v3** → **Enable**.
4. **APIs & Services → Credentials → Create credentials → API key**, and copy it.
5. Optional but wise: **Edit API key → API restrictions → Restrict key → YouTube Data
   API v3** (leave *Application restrictions* on *None*).
6. In Clip Studio's sidebar, open **YouTube**, paste the key, press **Save & test**.
   (Or put `"youtube_api_key": "..."` in `data/config.json`, or set `YOUTUBE_API_KEY`.)

It costs nothing: the free quota is 10,000 units a day, and the app uses about 5,000 -
an hourly live-stream check (~200 units) plus ~8 per trend research run. No billing
account is needed.

## Hosting

Set these as environment variables or in `.streamlit/secrets.toml` (never commit it):

| Setting | |
|---|---|
| `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET` | required |
| `CLIPDL_HOSTED=1` | hides local-only buttons (folder picker, open folder) |
| `[users]` table | accounts: `alice = "<hash>"`, hash from `manage_users.py hash` |
| `CLIPDL_LOGIN` | `site` (default) or `downloads` |
| `YOUTUBE_API_KEY` | optional |
| `CLIPDL_NO_COLLECTOR=1` | don't record stats in this copy (e.g. several copies behind one host) |

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
