# 🚀 Deploy to Fly.io — Free Tier with Persistent Storage

Fly.io gives you **3 free shared VMs** with persistent storage, which is perfect
for this bot. Unlike Railway, your SQLite database and OAuth tokens survive restarts.

---

## Prerequisites

- [Fly.io account](https://fly.io/sign-up) (free, no credit card needed)
- `flyctl` CLI installed ([install guide](https://fly.io/docs/hands-on/install-flyctl/))
- Your bot already works locally (test with `python bot.py`)

---

## Step 1: Install the Fly CLI

```bash
# macOS / Linux
curl -L https://fly.io/install.sh | sh

# Or via brew
brew install flyctl
```

## Step 2: Log in

```bash
fly auth login
```

This opens a browser — sign up or log in.

## Step 3: Create your app

```bash
cd "your-bot-folder"
fly launch
```

When prompted:
- **App name:** `your-bot-name` (or accept the suggested one)
- **Region:** pick the one closest to you (e.g. `sjc` for San Francisco, `lhr` for London)
- **Overwrite `Dockerfile`?** → Yes (use the one in your project)

## Step 4: Create a persistent volume

**This is the critical step** — it keeps your database alive across deploys.

```bash
fly volumes create bot_data --region sjc --size 1
```

> Replace `sjc` with your chosen region and `1` with size in GB.
> 1GB is enough for the SQLite database, downloaded files, and cookies.

## Step 5: Set environment variables

```bash
fly secrets set BOT_TOKEN="your_bot_token_here"
fly secrets set OWNER_ID="your_telegram_user_id"
fly secrets set YOUTUBE_CLIENT_ID="your_client_id"
fly secrets set YOUTUBE_CLIENT_SECRET="your_client_secret"
fly secrets set YOUTUBE_REDIRECT_URI="https://your-app.fly.dev/oauth/youtube/callback"
fly secrets set PUBLIC_BASE_URL="https://your-app.fly.dev"
```

> ⚠️ The `YOUTUBE_REDIRECT_URI` must be `https` (Fly provides free SSL).
> Register this exact URL in Google Cloud → Credentials → Authorized redirect URIs.

## Step 6: Update fly.toml

Replace or update `fly.toml` in your project root:

```toml
app = "your-bot-name"
primary_region = "sjc"

[build]
  dockerfile = "Dockerfile"

[env]
  STORAGE_DIR = "/data"
  PORT = "8080"

[http_service]
  internal_port = 8080
  force_https = true
  auto_stop_machines = false
  auto_start_machines = true
  min_machines_running = 0

  [http_service.concurrency]
    type = "connections"
    hard_limit = 250
    soft_limit = 200

[[vm]]
  memory = "512mb"
  cpu_kind = "shared"
  cpus = 1
```

## Step 7: Update Dockerfile

Make sure your `Dockerfile` maps the volume:

```dockerfile
FROM python:3.12-slim

# Install ffmpeg for video merging
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py .

# Persistent storage mount point
RUN mkdir -p /data/uploads /data/youtube /data/logs /data/social

EXPOSE 8080

CMD ["python", "bot.py"]
```

## Step 8: Deploy!

```bash
fly deploy
```

That's it! Your bot is now running for free with persistent storage.

---

## Verify it works

```bash
# Check logs
fly logs

# Check status
fly status

# Open the health endpoint
curl https://your-app.fly.dev/health
```

## Important Google Cloud Update

After deploying, Google needs to know your new redirect URI:

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Navigate to **APIs & Services → Credentials**
3. Click your OAuth 2.0 Client ID
4. Under **Authorized redirect URIs**, add:
   ```
   https://your-app.fly.dev/oauth/youtube/callback
   ```
5. Click **Save**

Then reconnect your channel in the bot.

---

## Managing secrets

```bash
# Add or update a secret
fly secrets set KEY="value"

# List all secrets
fly secrets list

# Remove a secret
fly secrets unset KEY
```

## Updating the bot

When you make changes to `bot.py`:

```bash
fly deploy
```

Your database and files in `/data` survive every deploy.

## Useful commands

```bash
# View logs (live)
fly logs

# SSH into the machine
fly ssh console

# Check volume status
fly volumes list

# Restart the app
fly apps restart your-bot-name
```

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Bot crashes on start | Run `fly logs` to see the error |
| "No YouTube channel connected" after deploy | Volumes are working, but tokens expired — reconnect via `/connect` |
| Buttons do nothing | Check `fly secrets list` — all secrets must be set |
| OAuth fails with 400 | Make sure `YOUTUBE_REDIRECT_URI` matches what's in Google Cloud exactly |
| "channels=0" after redeploy | Volume not attached — check `fly volumes list` shows your volume |

---

## Cost

Fly.io free tier includes:
- **3 shared-cpu-1x VMs** (256MB RAM each — our bot needs ~128MB)
- **3GB persistent storage** per volume (we only need ~1GB)
- **160GB outbound transfer** per month
- **Unlimited inbound**

This is more than enough for one bot running 24/7.
