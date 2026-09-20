# 🎬 YouTube Telegram Bot — Epic Edition

![Made by Animatrixx Gamer](https://img.shields.io/badge/Made%20by-𝕬𝖓𝖎𝖒𝖆𝖙𝖗𝖎𝖝𝖝%20𝕲𝖆𝖒𝖊𝖗✪-red)

```
╔══════════════════════════════════════════════╗
║  🎬  YouTube Bot — Epic Edition             ║
║  📤  Upload Shorts & Videos                 ║
║  📊  Analytics Dashboard                    ║
║  🎵  TikTok → YouTube Auto-Post             ║
║  📥  Download from Any Social Platform       ║
║  🔐  Multi-User, Encrypted, Admin Panel     ║
╚══════════════════════════════════════════════╝
```

> ⚡ **The only Telegram bot you need for YouTube.**
> Upload Shorts, regular videos, import from TikTok, Instagram, X — all from your phone.

> 🏗️ **`bot.py` is the only entry point.** Standalone, SQLite, colored buttons —
> run it yourself, or let `Procfile` / `railway.toml` start `python bot.py`.

---

## ✨ What's New 🆕

```
🆕 Upload Hub         — Pick Shorts, Video, Link, or Bulk before uploading
🆕 Channel Dashboard  — Full control panel after picking a channel
🆕 Analytics Dashboard — Views, likes, subscribers at a glance
🆕 Manage Videos      — Browse, filter, edit, delete your uploads
🆕 /mychannel         — One command to open your dashboard
🆕 /analytics         — One command to see your stats
🆕 Shorts Auto-Tag    — Upload a Short and #shorts is added automatically
```

---

## 📤 Upload Hub

```
┌─────────────────────────────────┐
│  📤  UPLOAD HUB                 │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │
│                                 │
│  📺  Target: My Channel         │
│  🔒  Privacy: Private           │
│                                 │
│  📹  YouTube Short              │
│      Under 60s, vertical        │
│      Gets #shorts tag auto!     │
│                                 │
│  🎬  Regular Video              │
│      Standard YouTube upload    │
│                                 │
│  🔗  From Link                  │
│      TikTok / Instagram / YT    │
│                                 │
│  📦  Bulk Upload                │
│      Many links at once         │
│                                 │
│  💡 Tip: Use a photo as a       │
│     thumbnail for your upload!  │
└─────────────────────────────────┘
```

---

## 📺 Channel Dashboard

```
┌─────────────────────────────────┐
│  📺  My Awesome Channel         │
│  ✅  Active channel             │
│                                 │
│  📊  Queue: ⏳ 3 · ✅ 12 · ❌ 1 │
│                                 │
│  📹  Upload Short               │
│  🎬  Upload Video               │
│  🔗  From Link                  │
│  📦  Bulk Upload                │
│                                 │
│  📋  My Videos                  │
│  📊  Analytics                  │
│  📈  Quick Stats                │
│  📅  Queue & Schedule           │
│                                 │
│  🔒  Default Privacy            │
│  📝  Default Tags               │
│  🎵  TikTok Auto                │
│  📥  My Downloads               │
└─────────────────────────────────┘
```

---

## 📊 Analytics Dashboard

```
┌─────────────────────────────────┐
│  📊  ANALYTICS DASHBOARD        │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │
│                                 │
│  📺  My Awesome Channel         │
│                                 │
│  👥  Subscribers: 1.2K          │
│  🎬  Total videos: 56           │
│  👀  Total views: 45.3K         │
│                                 │
│  🔥  Top video                  │
│      My Best Video Ever         │
│      👀 12.5K views             │
│      🔗 youtu.be/abc123         │
│                                 │
│  📈  Recent Videos              │
│  🎬 Video One                   │
│     👀 2.1K · 👍 156 · 💬 23   │
│  🎬 Video Two                   │
│     👀 1.8K · 👍 98 · 💬 15    │
│                                 │
│  💡 Post consistently, use      │
│     Shorts, engage comments!    │
└─────────────────────────────────┘
```

---

## 📋 Manage Videos

```
┌─────────────────────────────────┐
│  📋  MY VIDEOS (24 total)       │
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │
│  Filter: All                    │
│                                 │
│  ⏳ #25  My New Upload          │
│  ✅ #24  Short #1               │
│  ✅ #23  Another Video          │
│  ❌ #22  Failed Upload          │
│  ✅ #21  TikTok Import          │
│                                 │
│  [All] [⏳] [✅] [❌]          │
│  [⬅️ Prev] [Page 1/5] [Next ➡️]│
└─────────────────────────────────┘
```

---

## 🎨 Colored Buttons (Bot API 9.4+)

```
🔵  Blue    — Primary actions
🟢  Green   — Confirm / Start / Success
🔴  Red     — Danger / Cancel / Delete
```

---

## 📺 YouTube

- 🔗 Connect **multiple channels** through real OAuth 2.0
- ⬆️ **Real resumable uploads** with live progress and retry
- ✏️ Edit title / description / tags / privacy per video
- 🗓 Native YouTube scheduling (`publishAt`)
- 📊 Per-video stats (views, likes, comments)
- 🖼 Thumbnail upload from a photo
- 🔄 Channel switcher — active channel remembered per user

---

## 📥 Social Downloads

```
📱 Supported platforms:
   YouTube · TikTok · Instagram · X/Twitter · Facebook
   Reddit · Vimeo · Twitch · Pinterest · Dailymotion
   Snapchat · Likee · Kwai · Rumble · Odysee
```

- 📥 **Download only** — file comes to you, nothing uploaded
- 📤 **Download & queue** — waits in your queue for YouTube

---

## 📦 Bulk Downloads

- 🎵 **Whole TikTok account:** `@username 20` → fetches 20 newest videos
- 🔗 **Many links at once:** paste up to 50 links, mixed sites
- 🗂 Zip everything into one archive
- 🔗 Get browser download links for files over 50MB

---

## 🗂 Queue & Scheduling

- 📋 Send many videos at once — each becomes a job
- ⏰ Scheduling: `+90m` · `+3h` · `+2d` · `2026-09-20 18:30`
- 🔄 Background worker retries failures up to 3 times
- 📤 Upload all at once or one by one

---

## 🎵 TikTok → YouTube Auto-Post

- 🎵 `/tiktok @username` or profile link
- 🔄 Bot polls, downloads new videos, publishes automatically
- ⏸ Per source: pause/resume, batch size, interval, channel, privacy
- 📅 Optional spacing between posts

---

## 🖥️ File Hosting

- 📄 Host `.py`, `.js`, `.zip` with auto dependency install
- ▶️ Start / ⏹ Stop / 🔄 Restart / 📄 Logs / 🗑 Delete

---

## 🔐 Security & Admin

- 🔐 Fernet-encrypted OAuth tokens
- 👑 Role-based access: Owner / Admin / Premium / Free
- 🛡 Admin panel: stats, users, ban/unban, premium, broadcast
- 🧪 Code scanner for uploaded scripts
- 📜 Audit log of all actions

---

## 🚀 Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# Edit .env with your tokens

# 3. Run!
python bot.py
```

---

## 📱 Commands

```
/start  /menu          🏠  Main menu
/mychannel /dashboard  📺  Channel dashboard
/connect               🔗  Connect YouTube
/upload                📤  Upload hub
/analytics  /stats     📊  Analytics dashboard
/queue  /schedule      📋  Queue & scheduling
/dl <link>             📥  Download from link
/tiktok @user          🎵  TikTok auto-post
/jobs                  📜  Upload history
/cancel                ❌  Cancel current input
/ping  /status         🏓  Bot health
/diag                  🧪  Diagnostics (admin)
```

---

## ⚙️ Environment Variables

```env
# Required
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
PUBLIC_BASE_URL=https://your-app.fly.dev
```

---

## 🚀 Deploy for Free

| Platform | Guide | Cost |
|----------|-------|------|
| 🚂 **Railway** | Push to GitHub → Deploy | Free trial |
| 🪁 **Fly.io** | `FLY_IO_DEPLOY.md` | ✅ Free forever |
| ☁️ **Oracle Cloud** | `ORACLE_CLOUD_DEPLOY.md` | ✅ Free forever |
| 🖥️ **Your PC** | `LOCAL_PC_GUIDE.md` | Electricity only |

> 💡 **No domain needed!** Free hosting gives you a subdomain automatically:
> `your-bot.fly.dev`, `your-bot.up.railway.app`, etc.

---

## 🧪 Tests

```bash
python -m pytest -q
# ✅ 158 tests pass
```

Every inline button is verified to have a handler — no dead buttons in production.

---

## 🆘 Troubleshooting

| Issue | Fix |
|-------|-----|
| Bot uses wrong token | Run `/diag` to see the active token |
| Buttons do nothing | Update bot and re-run |
| TikTok downloads fail | Add cookies file via `SOCIAL_COOKIES_FILE` |
| Upload fails | Check quota (10K units/day ≈ 6 uploads), re-auth |
| OAuth redirect mismatch | Copy URI from `/diag` into Google Cloud |

---

## 📝 License

MIT License — feel free to use and modify!

## 🙏 Credits

Built with ❤️ using pyTelegramBotAPI, yt-dlp, Google API Client, Flask and SQLite.

```
╔══════════════════════════════════════════════╗
║  👑  Created & Maintained by                ║
║  𝕬𝖓𝖎𝖒𝖆𝖙𝖗𝖎𝖝𝖝 𝕲𝖆𝖒𝖊𝖗✪                      ║
║  📣  @animatrixxhub                         ║
║  💬  @animatrixxgamer                       ║
╚══════════════════════════════════════════════╝
```
