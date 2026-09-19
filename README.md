# 🎬 YouTube Telegram Bot — Epic Edition
![Made by Animatrixx Gamer](https://img.shields.io/badge/Made%20by-𝕬𝖓𝖎𝖒𝖆𝖙𝖗𝖎𝖝𝖝%20𝕲𝖆𝖒𝖊𝖗✪-red)

A feature-packed Telegram bot for YouTube channel management, social video
imports and script hosting, with colored inline buttons.

> **`bot.py` is the only entry point.** Standalone, SQLite, colored buttons —
> run it yourself, or let `Procfile` / `railway.toml` start `python bot.py`.

Everything below describes `bot.py`. For deploying or debugging a connection,
start with **[SETUP_CHECKLIST.md](SETUP_CHECKLIST.md)** — it has the exact env
vars, the Google Cloud settings, and how to tell a stale deploy from a real bug.

## ✨ Features

### 📤 Upload Hub
- **YouTube Shorts** — upload short videos with automatic `#shorts` tag
- **Regular Videos** — standard YouTube uploads
- **From Link** — download and queue from TikTok, Instagram, YouTube, X
- **Bulk Upload** — paste many links at once
- Pick your upload type before sending — no more guessing

### 📺 Channel Dashboard
- Pick a channel → get a **full dashboard** with all options
- Upload Short, Upload Video, Analytics, My Videos, Queue
- Default privacy and default tags settings per channel
- Channel stats with subscriber count, video count, total views

### 📊 Analytics Dashboard
- Channel overview: subscribers, total videos, total views
- Top performing video with direct link
- Recent videos with views, likes, and comments for each
- Average performance across your last 10 videos
- Access via `/analytics` or the channel dashboard

### 📋 Manage Videos
- Browse all your uploads with status filters (All / Pending / Completed / Failed)
- Paginated list with status indicators
- Tap any video to see details, edit, or delete
- Quick access to queue and upload hub

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
🔑 "It keeps using my other bot token!"
bot.py reads real environment variables first and only falls back to .env. On hosting, a BOT_TOKEN set in the dashboard therefore beats the one in .env — which is why the bot can appear to ignore your token.

Three ways to deal with it:

Option	How	Notes
A (recommended)	Update BOT_TOKEN in the host dashboard to the token you actually want	Keeps standard precedence
B	Add PREFER_ENV_FILE=1 to .env (or the host)	Every value in .env then wins over the environment
C	Send /diag (or open /diag on the web port)	Shows the active token (masked), where it came from
📱 Commands
Command	Description
/start, /menu	Main menu
/mychannel, /dashboard	Open channel dashboard directly
/connect	Connect a YouTube channel
/upload	Upload hub (Shorts, Video, Link, Bulk)
/analytics, /stats	Video analytics dashboard
/queue, /schedule	Pending & scheduled uploads
/dl <link>	Download from a social link
/tiktok [@user|link]	TikTok → YouTube auto-posting
/jobs	Upload history
/cancel	Cancel the current input
/ping, /status	Bot health / your status
/diag	Diagnostics (admins)
⚙️ Environment
See .env.example for the full annotated list. Highlights:

env
Copy
BOT_TOKEN=...
OWNER_ID=...
YOUTUBE_CLIENT_ID=...
YOUTUBE_CLIENT_SECRET=...
YOUTUBE_REDIRECT_URI=http://localhost:8080/oauth/youtube/callback

# Optional
PREFER_ENV_FILE=1
SOCIAL_COOKIES_FILE=storage/cookies.txt
TIKTOK_POLL_SECONDS=900
SCHEDULER_TICK_SECONDS=30
📺 YouTube OAuth setup
Create a Google Cloud project and enable YouTube Data API v3
OAuth client → type Web application
Add the redirect URI /diag prints, e.g. http://localhost:8080/oauth/youtube/callback (or your public URL)
Set YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET
In the bot: Connect YouTube → authorise → the channel is stored
🧪 Tests
bash
Copy
python -m pytest -q
All 158 tests pass. tests/test_bot_ui.py asserts that every inline button has a handler (dead buttons were a real production bug).

🚀 Deploy
Platform	Guide	Cost
Railway	Push to GitHub → Deploy	Free trial
Fly.io	FLY_IO_DEPLOY.md	Free forever
Oracle Cloud	ORACLE_CLOUD_DEPLOY.md	Free forever
Your PC	LOCAL_PC_GUIDE.md	Electricity only
🆘 Troubleshooting
Issue	Solution
Bot uses the wrong token	Run /diag to see the active token source
Buttons do nothing	Update the bot and re-run; /diag confirms the deployed code
TikTok downloads fail	Add a cookies file via SOCIAL_COOKIES_FILE
Upload fails	Check YouTube quota (10,000 units/day ≈ 6 uploads) and re-auth /connect
OAuth redirect mismatch	Copy the redirect URI from /diag into Google Cloud → Credentials
📝 License
MIT License — feel free to use and modify!

🙏 Credits
Built with ❤️ using pyTelegramBotAPI, yt-dlp, Google API Client, Flask and SQLite.

Created & Maintained by: 𝕬𝖓𝖎𝖒𝖆𝖙𝖗𝖎𝖝𝖝 𝕲𝖆𝖒𝖊𝑟✪
