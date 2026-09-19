# 🎬 YouTube Telegram Bot — Epic Edition
![Made by Animatrixx Gamer](https://img.shields.io/badge/Made%20by-𝕬𝖓𝖎𝖒𝖆𝖙𝖗𝖎𝖝𝖝%20𝕲𝖆𝖒𝖊𝖗✪-red)

A feature-packed Telegram bot for YouTube channel management, social video
imports and script hosting, with colored inline buttons.

> **`bot.py` is the only entry point.** Standalone, SQLite, colored buttons —
> run it yourself, or let `Procfile` / `railway.toml` start `python bot.py`.
>
> A second aiogram + FastAPI app used to live in `src/` and polled Telegram with
> the *same* `BOT_TOKEN`. Two pollers on one token split your updates between
> them, which makes buttons fail at random, so that app and the `Dockerfile`
> that built it have been removed. `legacy-aiogram-app.zip` keeps a copy.

Everything below describes `bot.py`. For deploying or debugging a connection,
start with **[SETUP_CHECKLIST.md](SETUP_CHECKLIST.md)** — it has the exact env
vars, the Google Cloud settings, and how to tell a stale deploy from a real bug.

## ✨ Features

### 🎨 Colored Buttons (Bot API 9.4+)
- 🔵 Blue buttons for primary actions
- 🟢 Green buttons for confirm/start actions
- 🔴 Red buttons for danger/cancel

### 📺 YouTube
- Connect **multiple channels** through the real OAuth 2.0 callback
  (`/oauth/callback`, `/oauth/youtube/callback`, `/connect`)
- **Real resumable uploads** with live progress, retry-with-backoff and
  failure reporting (no more simulated "uploads")
- Title / description / tags / privacy editing per job
- Native YouTube scheduling (`publishAt`) for anything 15+ minutes out
- Per-video stats (views, likes, comments) and thumbnail upload from a photo
- Channel switcher with the active channel remembered per user

### 📥 Social downloads (yt-dlp)
- Paste a link in chat, or `/dl <link>`
- YouTube · TikTok · Instagram · X/Twitter · Facebook · Reddit · Vimeo ·
  Twitch · Pinterest and more
- Shows title/uploader/duration before downloading, then you choose:
  - 📥 **Download only** — the file comes back to you, nothing is uploaded
    (no YouTube channel needed at all)
  - 📤 **Download & queue** — it waits in your queue to publish

### 📦 Bulk downloads (no uploading required)
- **A whole TikTok account:** send `@username 20` and the bot fetches the
  newest 20 videos, newest first
- **Many links at once:** paste up to 50 links, one per line, mixed sites fine
- Then pick *Download only* or *Download & queue*
- Everything is kept in **My Downloads**:
  - tap a file to receive it in chat again
  - 🗂 zip a page (or a whole batch) into one archive
  - 🔗 get signed browser links (12-hour expiry) for files over 50MB
  - queue a saved file for YouTube later, or delete it
- The downloader also runs standalone: `/dl` and bulk never touch YouTube
  unless you explicitly ask

> Telegram limits: bots can **send** files up to 50MB and **download** user
> files up to 20MB. Bigger downloads are served through `/dl/<token>` on the
> bot's own web port — set `PUBLIC_BASE_URL` so those links work off-device.

### 🗂 Queue & scheduling
- Send **many videos at once** — each becomes a job with its own controls
- `/queue` to see everything waiting, `Upload now` / `Schedule` / `Remove`
- Scheduling accepts `+90m`, `+3h`, `+2d` or `2026-09-20 18:30` (UTC)
- A background worker promotes due jobs and retries failures up to 3 times

### 🎵 TikTok → YouTube auto-posting
- `/tiktok @username` or a profile link — works with a username **or** a URL
- The bot polls each source, downloads new videos and publishes them to your
  chosen channel automatically
- Per source: pause/resume, batch size (videos per check), check interval,
  target channel, privacy, and optional spacing between posts
- Also supports one-off TikTok video links through the download flow

### 🖥️ File hosting
- Host `.py`, `.js` and `.zip` uploads with dependency auto-install
- Start / Stop / **Restart** / view **Logs** / Delete — every button works

### 🔐 Security & admin
- Fernet-encrypted OAuth tokens, stored with a **stable key** that no longer
  breaks when the bot token changes
- Role-based access (owner/admin/premium/free) with per-user banning
- Admin panel: stats, user list, ban/unban, grant premium, broadcast,
  editable limits, audit log and `/diag` diagnostics
- Code scanner for uploaded scripts

## 🚀 Quick start

```bash
pip install -r requirements.txt
# fill in .env (see .env.example)
python bot.py
```

Health endpoints: `/` (status), `/health` (probe), `/diag` (config + OAuth
redirect), plus the OAuth callback routes.

## 🔑 "It keeps using my other bot token!"

`bot.py` reads **real environment variables first** and only falls back to
`.env`. On hosting, a `BOT_TOKEN` set in the dashboard therefore beats the one
in `.env` — which is why the bot can appear to ignore your token.

Three ways to deal with it:

| Option | How | Notes |
|--------|-----|-------|
| **A (recommended)** | Update `BOT_TOKEN` in the host dashboard to the token you actually want | Keeps standard precedence |
| **B** | Add `PREFER_ENV_FILE=1` to `.env` (or the host) | Every value in `.env` then wins over the environment |
| **C** | Send `/diag` (or open `/diag` on the web port) | Shows the active token (masked), where it came from, and the `@username` Telegram reports |

Extra guards that ship with this version:
- a malformed host `BOT_TOKEN` no longer takes the bot down — it falls back to
  the `.env` token and logs a warning at startup
- a localhost `YOUTUBE_REDIRECT_URI` port is auto-rewritten to match `PORT`
- empty or `0` `PORT` values are ignored (some hosts export `PORT=0`)

## 📱 Commands

| Command | Description |
|---------|-------------|
| `/start`, `/menu` | Main menu |
| `/connect` | Connect a YouTube channel |
| `/upload` | Send videos to queue |
| `/queue`, `/schedule` | Pending & scheduled uploads |
| `/dl <link>` | Download from a social link |
| 📥 Download / Save | Single link, bulk TikTok account, many links, saved files |
| `/tiktok [@user\|link]` | TikTok → YouTube auto-posting |
| `/jobs` | Upload history |
| `/cancel` | Cancel the current input |
| `/ping`, `/status` | Bot health / your status |
| `/diag` | Diagnostics (admins) |

## ⚙️ Environment

See `.env.example` for the full annotated list. Highlights:

```env
BOT_TOKEN=...
OWNER_ID=...
YOUTUBE_CLIENT_ID=...
YOUTUBE_CLIENT_SECRET=...
YOUTUBE_REDIRECT_URI=http://localhost:8080/oauth/youtube/callback

# Optional
PREFER_ENV_FILE=1            # let .env win over the host environment
SOCIAL_COOKIES_FILE=storage/cookies.txt   # unblocks TikTok/Instagram
TIKTOK_POLL_SECONDS=900
SCHEDULER_TICK_SECONDS=30
SKIP_AUTO_INSTALL=0
```

`ffmpeg` is optional but recommended — with it yt-dlp merges the best
video+audio streams (1080p); without it the bot falls back to a single
progressive MP4.

## 📺 YouTube OAuth setup

1. Create a Google Cloud project and enable **YouTube Data API v3**
2. OAuth client → type **Web application**
3. Add the redirect URI `/diag` prints, e.g.
   `http://localhost:8080/oauth/youtube/callback` (or your public URL)
4. Set `YOUTUBE_CLIENT_ID` / `YOUTUBE_CLIENT_SECRET`
5. In the bot: **Connect YouTube** → authorise → the channel is stored

## 🧪 Tests

```bash
python -m pytest -q
```

`tests/test_bot_ui.py` covers the deployed bot: it asserts that **every
inline button has a handler** (dead buttons were a real production bug), plus
scheduling parsing, link parsing, the upload queue and TikTok sources.

## 🆘 Troubleshooting

| Issue | Solution |
|-------|----------|
| Bot uses the wrong token | See "It keeps using my other bot token" above, then run `/diag` |
| Buttons do nothing | Update the bot (`pip install -r requirements.txt`) and re-run; `/diag` confirms the deployed code |
| TikTok downloads fail | Add a cookies file via `SOCIAL_COOKIES_FILE` |
| "File too big for bots to download" | Telegram caps bot downloads at 20MB — paste a link instead |
| Upload fails | Check YouTube quota (10,000 units/day ≈ 6 uploads) and re-auth `/connect` |
| OAuth redirect mismatch | Copy the redirect URI from `/diag` into Google Cloud → Credentials |

## 📝 License

MIT License — feel free to use and modify!

## 🙏 Credits

Built with ❤️ using pyTelegramBotAPI, yt-dlp, Google API Client, Flask and SQLite.

**Created & Maintained by:** 𝕬𝖓𝖎𝖒𝖆𝖙𝖗𝖎𝖝𝖝 𝕲𝖆𝖒𝖊𝖗✪
