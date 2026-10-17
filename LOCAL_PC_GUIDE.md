# 🖥️ Run the Bot on Your PC 24/7

Run the bot on your own computer — no hosting fees, no cloud, just your machine.

---

## Prerequisites

- **Windows 10/11**, **macOS**, or **Linux** (Ubuntu, etc.)
- Python 3.10 or newer
- ffmpeg (optional but recommended for 1080p downloads)
- Your PC stays on and connected to the internet

---

## Step 1: Install Python

### Windows
1. Download from [python.org/downloads](https://www.python.org/downloads/)
2. **IMPORTANT:** Check "Add Python to PATH" during install
3. Click "Install Now"

### macOS
```bash
brew install python3
```

### Linux (Ubuntu/Debian)
```bash
sudo apt update && sudo apt install -y python3 python3-pip python3-venv
```

## Step 2: Install ffmpeg (recommended)

ffmpeg lets yt-dlp merge best video + audio (1080p). Without it you get 720p max.

### Windows
1. Download from [ffmpeg.org/download](https://ffmpeg.org/download.html)
2. Extract the zip
3. Add the `bin` folder to your system PATH
   - Search "Environment Variables" → Edit PATH → Add the bin folder path

### macOS
```bash
brew install ffmpeg
```

### Linux
```bash
sudo apt install -y ffmpeg
```

## Step 3: Set Up the Project

### If you have the zip file

```bash
# Extract the zip
# Windows: right-click → Extract All
# macOS/Linux:
unzip freebuff-desktop.zip -d youtube-bot
cd youtube-bot
```

### If you have the folder already

```bash
cd "path/to/your/bot/folder"
```

## Step 4: Create a Virtual Environment

```bash
# Create a virtual environment
python3 -m venv venv

# Activate it
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate
```

You should see `(venv)` in your terminal prompt.

## Step 5: Install Dependencies

```bash
pip install -r requirements.txt
```

This installs: pyTelegramBotAPI, requests, cryptography, flask, psutil, Pillow,
google-api-python-client, google-auth-oauthlib, yt-dlp.

## Step 6: Configure .env

```bash
# Copy the template
cp .env.example .env

# Edit it
# Windows: notepad .env
# macOS/Linux: nano .env
```

Fill in your values:
```env
BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
OWNER_ID=123456789
YOUTUBE_CLIENT_ID=your_client_id.apps.googleusercontent.com
YOUTUBE_CLIENT_SECRET=GOCSPX-your_secret
YOUTUBE_REDIRECT_URI=http://localhost:8080/oauth/youtube/callback
PORT=8080
```

## Step 7: Test It

```bash
python bot.py
```

You should see:
```
🎬 Starting YouTube Bot...
🏷 Build: abc123...
🔑 Token: 1234567...xyz
👑 Owner: 123456789
🤖 Signed in as @YourBotName (id 1234567890)
```

Open Telegram, find your bot, and test it!

Press `Ctrl+C` to stop.

---

## Running It 24/7

The bot needs to stay running to process uploads and TikTok checks.
Here are your options:

### Option A: Terminal Window (Simplest)

Just keep the terminal open with `python bot.py` running.
- ✅ Simple — no setup needed
- ❌ Closes if you close the terminal
- ❌ Closes if you log out (Windows)

### Option B: Nohup (macOS/Linux)

```bash
# Start in background
nohup python bot.py > bot.log 2>&1 &

# Check if running
ps aux | grep bot.py

# View logs
tail -f bot.log

# Stop it
kill $(pgrep -f "python bot.py")
```

### Option C: Screen / tmux (Linux/macOS)

```bash
# Start a screen session
screen -S bot

# Run the bot
python bot.py

# Detach: press Ctrl+A, then D
# Bot keeps running in background!

# Re-attach later:
screen -r bot

# List sessions:
screen -ls
```

### Option D: PM2 (All Platforms)

PM2 is a process manager that auto-restarts crashes.

```bash
# Install PM2
npm install -g pm2

# Start the bot
pm2 start "python bot.py" --name youtube-bot

# Useful commands:
pm2 status              # Check status
pm2 logs youtube-bot    # View logs
pm2 restart youtube-bot # Restart
pm2 stop youtube-bot    # Stop
pm2 delete youtube-bot  # Remove

# Auto-start on boot (Linux/macOS):
pm2 startup
pm2 save
```

### Option E: Windows Task Scheduler (Windows)

1. Open **Task Scheduler**
2. Click **Create Basic Task**
3. Name: `YouTube Bot`
4. Trigger: **When the computer starts**
5. Action: **Start a program**
   - Program: `C:\path\to\python.exe` (use the venv python)
   - Arguments: `bot.py`
   - Start in: `C:\path\to\your\bot\folder`
6. Check **"Run with highest privileges"**
7. Finish

---

## Keeping Your PC On

If you want the bot running 24/7, your PC needs to stay on:

### Windows
- Go to **Settings → System → Power & Sleep**
- Set **"Screen"** to turn off after 10 minutes (saves power)
- Set **"Sleep"** to **Never** (bot keeps running)
- Disable sleep on lid close (laptops): Power Options → Change what closing the lid does

### macOS
- Go to **System Settings → Battery → Options**
- Disable **"Put display to sleep when inactive"** for the screen only
- Or use `caffeinate -s` to prevent sleep:
  ```bash
  caffeinate -s python bot.py
  ```

### Linux
```bash
# Prevent sleep
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
```

---

## Network Requirements

The bot needs internet access for:
- Telegram API (long polling)
- YouTube API (OAuth + uploads)
- yt-dlp (downloads from social sites)
- Flask server (OAuth callback on port 8080)

### Firewall

If you use a firewall, allow:
- **Outbound:** All (for Telegram, YouTube, downloads)
- **Inbound port 8080:** Only if you need the OAuth callback from Google
  - Most people use "Paste Code Manually" so this isn't strictly needed

### Router

No port forwarding needed! The bot connects outbound to Telegram via long polling.
The Flask server on port 8080 only needs to be reachable if you use the
automatic OAuth browser flow (the "Paste Code Manually" flow works without it).

---

## Power Consumption

| PC Type | Idle Power | Annual Cost (est.) |
|---------|-----------|-------------------|
| Desktop PC | ~60-100W | $50-90/year |
| Laptop | ~10-20W | $10-20/year |
| Raspberry Pi 4 | ~3-5W | $3-5/year |
| Old phone (Termux) | ~2-3W | $2-3/year |

A **Raspberry Pi** or **old laptop** is ideal for running a bot 24/7 cheaply.

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| "BOT_TOKEN missing" | Check your `.env` file has the right token |
| Bot stops when closing terminal | Use PM2, screen, or nohup (see above) |
| Bot stops on Windows sleep | Disable sleep in Power Settings |
| "Port 8080 in use" | Change `PORT` in `.env` or kill the other process |
| Uploads fail | Check YouTube quota — 10,000 units/day ≈ 6 uploads |
| TikTok downloads fail | Add a cookies file: `SOCIAL_COOKIES_FILE=storage/cookies.txt` |
| "No module named telebot" | Activate the venv first: `source venv/bin/activate` |
