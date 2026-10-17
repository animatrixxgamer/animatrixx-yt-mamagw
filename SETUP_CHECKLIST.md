# ✅ Setup Checklist — YouTube connect, start to finish

Work top to bottom. Every step has a way to confirm it, so a failure can never
hide behind a symptom that "hasn't changed".

The bot deploys as **one file**, `bot.py`, started by `Procfile` / `railway.toml`
as `python bot.py`. Nothing else in this repo is an entry point.

---

## 0. Never deploy blind: confirm the build that is actually running

Every startup prints its own code fingerprint as the **first line of the deploy
log**:

```
🏷 Build: 55819f1a0103 · bot.py 253678b · written 2026-09-19 11:24 · up 0m
```

The fingerprint is the first 12 characters of `sha256` of `bot.py`, so compute it
on the copy you deployed:

```bash
sha256sum bot.py | cut -c1-12
```

- **Equal** → the running code is the code you built. Any remaining symptom is a
  real bug, not a stale deploy.
- **Different** → Railway is still running an older build. Stop and fix that
  first; nothing else you change will be visible.

The same fingerprint appears on `/diag`, in the Telegram `/diag` command, and as
`build=` on the status page.

---

## 1. Railway — the service

- [ ] Use your **existing** service. Do not create a new project: a new project
      gets a new domain, and the Google Cloud redirect URI is tied to
      `animatrixx-yt-manager.up.railway.app`.
- [ ] **Settings → Networking** — note the public domain. Everywhere below, if it
      is not `animatrixx-yt-manager.up.railway.app`, replace it with whatever is
      shown here.
- [ ] **Settings → Build** — the Builder must not say "Dockerfile Path". This repo
      has no Dockerfile any more; `railway.toml` pins `builder = "NIXPACKS"` and
      `startCommand = "python bot.py"`, and config-as-code overrides the dashboard.

## 2. Railway — the volume (do this before the deploy)

- [ ] **Settings → Volumes → New Volume**, mount path **`/data`**.
- [ ] Confirm after deploying: the log says
      `💾 Persistent volume: <name> — /data survives redeploys`.
      Anything else means channels are lost on the next deploy.

The volume is what keeps `bot_data.db` (your channels and jobs) **and**
`secret.key` (which decrypts the stored OAuth tokens). They live in the same
directory on purpose — if one survives a redeploy without the other, every saved
token becomes unreadable.

## 3. Railway — the variables

Variables → **RAW Editor**. These six are the complete required set:

```
BOT_TOKEN=<from @BotFather>
OWNER_ID=<your numeric id, from @userinfobot>
YOUTUBE_CLIENT_ID=<...apps.googleusercontent.com>
YOUTUBE_CLIENT_SECRET=<GOCSPX-...>
YOUTUBE_REDIRECT_URI=https://animatrixx-yt-manager.up.railway.app/oauth/youtube/callback
STORAGE_DIR=/data
```

- `STORAGE_DIR` must equal the volume mount path from step 2.
- Copy `YOUTUBE_REDIRECT_URI` from the `oauth_redirect=` line on your own `/`
  page so you cannot typo it.

**Do not set:**

| Variable | Why |
|---|---|
| `PORT` | Railway injects it; overriding it breaks the OAuth callback |
| `PUBLIC_BASE_URL` | Derived from `YOUTUBE_REDIRECT_URI` automatically |
| `TELEGRAM_BOT_TOKEN`, `DATA_DIR` | Legacy aliases of the two above |
| `RAILWAY_*`, `RENDER`, `DYNO` | Injected by the platform |
| `GITHUB_TOKEN`, `OPENROUTER_API_KEY` | Zero references in `bot.py`; they belonged to the deleted `src/` app |
| `YOUTUBE_REFRESH_TOKEN`, `YOUTUBE_CHANNEL_ID` | Nothing reads them; channels are per-user in SQLite |

**Optional:**

- `TOKEN_ENCRYPTION_KEY` — any long random string. Without it the bot generates
  `secret.key` on the volume. Set it *before* connecting a channel, not after.
- `CHANNEL_HEALTH_SECONDS` — how often to sweep channel tokens (default `21600`,
  6 hours; `0` disables).
- `HEALTH_SUMMARY_SECONDS` — how often to send the health summary to the owner
  (default `86400`, once a day; `0` disables). The last-sent time is stored in
  the database, so a redeploy does not reset the timer.

## 4. Cloud code — the repo Railway builds

- [ ] Put this code on the branch Railway deploys, and make sure the repo has **no**
      `Dockerfile`, `src/`, `docker-compose.yml` or `alembic*` left over — a
      Dockerfile in the repo is built instead of `bot.py`.
- [ ] No Git handy? Install the Railway CLI, then from the project folder:

```bash
railway login
railway link     # choose the existing service
railway up
```

- [ ] Deploy, then re-read step 0.

## 5. Google Cloud — the OAuth client

- [ ] **APIs & Services → Library → YouTube Data API v3 → Enable.**
- [ ] **Credentials → your OAuth 2.0 Client ID** — type must be **Web
      application**. A "Desktop app" client cannot use this redirect flow.
- [ ] **Authorized redirect URIs** — add both, byte for byte, no trailing slash:
  - `https://animatrixx-yt-manager.up.railway.app/oauth/youtube/callback`
  - `http://localhost:8080/oauth/youtube/callback`
- [ ] **OAuth consent screen** — add scopes `youtube.upload`, `youtube`,
      `youtube.readonly`.
- [ ] **OAuth consent screen → Publishing status → Publish app.** While it says
      *Testing*, Google expires refresh tokens after **7 days**, so every channel
      needs reconnecting weekly.
- [ ] The Google account you sign in with must have a YouTube channel (personal
      or brand). An account without one returns no channels at all.

## 6. Verify — before tapping anything in Telegram

- [ ] Open `https://animatrixx-yt-manager.up.railway.app/oauth-check`.
      It must read ✅ throughout, ending with *"Google accepted this client id,
      secret and redirect URI"*. Anything ❌ names the exact screen to change.
- [ ] Open `https://animatrixx-yt-manager.up.railway.app/` — it must show
      `channels=`, `oauth_redirect=` and `build=` matching step 0.
- [ ] Telegram: the owner receives a **Bot started** message with the same
      verdicts. If it says the browser redirect cannot work, use **Paste Code
      Manually** when connecting.

## 7. Connect the channel

- [ ] Telegram → `/start` → **Connect YouTube** → tap **Authorize YouTube in
      browser** → pick the account with the channel → Allow.
- [ ] You land on a confirmation page, and the bot messages you `✅ YouTube
      connected!` with the channel name.
- [ ] Re-open the status page: `channels=1`.

If the redirect page fails to load, tap **Paste Code Manually** instead: tap it,
tap **Open Google Auth**, approve, then copy the URL from the address bar and
paste it back — the code expires in about 60 seconds, so paste it immediately.

---

## When it fails: read the code, not the symptom

Every failure now reports Google's own error, in Telegram and in the log as
`[oauth] token endpoint 400: <error> - <description> (redirect_uri=...)`:

| Code | What it means | Fix |
|---|---|---|
| `invalid_grant` | The code was already used or is older than ~60s | Retry with **Paste Code Manually** and paste immediately. If it happens on uploads, the app is in Testing mode — step 5, Publish app |
| `redirect_uri_mismatch` | The URI sent is not registered for this client | Step 5 — add it byte for byte |
| `invalid_client` | The id/secret pair is wrong or from different clients | Step 3 — re-copy both from the same Web application client |
| `unauthorized_client` | The client type or scopes are wrong | Step 5 — Web application + scopes on the consent screen |

Fastest route to the answer: `/oauthcheck` in Telegram, or `/oauth-check` in a
browser. Both make a live token call with a throwaway code, so the verdict is
Google's, not a guess.

## Other symptoms

| Symptom | Cause |
|---|---|
| Buttons do nothing at all | A webhook is set on the token, or a second process polls with the same `BOT_TOKEN`. Polling clears webhooks at startup; only one process may poll |
| `channels=0` right after a redeploy | Storage is not on the volume — step 2 |
| "Channel needs reconnecting" message | Google revoked the grant. Reconnect; if it repeats weekly, publish the app (step 5) |
| Uploads fail with 403 | YouTube API quota for the day is used up (10,000 units/day) |

## Local checks

```bash
pip install -r requirements.txt
python -m pytest tests/ -q      # 142 tests, no network needed
python bot.py                   # runs with the bundled .env
```
