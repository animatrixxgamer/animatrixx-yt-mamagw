# Multi-user YouTube OAuth — fixes, config, migration

Everything here applies to **`bot.py`** (that is what the `Procfile` runs:
`worker: python bot.py`). The `src/` package is a separate, currently unused
codebase.

---

## 1. Audit: what was actually wrong

| # | Bug | Where | Impact |
|---|-----|-------|--------|
| 1 | OAuth env vars read with **no `.strip()`** | `bot.py` ~248-254 | A trailing newline/space on a Railway secret (`YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REDIRECT_URI`) makes Google answer **every** token call with `400 invalid_client` / `redirect_uri_mismatch`. This is the #1 cause of "400 Bad Request" with a perfectly fresh code. |
| 2 | Token exchange raised `requests.HTTPError`; callers had to dig into `exc.response.json()` | `exchange_code` | Google's real reason (`invalid_grant` vs `redirect_uri_mismatch`) was often lost, so users only saw "400 Client Error: Bad Request". |
| 3 | Code extraction was **duplicated** in two places (Flask callback + paste handler) with different decoding rules | `_handle_oauth_callback`, `handle_text` | Easy for one path to double-encode `%2F` → `%252F` → `invalid_grant`. |
| 4 | `oauth_states` rows never expired and were consumed *after* the token exchange | `_handle_oauth_callback` | A stale/refreshable callback page could replay an already-used code, and old links looked valid forever ("Link expired" vs a confusing Google error). |
| 5 | No state TTL → a code older than 60 s was still exchanged | `_handle_oauth_callback` | Guaranteed `invalid_grant`. |
| 6 | **No Disconnect feature at all** in `bot.py` | — | Users could never unlink or revoke a channel. |
| 7 | `invalid_grant` on refresh was surfaced as a raw exception and left the stale row in place | `credentials_for_channel` | "channels=N" but uploads fail forever; nothing told the user to reconnect. |
| 8 | Access tokens re-read from SQLite on every API call | `credentials_for_channel` | Extra DB hits, and no in-process cache after a refresh. |
| 9 | Missing `youtube.readonly` scope | `YT_SCOPES` | `channels.list` / `videos.list` stats calls depend on it. |
| 10 | The OAuth app is in Google **"Testing** publishing status (noted in your own `.env`) | Google Cloud | Refresh tokens **expire after 7 days** → recurring `invalid_grant`. No code change can fix this; see §3. |

Global `YOUTUBE_REFRESH_TOKEN` / `YOUTUBE_CHANNEL_ID` were already **never read**
by any code (verified repo-wide), so they cannot cause `channels=0` — that was
the old single-channel design. They are now documented as inert.

> **Multi-user was already partially implemented**: `youtube_channels` is keyed
> by `(user_id, channel_id)`, and `run_job_upload()` →
> `credentials_for_channel(uid, channel_id)` already resolves per-user tokens.
> The work here hardens it, removes the duplication and adds the missing pieces.

---

## 2. Code changes (all in `bot.py`)

| Line | Change |
|------|--------|
| 248-259 | `YT_CLIENT_ID` / `YT_CLIENT_SECRET` / `YT_REDIRECT_URI` are now `.strip()`ed; `youtube.readonly` scope added. |
| 1079-1086 | New `TOKEN_ENDPOINT`, `REVOKE_ENDPOINT`, `OAUTH_STATE_TTL_SECONDS = 600`, in-memory `ACCESS_TOKEN_CACHE`. |
| 1088-1110 | New `OAuthError` exception + `extract_auth_code()` — the **single** place a code is percent-decoded, exactly once. |
| 1139-1191 | `exchange_code()` / `refresh_access_token()` now go through `_token_request()`, which parses Google's JSON error body into a typed `OAuthError` and logs it. New `revoke_token()`. |
| 1209-1275 | `credentials_for_channel()` — serves from cache, refreshes via `grant_type=refresh_token`, and on `invalid_grant` marks the row `status='revoked'` with `last_error` and raises a message telling the user to reconnect. |
| 695-748 | New `save_channel_tokens()` (shared storage path) and `disconnect_channel()` (revoke at Google + delete row + reset default channel + drop cache). |
| 379-382, 443-448 | `youtube_channels` gains `status` and `last_error`; `_ensure_columns` migrates old databases automatically. |
| 1950-2020 | Flask callback: state TTL, single-use state burned *before* the exchange, typed error pages, `save_channel_tokens()`, and a warning when Google omits `refresh_token`. |
| 2772-2825 | New **Disconnect** UI: per-channel button, confirmation, revoke + delete. |
| 4869-4960 | Paste handler now uses `extract_auth_code()` + `save_channel_tokens()` + typed errors (30 s cooldown kept). |

### Why `youtube_channels` and not a new `youtube_accounts` table

Your proposed `youtube_accounts (telegram_user_id PRIMARY KEY, …)` allows exactly
**one** channel per user. `youtube_channels (user_id, channel_id) PRIMARY KEY`
is the same thing *plus* multi-channel support, and it already carries the
`access_token`, `token_expiry`, `thumbnail` and `connected_at` fields. Creating a
parallel table would mean two sources of truth for the same tokens.

If you still want the name for reporting, add the read-only view in §4.

---

## 3. Google Cloud Console — what to change

**Authorized redirect URIs** (APIs & Services → Credentials → your *Web
application* OAuth client):

```
https://animatrixx-yt-manager.up.railway.app/oauth/youtube/callback
https://animatrixx-yt-manager.up.railway.app/oauth/callback
http://localhost:8080/oauth/youtube/callback
```

- The value must match `YOUTUBE_REDIRECT_URI` **byte for byte** — no trailing
  slash, `https` not `http`, same path.
- `YOUTUBE_REDIRECT_URI` on Railway must be exactly
  `https://animatrixx-yt-manager.up.railway.app/oauth/youtube/callback`.
- Keep the legacy `/oauth/callback` entry so old links don't break.

**Authorized JavaScript origins** — not needed for this flow.

**Scopes** — add `https://www.googleapis.com/auth/youtube.readonly` on the OAuth
consent screen if it isn't there.

**⚠️ Publish the app (the important one).** OAuth consent screen → *Publishing
status*. While it says **Testing**, Google issues refresh tokens that **expire
after 7 days**, which is exactly the recurring `invalid_grant` you saw (your own
`.env` predicted this for ~2026-09-20). Either click **Publish app** (fine for
personal use; you'll see the "unverified app" warning) or re-connect every week.

---

## 4. Database migration

`init_db()` runs on every start and adds the new columns itself via
`_ensure_columns`. To apply the same change by hand (SQLite):

```sql
ALTER TABLE youtube_channels ADD COLUMN status TEXT DEFAULT 'connected';
ALTER TABLE youtube_channels ADD COLUMN last_error TEXT;
UPDATE youtube_channels SET status = 'connected' WHERE status IS NULL;
```

Optional compatibility view matching the requested `youtube_accounts` shape:

```sql
CREATE VIEW IF NOT EXISTS youtube_accounts AS
SELECT user_id        AS telegram_user_id,
       refresh_token  AS refresh_token,   -- Fernet-encrypted blob
       channel_id     AS channel_id,
       channel_name   AS channel_title,
       connected_at   AS connected_at
FROM   youtube_channels;
```

Clear old one-shot sign-in states (safe; they are single-use anyway):

```sql
DELETE FROM oauth_states;
```

After deploying, delete the now-meaningless `YOUTUBE_REFRESH_TOKEN` and
`YOUTUBE_CHANNEL_ID` variables from the Railway dashboard so nobody is misled.

---

## 5. Deploying on Railway

### 5a. Two Railway-specific landmines (both are real causes of your symptoms)

**1. Ephemeral filesystem wipes the database.** Railway resets the container
filesystem on every deploy, restart and rebuild. The bot stores everything in
`storage/bot_data.db`, so **every redeploy deletes `youtube_channels`** — which
is exactly what `channels=0` looks like, even right after a successful connect.

Fix: Service → **Settings → Volumes → New Volume**, mount path **`/data`**, then
set `STORAGE_DIR=/data` (bot.py also auto-detects Railway's
`RAILWAY_VOLUME_MOUNT_PATH`). The startup banner now prints
`Storage dir:` / `Database:` so you can confirm it in the deploy logs.

**2. The root `Dockerfile` builds the wrong app.** It only copies `src/`, never
`bot.py`, and runs `uvicorn src.main:app` (async aiogram + Postgres + Redis).
Railway prefers a root Dockerfile over auto-detection, so it can build an image
where `bot.py` does not exist. `railway.toml` now pins `builder = "NIXPACKS"` and
`startCommand = "python bot.py"`. **Verify in Railway → the service → Settings →
Build that the Builder is "Nixpacks" and not "Dockerfile".**

### 5b. Variables to set (Railway dashboard → Variables)

Copy-paste list lives in **`.env.railway.example`**. The important ones:

```
BOT_TOKEN=<from @BotFather>
OWNER_ID=<your numeric id>
YOUTUBE_CLIENT_ID=<...apps.googleusercontent.com>
YOUTUBE_CLIENT_SECRET=<GOCSPX-...>
YOUTUBE_REDIRECT_URI=https://animatrixx-yt-manager.up.railway.app/oauth/youtube/callback
STORAGE_DIR=/data
```

- **Do not set `PORT`** — Railway injects it and the bot honours it.
- **Delete `YOUTUBE_REFRESH_TOKEN` and `YOUTUBE_CHANNEL_ID`** if they are in the
dashboard. Nothing reads them.
- Dashboard variables **win** over the bundled `.env`, so the `.env` in the zip
(which still says `localhost:8080`) cannot break the deployed bot — but you must
set `YOUTUBE_REDIRECT_URI` in the dashboard, otherwise the bundled localhost
value is used and Google redirects to a dead localhost.

### 5c. Confirm the public domain

Railway → service → **Settings → Networking → Public Networking**. The redirect
URI must use the exact domain shown there. If it is not
`animatrixx-yt-manager.up.railway.app`, update **both** `YOUTUBE_REDIRECT_URI` in
the dashboard **and** the Authorized redirect URI in Google Cloud.

### 5d. Deploy and verify

1. Push/deploy, then open the deploy **logs**. You should see:
   `🔗 OAuth redirect URI: https://animatrixx-yt-manager.up.railway.app/oauth/youtube/callback`
   `🗄  Storage dir: /data`
   `🤖 Signed in as @animatrixx_yt_manager_bot`
   `🌐 Web server started on port <PORT>`
2. In Telegram send `/diag` — it must show the same redirect URI and
   `YouTube API libs: ✅`.
3. Send `/start`, tap **Connect YouTube**, finish consent. You should land on
   "Channel connected" and get the ✅ message in Telegram.
4. **Redeploy on purpose**, then send `/status`. If `channels=` is still ≥ 1 the
   volume is working. If it reset to 0, the volume is not mounted correctly.

---

## 6. Test checklist

Automated (`123 passed`):

```bash
.venv/bin/python -m pytest tests/ -q
```

Manual, on the deployed bot:

1. `/diag` shows `OAuth redirect URI` = the Railway URL, and `channels=` grows
   only when you connect.
2. **New user, second account:** from a different Telegram account, tap
   *Connect YouTube*, finish consent → you get "✅ YouTube connected" naming
   **that** channel. `My Channels` lists it.
3. `My Channels` → the new **🗑 Disconnect** button → confirm → the channel
   disappears, the default falls back, and uploads to it fail with a clear
   "connect a channel" message.
4. **Paste fallback:** tap *Paste Code Manually* → *Open Google Auth* → consent →
   page fails to load → copy the address-bar URL → paste it in the chat →
   "✅ YouTube connected". (The fresh link is deliberately *not* stored in
   `oauth_states`, so the server callback can't consume the code first.)
5. **Expired code:** wait >60 s before pasting → the bot says the code expired
   and offers *Try Again* (not a raw `400 Bad Request`).
6. **Multi-user upload:** queue a video from each account; each lands on its own
   channel.
7. **Revoked token:** at <https://myaccount.google.com/permissions> remove the
   app's access, then queue an upload → the job fails with "YouTube access was
   revoked or expired. Tap Connect YouTube to link the channel again."
8. **Token refresh:** after ≥1 h, queue an upload without reconnecting — the bot
   refreshes silently (server log shows no new consent prompt).
9. `/status` and the job list still work; `channels=0` now only appears when
   nobody has connected a channel.
