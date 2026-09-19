"""
🎬 YouTube Telegram Bot - Epic Edition
Features: YouTube Upload, File Hosting, Colored Buttons, Security, GitHub Backup
"""

from __future__ import annotations
import base64
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

# ═════════════════════════════════════════════════════════════════
#  AUTO-INSTALL MISSING PACKAGES
# ═════════════════════════════════════════════════════════════════

_REQUIRED_PKGS = [
    ("telebot", "pyTelegramBotAPI"),
    ("requests", "requests"),
    ("cryptography.fernet", "cryptography"),
    ("flask", "flask"),
    ("psutil", "psutil"),
    ("PIL", "Pillow"),
    ("googleapiclient", "google-api-python-client"),
    ("google_auth_oauthlib", "google-auth-oauthlib"),
    ("yt_dlp", "yt-dlp"),
]


def _auto_install_missing() -> None:
    # Skipped in CI/tests and in images that manage their own dependencies.
    if (os.environ.get("SKIP_AUTO_INSTALL") or "").strip().lower() in {"1", "true", "yes", "on"}:
        return
    missing: List[str] = []
    for mod, pip_name in _REQUIRED_PKGS:
        try:
            __import__(mod)
        except ImportError:
            missing.append(pip_name)
    if not missing:
        return
    print(f"[setup] installing missing packages: {', '.join(missing)}")
    strategies = [
        [sys.executable, "-m", "pip", "install", "--upgrade", "--quiet", *missing],
        [sys.executable, "-m", "pip", "install", "--upgrade", "--quiet",
         "--break-system-packages", *missing],
    ]
    for cmd in strategies:
        try:
            subprocess.run(cmd, check=True)
            print("[setup] install ok")
            return
        except Exception:
            continue
    sys.exit(f"[x] auto-install failed. Run: pip install {' '.join(missing)}")


_auto_install_missing()

import telebot
from telebot import types
from telebot.apihelper import ApiTelegramException
import requests as req_lib
from cryptography.fernet import Fernet
from flask import Flask, request, send_file
from html import escape
from threading import Thread

try:
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    from googleapiclient.errors import HttpError
    from google_auth_oauthlib.flow import Flow
    _YT_OK = True
except ImportError:
    _YT_OK = False

try:
    from PIL import Image, ImageDraw, ImageFont
    _PIL_OK = True
except Exception:
    _PIL_OK = False

try:
    import psutil
except ImportError:
    psutil = None

try:
    import yt_dlp  # noqa: F401  (used lazily inside the download helpers)
    _YTDLP_OK = True
except ImportError:
    _YTDLP_OK = False

# ═════════════════════════════════════════════════════════════════
#  COLORED BUTTON CLASS (Bot API 9.4+)
# ═════════════════════════════════════════════════════════════════


class Btn(types.InlineKeyboardButton):
    """InlineKeyboardButton with optional style support (Bot API 9.4+).
    style="primary" = Blue | style="success" = Green | style="danger" = Red
    """
    def __init__(self, *args, style: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        if style:
            self.style = style

    def to_dict(self):
        d = super().to_dict()
        if getattr(self, "style", ""):
            d["style"] = self.style
        return d


# ═════════════════════════════════════════════════════════════════
#  .env LOADER (stdlib only - no python-dotenv dependency)
# ═════════════════════════════════════════════════════════════════


# Keys that came out of the .env file instead of the real process
# environment, and keys the hosting platform set itself.
DOTENV_VALUES: Dict[str, str] = {}
PROCESS_ENV_KEYS: set = set()
DOTENV_LOADED = False


def _load_dotenv(path: Optional[Path] = None) -> None:
    """Load KEY=VALUE pairs from a .env file into os.environ.

    Real environment variables win by default, so shell exports and
    container/platform variables override whatever is in the file. Set
    PREFER_ENV_FILE=1 (or ENV_FILE_WINS=1) to flip that around when a host
    has a stale BOT_TOKEN pinned in its dashboard.
    """
    global DOTENV_LOADED
    env_path = path or (Path(__file__).resolve().parent / ".env")
    if not env_path.is_file():
        return
    file_wins = (os.environ.get("PREFER_ENV_FILE") or os.environ.get("ENV_FILE_WINS") or "").strip().lower() in {
        "1", "true", "yes", "on"
    }
    try:
        for raw in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):].lstrip()
            key, _, value = line.partition("=")
            key = key.strip()
            if not key:
                continue
            value = value.strip()
            # Strip a single layer of matching quotes
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if key in os.environ and not file_wins:
                # Host/platform value is in charge - remember it for /diag.
                PROCESS_ENV_KEYS.add(key)
                continue
            os.environ[key] = value
            DOTENV_VALUES[key] = value
        DOTENV_LOADED = True
        print(f"[setup] loaded config from {env_path.name}"
              f"{' (file values override the environment)' if file_wins else ''}")
    except OSError as exc:
        print(f"[setup] could not read {env_path}: {exc}")


_load_dotenv()


def value_source(key: str) -> str:
    """Human-readable description of where a config value came from."""
    if key in DOTENV_VALUES:
        return ".env file"
    if key in PROCESS_ENV_KEYS:
        return "hosting environment (overrides .env)"
    if os.environ.get(key):
        return "process environment"
    return "not set"


def mask_secret(value: str, keep_tail: int = 4) -> str:
    """Mask a secret for safe display in chat or logs."""
    if not value:
        return "(empty)"
    tail = value[-keep_tail:] if len(value) > keep_tail else ""
    bot_id = value.split(":", 1)[0] if ":" in value else ""
    return f"{bot_id}:…{tail}" if bot_id else f"…{tail}"

# ═════════════════════════════════════════════════════════════════
#  CONFIGURATION
# ═════════════════════════════════════════════════════════════════

BASE_DIR = Path(__file__).resolve().parent

# Where the SQLite database, downloads and cookies live.
#
# ⚠️ Hosting note (Railway / Render / Heroku): the container filesystem is
# WIPED on every deploy, which silently deletes bot_data.db — and with it every
# connected YouTube channel. That is a very common cause of "channels=0" right
# after a redeploy. Attach a persistent volume and point this at it:
#     STORAGE_DIR=/data        # volume mounted on /data
# Railway also exports RAILWAY_VOLUME_MOUNT_PATH, which is picked up below.
STORAGE_DIR = Path(
    os.environ.get("STORAGE_DIR")
    or os.environ.get("DATA_DIR")
    or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
    or (BASE_DIR / "storage")
)
DB_FILE = STORAGE_DIR / "bot_data.db"

STORAGE_DIR.mkdir(parents=True, exist_ok=True)
(STORAGE_DIR / "uploads").mkdir(exist_ok=True)
(STORAGE_DIR / "youtube").mkdir(exist_ok=True)
(STORAGE_DIR / "logs").mkdir(exist_ok=True)
(STORAGE_DIR / "social").mkdir(exist_ok=True)


def _hosting_env() -> str:
    """Name of the platform wiping the filesystem on deploy, or ""."""
    if os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_PROJECT_ID"):
        return "Railway"
    if os.environ.get("RENDER"):
        return "Render"
    if os.environ.get("DYNO"):
        return "Heroku"
    return ""


def storage_volume_name(storage_dir=None, mounts_file: str = "/proc/mounts") -> str:
    """Volume backing `storage_dir`, or "" when that path is ephemeral.

    Two independent signals, because either one alone gives false answers:
    Railway exports RAILWAY_VOLUME_MOUNT_PATH (plus NAME) once a volume is
    attached, and on Linux any real mount is listed in /proc/mounts.

    A "" here is the difference between the bot keeping its channels and coming
    back as channels=0: STORAGE_DIR holds BOTH bot_data.db and secret.key, so an
    ephemeral path loses the channels and makes every stored OAuth token
    undecryptable at the same time.
    """
    try:
        target = Path(storage_dir or STORAGE_DIR).resolve()
    except OSError:
        return ""
    name = (os.environ.get("RAILWAY_VOLUME_NAME") or "").strip()

    # NB: test the raw string, never Path(it). Path("") is ".", which quietly
    # matched the current working directory and made every local run claim it
    # had a volume.
    mount_env = (os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or "").strip()
    if mount_env:
        try:
            attached = Path(mount_env).resolve()
        except OSError:
            attached = None
        if attached is not None and (target == attached or attached in target.parents):
            return name or mount_env

    try:
        lines = Path(mounts_file).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    for line in lines:
        parts = line.split()
        if len(parts) < 2:
            continue
        point = parts[1].replace("\\040", " ")
        if point == "/":
            continue  # the container root is not persistence
        try:
            point_path = Path(point).resolve()
        except OSError:
            continue
        if target == point_path or point_path in target.parents:
            return name or str(point_path)
    return ""


def storage_report_lines(storage_dir=None, mounts_file: str = "/proc/mounts") -> List[str]:
    """Startup lines saying whether STORAGE_DIR survives a redeploy."""
    volume = storage_volume_name(storage_dir, mounts_file)
    target = Path(storage_dir or STORAGE_DIR)
    if volume:
        return [f"    💾 Persistent volume: {volume} — {target} survives redeploys"]

    host = _hosting_env()
    lines = [f"    ⚠️  NOT persistent — {target} is not on a volume."]
    if host:
        lines.append(f"        {host} wipes the filesystem on every deploy, so")
        lines.append("        bot_data.db + secret.key are lost: channels come back")
        lines.append("        as channels=0 and stored tokens stop decrypting.")
    else:
        lines.append("        Local run — no host volume in use.")
    attached = (os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or "").strip()
    if attached:
        lines.append(f"        A volume IS mounted at {attached} — set STORAGE_DIR={attached}")
    elif host:
        lines.append("        Attach a volume mounted at /data, then set STORAGE_DIR=/data")
    return lines


def oauth_config_lines() -> List[str]:
    """Startup lines for the OAuth settings Google will reject.

    "Connect YouTube" failing with a bare 400 is close to invisible from
    Telegram, and every one of its causes is visible right here in the process
    environment - so say it in the deploy log instead of making the user guess.
    """
    if not (YT_CLIENT_ID and YT_CLIENT_SECRET):
        missing = ", ".join(name for name, value in (
            ("YOUTUBE_CLIENT_ID", YT_CLIENT_ID),
            ("YOUTUBE_CLIENT_SECRET", YT_CLIENT_SECRET)) if not value)
        return [f"🔗 OAuth redirect URI: {YT_REDIRECT_URI or '(unset)'}",
                f"❌ {missing} missing — Connect YouTube cannot work."]

    host = _hosting_env()
    if host and YT_REDIRECT_URI.startswith(("http://localhost", "http://127.0.0.1")):
        return [f"🔗 OAuth redirect URI: {YT_REDIRECT_URI}",
                "⚠️  That points at this container's own machine, so Google will send",
                f"    the browser nowhere. On {host}, set YOUTUBE_REDIRECT_URI to the",
                "    public HTTPS URL from Settings → Networking, then register the very",
                "    same value in Google Cloud → Credentials → Authorized redirect URIs."]

    return [f"🔗 OAuth redirect URI: {YT_REDIRECT_URI}",
            "    Register exactly this in Google Cloud → Credentials → Authorized",
            "    redirect URIs, or Google answers HTTP 400 on the token call."]


def _code_fingerprint(path=None) -> str:
    """Short digest of the running bot.py.

    The only dependable way to tell "my fix is not working" from "my fix is not
    deployed": compare this with the same digest computed on the repo copy.
    """
    try:
        data = Path(path or __file__).read_bytes()
    except OSError:
        return "unknown"
    return hashlib.sha256(data).hexdigest()[:12]


def build_marker() -> str:
    """Everything needed to identify the running code, on one line."""
    try:
        stat = Path(__file__).stat()
        written = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
        size = stat.st_size
    except OSError:
        written, size = "unknown", 0
    return (f"{_code_fingerprint()} · bot.py {size}b · written {written} · "
            f"up {int((time.time() - START_TIME) // 60)}m")


def oauth_self_check_html(check_reachability: bool = True) -> str:
    """Explain why "Connect YouTube" fails, by asking Google instead of guessing.

    Google answers four unrelated misconfigurations with an identical HTTP 400,
    so one live token call with a throwaway code is worth more than any amount
    of reading configuration: the error code it returns is unambiguous.
    """
    lines = ["🔍 <b>YouTube OAuth self-check</b>", ""]
    problems = [0]

    def good(text: str) -> None:
        lines.append(f"✅ {text}")

    def bad(text: str) -> None:
        problems[0] += 1
        lines.append(f"❌ {text}")

    def warn(text: str) -> None:
        lines.append(f"⚠️ {text}")

    missing = [name for name, value in (("YOUTUBE_CLIENT_ID", YT_CLIENT_ID),
                                        ("YOUTUBE_CLIENT_SECRET", YT_CLIENT_SECRET))
               if not value]
    if missing:
        bad(f"{' and '.join(missing)} not set on this host.")
    else:
        # The client id is public (it is in every auth URL); only the secret is not.
        good(f"Client <code>{escape(YT_CLIENT_ID)}</code> and its secret are set.")

    lines.append(f"🔗 Redirect URI: <code>{escape(YT_REDIRECT_URI or 'not set')}</code>")
    host = _hosting_env()
    if not YT_REDIRECT_URI:
        bad("YOUTUBE_REDIRECT_URI is not set, so Google has nowhere to send you back.")
    elif host and YT_REDIRECT_URI.startswith(("http://localhost", "http://127.0.0.1")):
        bad(f"On {host} that points back into its own container. Use the public HTTPS "
            "domain from Settings → Networking.")
    else:
        good("That is a public URL Google can redirect the browser back to.")

    if check_reachability and PUBLIC_BASE_URL:
        try:
            resp = req_lib.get(f"{PUBLIC_BASE_URL}/health", timeout=10)
            if resp.status_code == 200 and resp.text.strip() == "alive":
                good(f"This host answers its own callback at "
                     f"<code>{escape(PUBLIC_BASE_URL)}/health</code>.")
            else:
                warn(f"<code>{escape(PUBLIC_BASE_URL)}/health</code> replied HTTP "
                     f"{resp.status_code} — the automatic redirect may not land.")
        except Exception as exc:
            warn(f"Could not reach <code>{escape(PUBLIC_BASE_URL)}</code> "
                 f"({escape(str(exc)[:80])}) — use Paste Code Manually instead.")


    if missing or not (_YT_OK and yt_service):
        warn("Skipped the live Google check: it needs the client credentials and the "
             "YouTube libraries.")
    else:
        probe = yt_service.probe_client_config()
        if probe is None or probe.error == "invalid_grant":
            good("Google accepted this client id, secret and redirect URI "
                 "(verified live with a throwaway code).")
        elif probe.error == "redirect_uri_mismatch":
            bad("Google does not have that redirect URI registered for this client. "
                "Add it byte for byte under Google Cloud → Credentials → your OAuth "
                "client → Authorized redirect URIs.")
        elif probe.error == "invalid_client":
            bad("Google rejected the client id / secret pair. Both must come from the "
                "same <b>Web application</b> client, with no trailing spaces.")
        elif probe.error == "unauthorized_client":
            bad("This client may not use this flow: set its type to <b>Web "
                "application</b> and add the YouTube scopes on the consent screen.")
        else:
            bad(f"Google answered <code>{escape(probe.error)}</code>: "
                f"{escape(probe.description[:200])}")

    lines.append("")
    lines.append("✅ Nothing to fix — tap Connect YouTube." if not problems[0] else
                 f"❌ {problems[0]} problem(s) above. Fix those, then run /oauthcheck again.")
    return "\n".join(lines)


# Cached result of "can Google's redirect actually reach us?" - probed at most
# once per _CALLBACK_PROBE_TTL so a button press never pays for a network call.
_CALLBACK_PROBE: Dict[str, object] = {}
_CALLBACK_PROBE_TTL = 300


def _probe_callback() -> Tuple[bool, str]:
    """Ask whether the URL Google will redirect to actually answers."""
    uri = YT_REDIRECT_URI or ""
    host = _hosting_env()
    if not uri:
        return False, ("YOUTUBE_REDIRECT_URI is not set, so Google has nowhere to send "
                       "your browser back to.")
    if host and uri.startswith(("http://localhost", "http://127.0.0.1")):
        return False, (f"{uri} points back into this container, so your browser could "
                       f"never reach it from {host}.")
    base = (PUBLIC_BASE_URL or "").rstrip("/")
    if not base:
        return True, ""  # nothing to compare against; assume the redirect lands
    try:
        resp = req_lib.get(f"{base}/health", timeout=8)
    except Exception as exc:
        return False, f"{base} did not answer ({str(exc)[:80]})."
    if resp.status_code != 200 or resp.text.strip() != "alive":
        return False, f"{base}/health answered HTTP {resp.status_code}, not this bot."
    return True, ""


def oauth_callback_reachable(force: bool = False,
                             ttl: int = _CALLBACK_PROBE_TTL) -> Tuple[bool, str]:
    """(reachable, reason) for the browser redirect, cached for `ttl` seconds."""
    now = time.time()
    checked_at = float(_CALLBACK_PROBE.get("at") or 0)
    if not force and checked_at and now - checked_at < ttl:
        return bool(_CALLBACK_PROBE.get("ok")), str(_CALLBACK_PROBE.get("why") or "")
    ok, why = _probe_callback()
    _CALLBACK_PROBE.update({"at": now, "ok": ok, "why": why})
    return ok, why


def startup_report_html(with_probe: bool = True) -> str:
    """One plain-English message saying whether this deploy can actually work.

    Everything here is also in the logs, but a deploy that silently loses its
    volume or its redirect URI should say so where the user already is.
    """
    lines = ["🎬 <b>Bot started</b>",
             "",
             f"🏷 Build: <code>{escape(build_marker())}</code>"]

    if storage_volume_name():
        lines.append(f"💾 Storage: persistent on <code>{escape(str(STORAGE_DIR))}</code> ✅")
    else:
        lines.append(f"⚠️ Storage: <code>{escape(str(STORAGE_DIR))}</code> is not persistent — "
                     "connected channels are lost on the next deploy.")

    reachable, why = oauth_callback_reachable(force=True)
    if reachable:
        lines.append("🔗 Browser redirect: reaches this bot ✅")
    else:
        lines.append(f"⚠️ Browser redirect: cannot work — {escape(why)}")
        lines.append("   Connect YouTube will offer <b>Paste Code Manually</b> instead.")

    missing = [name for name, value in (("YOUTUBE_CLIENT_ID", YT_CLIENT_ID),
                                        ("YOUTUBE_CLIENT_SECRET", YT_CLIENT_SECRET))
               if not value]
    if missing:
        lines.append(f"❌ YouTube OAuth: {' and '.join(missing)} missing.")
    elif with_probe and _YT_OK and yt_service:
        probe = yt_service.probe_client_config()
        if probe is None or probe.error == "invalid_grant":
            lines.append("🔐 Google accepted the OAuth client id, secret and redirect URI ✅")
        else:
            hint = _oauth_error_hint(probe).strip(" —")
            lines.append(f"❌ Google rejected the OAuth client: "
                         f"<code>{escape(probe.error)}</code> — {escape(hint)}")
    else:
        lines.append("🔐 YouTube OAuth: client id and secret are set ✅")

    lines += ["", "Full check: /oauthcheck · configuration: /diag"]
    return "\n".join(lines)


def announce_startup() -> None:
    """Tell the owner how the deploy looks, without them having to go looking."""
    if not OWNER_ID:
        print("[startup] OWNER_ID unset — skipping the startup report")
        return
    time.sleep(3)  # let polling come up before the first send
    try:
        notify(OWNER_ID, startup_report_html())
        print("[startup] sent the startup report to the owner")
    except Exception as exc:
        print(f"[startup] could not send the startup report: {exc}")

# Bot Token - .env first, then the src/ package name, then plain env.
BOT_TOKEN_ENV_KEY = "BOT_TOKEN" if os.environ.get("BOT_TOKEN") else "TELEGRAM_BOT_TOKEN"
TOKEN = (
    os.environ.get("BOT_TOKEN")
    or os.environ.get("TELEGRAM_BOT_TOKEN")
    or ""
).strip()

# Safety net: if the hosting platform pinned a malformed BOT_TOKEN that would
# override a perfectly good .env value, fall back to the .env one instead of
# exiting. A broken host variable should not take the bot offline.
if ":" not in TOKEN and ":" in (DOTENV_VALUES.get("BOT_TOKEN") or ""):
    TOKEN = DOTENV_VALUES["BOT_TOKEN"]
    BOT_TOKEN_ENV_KEY = "BOT_TOKEN"
    print("[setup] host BOT_TOKEN looked invalid - using the .env token instead")

# No hardcoded fallback on purpose: baking a personal Telegram id in here
# would silently grant owner rights to anyone who runs this file without
# setting OWNER_ID. Set it in .env instead.
try:
    OWNER_ID = int(os.environ.get("OWNER_ID") or 0)
except (TypeError, ValueError):
    OWNER_ID = 0

# YouTube OAuth.
# .strip() is not cosmetic: secrets pasted into Railway / Render / Heroku
# dashboards very often carry a trailing newline or space. One stray character
# makes Google answer EVERY token request with 400 invalid_client or
# redirect_uri_mismatch, which looks exactly like a code problem.
YT_CLIENT_ID = os.environ.get("YOUTUBE_CLIENT_ID", "").strip()
YT_CLIENT_SECRET = os.environ.get("YOUTUBE_CLIENT_SECRET", "").strip()
YT_REDIRECT_URI = os.environ.get(
    "YOUTUBE_REDIRECT_URI", "http://localhost:8000/oauth/callback").strip()
YT_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",    # insert videos
    "https://www.googleapis.com/auth/youtube",           # manage the channel
    "https://www.googleapis.com/auth/youtube.readonly",  # list channels + stats
]

# The HTTP port for the built-in Flask app (keep-alive + OAuth callback).
# Most hosts inject PORT themselves and their value wins.
def _resolve_port() -> int:
    """PORT, ignoring values hosts sometimes export as empty or 0."""
    try:
        port = int(os.environ.get("PORT") or 0)
    except (TypeError, ValueError):
        port = 0
    return port if 0 < port < 65536 else 8080


HTTP_PORT = _resolve_port()


def _normalize_redirect(uri: str, port: int) -> str:
    """Keep the localhost redirect URI in sync with the real listen port.

    Google rejects the callback when the URI does not match the one listed in
    the Cloud console, and a leftover :8000 default next to PORT=8080 is an
    easy way to break the whole OAuth flow.
    """
    if not uri:
        return uri
    match = re.match(r'^(https?://(?:localhost|127\.0\.0\.1))(?::(\d+))?(/.*)?$', uri)
    if not match:
        return uri
    path = match.group(3) or "/oauth/callback"
    return f"{match.group(1)}:{port}{path}"


YT_REDIRECT_URI = _normalize_redirect(YT_REDIRECT_URI, HTTP_PORT)

# Public URL of this process (used to build the OAuth redirect the user has
# to whitelist in Google Cloud). Hosts normally expose it as PUBLIC_URL or
# RENDER_EXTERNAL_URL; fall back to the redirect URI's origin.
PUBLIC_BASE_URL = (
    os.environ.get("PUBLIC_BASE_URL")
    or os.environ.get("PUBLIC_URL")
    or os.environ.get("RENDER_EXTERNAL_URL")
    or os.environ.get("APP_BASE_URL")
    or ""
).rstrip("/")
if not PUBLIC_BASE_URL and "://" in YT_REDIRECT_URI:
    # e.g. https://mybot.onrender.com/oauth/youtube/callback -> https://mybot.onrender.com
    PUBLIC_BASE_URL = YT_REDIRECT_URI.split("/oauth", 1)[0].rstrip("/")

# ── Social downloads (yt-dlp) ──────────────────────────────────────
# Optional Netscape-format cookies file: TikTok blocks logged-out scraping
# from data-center IPs, so a cookies export usually makes it work.
SOCIAL_COOKIES_FILE = (
    os.environ.get("SOCIAL_COOKIES_FILE")
    or os.environ.get("COOKIES_FILE")
    or str(STORAGE_DIR / "cookies.txt")
)
SOCIAL_MAX_DURATION = int(os.environ.get("SOCIAL_MAX_DURATION", 600))
SOCIAL_MAX_HEIGHT = int(os.environ.get("SOCIAL_MAX_HEIGHT", 1080))

# TikTok -> YouTube auto-posting
TIKTOK_POLL_SECONDS = int(os.environ.get("TIKTOK_POLL_SECONDS", 900))
TIKTOK_MAX_PER_RUN = int(os.environ.get("TIKTOK_MAX_PER_RUN", 3))

# Background loops
SCHEDULER_TICK_SECONDS = int(os.environ.get("SCHEDULER_TICK_SECONDS", 30))
MAX_JOB_RETRIES = int(os.environ.get("MAX_JOB_RETRIES", 3))

# Brand
BRAND = "YouTube Bot"
SUPPORT_USR = "@animatrixxgamer"
UPDATE_CH = "https://t.me/animatrixxhub"

# File limits
FREE_LIMIT = 10
PREMIUM_LIMIT = 15
ADMIN_LIMIT = 999
MAX_FILE_SIZE = 50 * 1024 * 1024
# Telegram bots may download at most 20 MB through getFile().
TG_DOWNLOAD_LIMIT = 20 * 1024 * 1024

if not TOKEN or ":" not in TOKEN:
    sys.exit("BOT_TOKEN missing or invalid. Set BOT_TOKEN env var.")

# ═════════════════════════════════════════════════════════════════
#  GLYPH ICONS
# ═════════════════════════════════════════════════════════════════

G = {
    "ok": "✓", "no": "✘", "warn": "⚠", "arrow": "→", "bullet": "•",
    "tri": "▸", "diamond": "◆", "star": "★", "spark": "✦", "back": "↲",
    "fwd": "▶", "plus": "⊕", "play": "‣", "stop": "■", "refresh": "↻",
    "lock": "▣", "key": "❖", "shield": "◇", "eye": "◉", "user": "◈",
    "crown": "♔", "wallet": "◆", "bolt": "⚡", "settings": "⚙",
    "upload": "▴", "download": "▾", "folder": "▸", "trash": "✖",
    "ban": "⚔", "chat": "▫", "broadcast": "⚑", "graph": "▪",
    "clock": "⏱", "calendar": "🗓", "social": "📥", "tiktok": "🎵",
    "queue": "📋", "doc": "📄", "world": "🌐", "diag": "🧪",
    "div": "━" * 16,
}

# ═════════════════════════════════════════════════════════════════
#  DATABASE (SQLite)
# ═════════════════════════════════════════════════════════════════

DB_LOCK = threading.Lock()


def init_db():
    conn = sqlite3.connect(str(DB_FILE), check_same_thread=False)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY, username TEXT, status TEXT DEFAULT 'free',
                  plan_expiry TEXT, created_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS admins
                 (user_id INTEGER PRIMARY KEY)''')
    c.execute('''CREATE TABLE IF NOT EXISTS user_files
                 (user_id INTEGER, file_name TEXT, file_type TEXT,
                  PRIMARY KEY (user_id, file_name))''')
    c.execute('''CREATE TABLE IF NOT EXISTS youtube_channels
                 (user_id INTEGER, channel_id TEXT, channel_name TEXT,
                  access_token TEXT, refresh_token TEXT, token_expiry TEXT,
                  status TEXT DEFAULT 'connected', last_error TEXT,
                  PRIMARY KEY (user_id, channel_id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS upload_jobs
                 (job_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                  channel_id TEXT, video_id TEXT, title TEXT, description TEXT,
                  tags TEXT, privacy_status TEXT DEFAULT 'private',
                  status TEXT DEFAULT 'pending', error_msg TEXT,
                  created_at TEXT, completed_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS audit_log
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                  action TEXT, detail TEXT, timestamp TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS settings
                 (key TEXT PRIMARY KEY, value TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS oauth_states
                 (state TEXT PRIMARY KEY, user_id INTEGER, chat_id INTEGER,
                  created_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS tiktok_sources
                 (source_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                  handle TEXT, profile_url TEXT, channel_id TEXT,
                  privacy_status TEXT DEFAULT 'private', enabled INTEGER DEFAULT 1,
                  interval_seconds INTEGER, max_per_run INTEGER DEFAULT 1,
                  stagger_minutes INTEGER DEFAULT 0, title_template TEXT,
                  last_check TEXT, last_status TEXT, created_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS tiktok_seen
                 (source_id INTEGER, video_id TEXT, seen_at TEXT,
                  PRIMARY KEY (source_id, video_id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS downloads
                 (download_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                  name TEXT, path TEXT, size INTEGER, source_url TEXT,
                  kind TEXT DEFAULT 'link', status TEXT DEFAULT 'ready',
                  created_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS download_tokens
                 (token TEXT PRIMARY KEY, path TEXT, user_id INTEGER,
                  created_at TEXT, expires_at TEXT)''')

    # Migrations for databases created by earlier versions of this file.
    _ensure_columns(conn, "users", {
        "banned": "INTEGER DEFAULT 0",
        "default_privacy": "TEXT DEFAULT 'private'",
        "last_channel_id": "TEXT",
        "last_tags": "TEXT",
        "last_description": "TEXT",
        "plan": "TEXT DEFAULT 'free'",
        "uploads_used": "INTEGER DEFAULT 0",
        "uploads_reset_at": "TEXT",
    })
    c.execute('''CREATE TABLE IF NOT EXISTS force_join_channels
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id TEXT UNIQUE,
                  title TEXT, username TEXT, added_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS plans
                 (plan_name TEXT PRIMARY KEY, display_name TEXT,
                  max_uploads_daily INTEGER DEFAULT 10, max_file_size INTEGER DEFAULT 50,
                  features TEXT, price TEXT DEFAULT 'Free', enabled INTEGER DEFAULT 1)''')
    _ensure_columns(conn, "upload_jobs", {
        "thumb_path": "TEXT",
        "file_path": "TEXT",
        "source": "TEXT DEFAULT 'telegram'",
        "source_url": "TEXT",
        "scheduled_at": "TEXT",
        "publish_at": "TEXT",
        "retries": "INTEGER DEFAULT 0",
        "progress": "INTEGER DEFAULT 0",
        "notified": "INTEGER DEFAULT 0",
        "duration": "INTEGER",
    })
    _ensure_columns(conn, "youtube_channels", {
        "thumbnail": "TEXT",
        "connected_at": "TEXT",
        "status": "TEXT DEFAULT 'connected'",
        "last_error": "TEXT",
    })

    if OWNER_ID:
        c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (OWNER_ID,))
    conn.commit()
    conn.close()


def _ensure_columns(conn, table: str, columns: Dict[str, str]) -> None:
    """Add any missing columns to an existing table (cheap SQLite migration)."""
    try:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return
    for name, decl in columns.items():
        if name in existing:
            continue
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
        except sqlite3.Error as exc:  # pragma: no cover - depends on old DB state
            print(f"[db] could not add {table}.{name}: {exc}")


def db_query(query, params=(), fetch=False):
    with DB_LOCK:
        conn = sqlite3.connect(str(DB_FILE), check_same_thread=False)
        c = conn.cursor()
        c.execute(query, params)
        result = c.fetchall() if fetch else None
        conn.commit()
        conn.close()
        return result


def db_insert(query, params=()) -> int:
    """Run an INSERT and return the new row id."""
    with DB_LOCK:
        conn = sqlite3.connect(str(DB_FILE), check_same_thread=False)
        c = conn.cursor()
        c.execute(query, params)
        row_id = c.lastrowid
        conn.commit()
        conn.close()
        return int(row_id or 0)


def get_setting(key: str, default: str = "") -> str:
    row = db_query('SELECT value FROM settings WHERE key = ?', (key,), fetch=True)
    return row[0][0] if row else default


def set_setting(key: str, value: str) -> None:
    db_query('INSERT INTO settings (key, value) VALUES (?, ?) '
             'ON CONFLICT(key) DO UPDATE SET value = excluded.value',
             (key, str(value)))


def is_banned(uid: int) -> bool:
    row = db_query('SELECT banned FROM users WHERE user_id = ?', (uid,), fetch=True)
    return bool(row and row[0][0])


def audit(user_id: int, action: str, detail: str = ""):
    db_query(
        'INSERT INTO audit_log (user_id, action, detail, timestamp) VALUES (?, ?, ?, ?)',
        (user_id, action, detail, datetime.utcnow().isoformat())
    )


def is_admin(uid: int) -> bool:
    # Guard on OWNER_ID truthiness: when it is unset (0) a uid of 0 must not
    # be treated as the owner.
    if OWNER_ID and uid == OWNER_ID:
        return True
    result = db_query('SELECT 1 FROM admins WHERE user_id = ?', (uid,), fetch=True)
    return bool(result)


def get_user_status(uid: int) -> str:
    if OWNER_ID and uid == OWNER_ID:
        return "owner"
    if is_admin(uid):
        return "admin"
    result = db_query('SELECT plan_expiry FROM users WHERE user_id = ?', (uid,), fetch=True)
    if result and result[0][0]:
        try:
            expiry = datetime.fromisoformat(result[0][0])
            if expiry > datetime.utcnow():
                return "premium"
        except Exception:
            pass
    return "free"


def _setting_int(key: str, fallback: int) -> int:
    try:
        return int(get_setting(key, str(fallback)))
    except (TypeError, ValueError):
        return fallback


def get_file_limit(uid: int) -> int:
    status = get_user_status(uid)
    if status == "owner":
        return 999999
    if status == "admin":
        return ADMIN_LIMIT
    if status == "premium":
        return _setting_int("premium_file_limit", PREMIUM_LIMIT)
    return _setting_int("free_file_limit", FREE_LIMIT)


def get_file_count(uid: int) -> int:
    result = db_query('SELECT COUNT(*) FROM user_files WHERE user_id = ?', (uid,), fetch=True)
    return result[0][0] if result else 0


# ── User helpers ────────────────────────────────────────────────────

def ensure_user(uid: int, username: str = "") -> None:
    db_query('INSERT OR IGNORE INTO users (user_id, username, status, created_at) '
             'VALUES (?, ?, ?, ?)',
             (uid, username, "free", datetime.utcnow().isoformat()))
    if username:
        db_query('UPDATE users SET username = ? WHERE user_id = ?', (username, uid))


# -- User metadata defaults ------------------------------------------------

def get_user_defaults(uid: int) -> dict:
    """Get saved default description and tags for a user."""
    row = db_query('SELECT last_description, last_tags FROM users WHERE user_id = ?', (uid,), fetch=True)
    if not row:
        return {"description": "", "tags": ""}
    return {"description": row[0][0] or "", "tags": row[0][1] or ""}


def save_user_defaults(uid: int, description: str = None, tags: str = None) -> None:
    """Save default description and/or tags for a user."""
    if description is not None:
        db_query('UPDATE users SET last_description = ? WHERE user_id = ?', (description[:4800], uid))
    if tags is not None:
        db_query('UPDATE users SET last_tags = ? WHERE user_id = ?', (tags[:450], uid))


def extract_hashtags(text: str) -> str:
    """Extract #hashtags from text (e.g. TikTok description) and return as comma-separated tags."""
    if not text:
        return ""
    tags = re.findall(r'#([\w]+)', text)
    skip = {"fyp", "foryou", "foryoupage", "viral", "tiktok", "comedy", "funny",
            "music", "duet", "stitch", "greenscreen", "reaction"}
    filtered = [t for t in tags if t.lower() not in skip and len(t) > 1]
    return ",".join(filtered[:15])


def suggest_tags(title: str) -> str:
    """Generate trending-suggest tags based on the video title."""
    if not title:
        return "shorts,viral,trending"
    title_lower = title.lower()
    tags = ["shorts", "viral", "trending"]

    music_kw = ["music", "song", "sing", "dance", "rap", "beat", "remix", "audio", "sound"]
    gaming_kw = ["gaming", "game", "play", "minecraft", "fortnite", "valorant", "cod", "gta"]
    tech_kw = ["tech", "coding", "programming", "ai", "python", "javascript", "hack", "tutorial"]
    food_kw = ["cooking", "recipe", "food", "chef", "bake", "eat", "restaurant", "yummy"]
    fitness_kw = ["fitness", "workout", "gym", "exercise", "health", "muscle", "run", "sport"]
    beauty_kw = ["beauty", "makeup", "skincare", "fashion", "style", "glow", "outfit"]
    pet_kw = ["cat", "dog", "pet", "animal", "cute", "funny", "puppy", "kitten"]
    travel_kw = ["travel", "trip", "vacation", "beach", "mountain", "explore", "adventure"]

    keyword_groups = [
        (music_kw, ["music", "song", "dance"]),
        (gaming_kw, ["gaming", "gamer", "gameplay"]),
        (tech_kw, ["tech", "coding", "tutorial"]),
        (food_kw, ["food", "recipe", "cooking"]),
        (fitness_kw, ["fitness", "workout", "gym"]),
        (beauty_kw, ["beauty", "makeup", "fashion"]),
        (pet_kw, ["pets", "animals", "cute"]),
        (travel_kw, ["travel", "adventure", "explore"]),
    ]

    for keywords, group_tags in keyword_groups:
        if any(kw in title_lower for kw in keywords):
            tags.extend(group_tags)
            break

    tags.append("youtube")
    return ",".join(tags[:12])



def list_users(limit: int = 10, offset: int = 0, query: str = "") -> List[tuple]:
    if query:
        like = f"%{query}%"
        return db_query(
            'SELECT user_id, username, COALESCE(banned, 0), created_at FROM users '
            'WHERE CAST(user_id AS TEXT) LIKE ? OR username LIKE ? '
            'ORDER BY created_at DESC LIMIT ? OFFSET ?',
            (like, like, limit, offset), fetch=True) or []
    return db_query(
        'SELECT user_id, username, COALESCE(banned, 0), created_at FROM users '
        'ORDER BY created_at DESC LIMIT ? OFFSET ?', (limit, offset), fetch=True) or []


def count_users() -> int:
    row = db_query('SELECT COUNT(*) FROM users', fetch=True)
    return row[0][0] if row else 0


def set_banned(uid: int, banned: bool) -> None:
    # Upsert: banning someone who never pressed /start must still stick.
    ensure_user(uid)
    db_query('UPDATE users SET banned = ? WHERE user_id = ?', (1 if banned else 0, uid))


def set_premium(uid: int, days: int = 30) -> None:
    ensure_user(uid)
    expiry = (datetime.utcnow() + timedelta(days=days)).isoformat()
    db_query('UPDATE users SET status = ?, plan_expiry = ? WHERE user_id = ?',
             ("premium", expiry, uid))


# ── Force-join helpers ───────────────────────────────────────────

def force_join_channels() -> List[tuple]:
    """All channels/groups users must join."""
    return db_query('SELECT chat_id, title, username FROM force_join_channels ORDER BY id',
                    fetch=True) or []


def add_force_join_channel(chat_id: str, title: str = "", username: str = "") -> bool:
    """Register a channel/group for force-join."""
    try:
        db_query('INSERT OR IGNORE INTO force_join_channels (chat_id, title, username, added_at) '
                 'VALUES (?, ?, ?, ?)',
                 (str(chat_id), title, username, datetime.utcnow().isoformat()))
        return True
    except Exception:
        return False


def remove_force_join_channel(chat_id: str) -> bool:
    """Remove a force-join requirement."""
    db_query('DELETE FROM force_join_channels WHERE chat_id = ?', (str(chat_id),))
    return True


def check_force_join(uid: int, chat_id: int) -> bool:
    """Check if user has joined all required channels. Returns True if OK."""
    if get_setting("force_join_enabled", "0") != "1":
        return True
    required = force_join_channels()
    if not required:
        return True
    not_joined = []
    for chat_id_str, title, username in required:
        try:
            member = bot.get_chat_member(chat_id_str, uid)
            if member.status in ("left", "kicked"):
                not_joined.append((chat_id_str, title, username))
        except Exception:
            # Can't check — bot not in that chat or API error
            not_joined.append((chat_id_str, title, username))
    if not not_joined:
        return True
    # Build the join prompt
    kb = types.InlineKeyboardMarkup(row_width=1)
    for chat_id_str, title, username in not_joined:
        if username:
            kb.add(types.InlineKeyboardButton(f"📺 Join @{username}", url=f"https://t.me/{username}"))
        else:
            kb.add(types.InlineKeyboardButton(f"📺 Join {title or chat_id_str}",
                                               url=f"https://t.me/c/{str(chat_id_str).replace('-100', '')}"))
    kb.add(Btn(f"{G['refresh']}  I joined — check again", callback_data="force_join_check",
               style="success"))
    kb.add(Btn(f"{G['back']}  Main Menu", callback_data="main_menu", style="danger"))
    names = "\n".join(f"  • {title or username or cid}" for cid, title, username in not_joined)
    safe_send(chat_id,
              f"🔒 <b>Join our community first!</b>\n\n"
              f"You need to join {len(not_joined)} channel(s)/group(s):\n"
              f"{names}\n\n"
              f"After joining, tap <b>I joined — check again</b>.",
              kb)
    return False


# ── Plan helpers ─────────────────────────────────────────────────

DEFAULT_PLANS = {
    "free": {
        "display": "🆓 Free",
        "uploads": 5,
        "size": 50,
        "features": "5 uploads/day, 50MB, 1 channel, basic queue",
        "yt_features": "upload,schedule,queue",
        "max_channels": 1,
        "tiktok_sources": 0,
        "analytics": False,
        "bulk_upload": False,
        "priority_queue": False,
    },
    "basic": {
        "display": "⭐ Basic",
        "uploads": 20,
        "size": 100,
        "features": "20 uploads/day, 100MB, 2 channels, TikTok auto-post",
        "yt_features": "upload,schedule,queue,tiktok",
        "max_channels": 2,
        "tiktok_sources": 1,
        "analytics": False,
        "bulk_upload": False,
        "priority_queue": False,
    },
    "premium": {
        "display": "💎 Premium",
        "uploads": 50,
        "size": 200,
        "features": "50/day, 200MB, 5 channels, analytics, bulk upload",
        "yt_features": "upload,schedule,queue,tiktok,analytics,bulk",
        "max_channels": 5,
        "tiktok_sources": 5,
        "analytics": True,
        "bulk_upload": True,
        "priority_queue": False,
    },
    "vip": {
        "display": "👑 VIP",
        "uploads": 999,
        "size": 500,
        "features": "Unlimited, 500MB, unlimited everything, priority",
        "yt_features": "upload,schedule,queue,tiktok,analytics,bulk,priority",
        "max_channels": 999,
        "tiktok_sources": 999,
        "analytics": True,
        "bulk_upload": True,
        "priority_queue": True,
    },
}


def init_default_plans() -> None:
    """Seed the plans table with defaults if empty."""
    existing = db_query('SELECT COUNT(*) FROM plans', fetch=True)
    if existing and existing[0][0] > 0:
        return
    for name, info in DEFAULT_PLANS.items():
        db_query('INSERT OR IGNORE INTO plans (plan_name, display_name, max_uploads_daily, '
                 'max_file_size, features, price) VALUES (?, ?, ?, ?, ?, ?)',
                 (name, info["display"], info["uploads"], info["size"], info["features"],
                  "Free" if name == "free" else "Paid"))


def get_user_plan(uid: int) -> str:
    """Get the user's plan name."""
    row = db_query('SELECT plan FROM users WHERE user_id = ?', (uid,), fetch=True)
    return (row[0][0] if row and row[0][0] else "free") or "free"


def set_user_plan(uid: int, plan: str) -> None:
    """Assign a plan to a user."""
    ensure_user(uid)
    db_query('UPDATE users SET plan = ? WHERE user_id = ?', (plan, uid))


def get_plan_limits(plan: str) -> dict:
    """Get limits for a plan."""
    row = db_query('SELECT max_uploads_daily, max_file_size, features FROM plans '
                   'WHERE plan_name = ?', (plan,), fetch=True)
    if row:
        return {"uploads": row[0][0], "size": row[0][1], "features": row[0][2]}
    return {"uploads": 5, "size": 50, "features": "Basic"}


def plan_has_feature(uid: int, feature: str) -> bool:
    """Check if user's plan includes a specific feature."""
    if get_setting("plans_enabled", "0") != "1":
        return True
    plan = get_user_plan(uid)
    info = DEFAULT_PLANS.get(plan, DEFAULT_PLANS["free"])
    features = info.get("yt_features", "")
    return feature in features.split(",")


def plan_max_channels(uid: int) -> int:
    """Max YouTube channels for this user's plan."""
    if get_setting("plans_enabled", "0") != "1":
        return 999
    plan = get_user_plan(uid)
    return DEFAULT_PLANS.get(plan, DEFAULT_PLANS["free"]).get("max_channels", 1)


def plan_max_tiktok(uid: int) -> int:
    """Max TikTok sources for this user's plan."""
    if get_setting("plans_enabled", "0") != "1":
        return 999
    plan = get_user_plan(uid)
    return DEFAULT_PLANS.get(plan, DEFAULT_PLANS["free"]).get("tiktok_sources", 0)


def check_upload_limit(uid: int) -> Tuple[bool, str]:
    """Check if user can upload more today. Returns (allowed, message)."""
    if get_setting("plans_enabled", "0") != "1":
        return True, "Plans disabled"
    plan = get_user_plan(uid)
    limits = get_plan_limits(plan)
    max_uploads = limits["uploads"]
    # Count uploads today
    today = datetime.utcnow().strftime("%Y-%m-%d")
    row = db_query('SELECT COUNT(*) FROM upload_jobs WHERE user_id = ? '
                   'AND created_at LIKE ?', (uid, f"{today}%"), fetch=True)
    used = row[0][0] if row else 0
    if used >= max_uploads:
        return False, (f"⚠️ Daily upload limit reached ({used}/{max_uploads})\n"
                       f"Your plan: {plan.title()}\n\n"
                       f"Upgrade your plan for more uploads!")
    return True, f"{used}/{max_uploads} uploads today"


# ── YouTube Auto-Subscribe ───────────────────────────────────────

def get_owner_youtube_channel() -> str:
    """The owner's YouTube channel ID that users should subscribe to."""
    return get_setting("owner_youtube_channel", "")


def set_owner_youtube_channel(channel_id: str) -> None:
    """Set the owner's YouTube channel for auto-subscribe."""
    set_setting("owner_youtube_channel", channel_id)


def auto_subscribe_owner(credentials, user_channel_name: str = "") -> Tuple[bool, str]:
    """Subscribe the connected channel to the owner's YouTube channel.
    
    Returns (success, message). Called after a user connects their channel.
    """
    owner_ch = get_owner_youtube_channel()
    if not owner_ch or not _YT_OK:
        return False, ""
    try:
        youtube = build("youtube", "v3", credentials=credentials)
        # Check if already subscribed
        resp = youtube.subscriptions().list(
            part="snippet",
            forChannelId=owner_ch,
            mine=True,
            maxResults=1).execute()
        if resp.get("items"):
            return True, "Already subscribed"
        # Subscribe
        youtube.subscriptions().insert(
            part="snippet",
            body={
                "snippet": {
                    "resourceId": {
                        "kind": "youtube#channel",
                        "channelId": owner_ch
                    }
                }
            }).execute()
        return True, f"Subscribed to owner's channel"
    except Exception as exc:
        print(f"[auto-subscribe] failed: {exc}")
        return False, str(exc)[:200]


# ── YouTube channel helpers ─────────────────────────────────────────

def user_channels(uid: int) -> List[tuple]:
    return db_query(
        'SELECT channel_id, channel_name, thumbnail, connected_at FROM youtube_channels '
        'WHERE user_id = ? ORDER BY connected_at DESC', (uid,), fetch=True) or []


def channel_row(uid: int, channel_id: str) -> Optional[tuple]:
    rows = db_query(
        'SELECT channel_id, channel_name, access_token, refresh_token, token_expiry '
        'FROM youtube_channels WHERE user_id = ? AND channel_id = ?',
        (uid, channel_id), fetch=True)
    return rows[0] if rows else None


def revoked_notice_html(uid: int, channel_id: str, error: str) -> str:
    """What to tell someone whose channel just stopped working."""
    if error in ("invalid_grant", "unauthorized_client"):
        reason = "you removed the bot's access, or its saved token expired"
    else:
        reason = "the saved credentials are no longer accepted"
    lines = [
        "⚠️ <b>YouTube channel needs reconnecting</b>",
        "",
        f"📺 <code>{escape(channel_title(uid, channel_id))}</code>",
        f"Google stopped accepting its saved token — {escape(reason)}.",
    ]
    if error == "invalid_grant":
        # The single most common cause, and the one a user can actually fix
        # permanently: Testing-mode apps get 7-day refresh tokens.
        lines += ["", "A very common cause is an OAuth app still in <b>Testing</b> mode —",
                  "Google expires those refresh tokens every 7 days. Publishing the app",
                  "(OAuth consent screen → Publish app) stops the weekly reconnect."]
    lines += ["", "Uploads and scheduled jobs for this channel fail until you reconnect it.",
              "", "Tap <b>Reconnect YouTube</b> to fix it now."]
    return "\n".join(lines)


def mark_channel_revoked(uid: int, channel_id: str, error: str) -> bool:
    """Flag a channel as needing a reconnect, and tell its owner exactly once.

    Runs from the token-refresh path, so it would otherwise fire on every single
    upload attempt against a dead grant. Only the connected -> revoked
    transition notifies: after a reconnect the next failure notifies again.

    Returns True when the user was actually told.
    """
    rows = db_query('SELECT status FROM youtube_channels WHERE user_id = ? AND channel_id = ?',
                    (uid, channel_id), fetch=True)
    if not rows:
        return False
    first_time = (rows[0][0] or "") != "revoked"
    db_query('UPDATE youtube_channels SET status = ?, last_error = ? '
             'WHERE user_id = ? AND channel_id = ?',
             ("revoked", error, uid, channel_id))
    ACCESS_TOKEN_CACHE.pop((uid, channel_id), None)
    if not first_time:
        return False
    notify(uid, revoked_notice_html(uid, channel_id, error),
           types.InlineKeyboardMarkup(row_width=1)
           .add(Btn(f"{G['play']}  Reconnect YouTube", callback_data="yt_connect",
                    style="primary")))
    return True


def channel_health_check() -> int:
    """Refresh every connected channel once; return how many are now broken.

    Nothing else would notice a dead grant until an upload was actually tried —
    usually a scheduled one, in the middle of the night. Sweeping on a timer
    turns "my upload failed" into a message days earlier.
    """
    if not (yt_service and _YT_OK):
        return 0
    rows = db_query('SELECT user_id, channel_id FROM youtube_channels '
                    'WHERE status IS NULL OR status != ?', ("revoked",), fetch=True) or []
    broken = 0
    for uid, channel_id in rows:
        try:
            yt_service.credentials_for_channel(int(uid), channel_id)
        except ValueError as exc:
            # credentials_for_channel() already flagged and notified it.
            broken += 1
            print(f"[health] {channel_id} (user {uid}): {exc}")
        except Exception as exc:
            print(f"[health] check failed for {channel_id}: {exc}")
    return broken


def channel_health_loop() -> None:
    """Sweep channel tokens on a timer; set CHANNEL_HEALTH_SECONDS=0 to stop."""
    try:
        interval = int(os.environ.get("CHANNEL_HEALTH_SECONDS", 6 * 3600))
    except (TypeError, ValueError):
        interval = 6 * 3600
    if interval <= 0:
        print("[health] token sweep disabled")
        return
    time.sleep(60)  # let polling settle after a deploy before poking Google
    while True:
        try:
            broken = channel_health_check()
            if broken:
                print(f"[health] {broken} channel(s) need reconnecting")
        except Exception as exc:
            print(f"[health] sweep failed: {exc}")
        time.sleep(interval)


def job_counts_by_status(uid: int = 0) -> Dict[str, int]:
    """Upload counts per status, for one user (or everyone when uid is 0)."""
    sql = "SELECT status, COUNT(*) FROM upload_jobs"
    params: tuple = ()
    if uid:
        sql += " WHERE user_id = ?"
        params = (uid,)
    rows = db_query(sql + " GROUP BY status", params, fetch=True) or []
    return {str(status or "unknown"): int(count or 0) for status, count in rows}


def health_summary_html(uid: int = 0) -> str:
    """One message covering channels, quota, failures and pending work.

    uid 0 reports the whole bot (the owner's view); any other id reports just
    that user's channels and jobs, so /summary works for everyone.
    """
    channel_rows = db_query(
        "SELECT COUNT(*), SUM(CASE WHEN status = 'revoked' THEN 1 ELSE 0 END) "
        "FROM youtube_channels" + (" WHERE user_id = ?" if uid else ""),
        (uid,) if uid else (), fetch=True)
    total_channels = int((channel_rows[0][0] if channel_rows else 0) or 0)
    revoked = int((channel_rows[0][1] if channel_rows else 0) or 0)

    jobs = job_counts_by_status(uid)
    since = (datetime.utcnow() - timedelta(hours=24)).isoformat()
    done_today = int(db_query(
        "SELECT COUNT(*) FROM upload_jobs WHERE status = 'completed' "
        "AND completed_at >= ?" + (" AND user_id = ?" if uid else ""),
        (since, uid) if uid else (since,), fetch=True)[0][0] or 0)
    failed = jobs.get("failed", 0)

    lines = ["🤖 <b>Health summary</b>" if uid else "🤖 <b>Bot health summary</b>", ""]
    if total_channels:
        lines.append(f"📺 Channels: {total_channels - revoked} connected"
                     + (f", <b>{revoked} need reconnecting</b>" if revoked else " ✅"))
    else:
        lines.append("📺 Channels: none connected")
    # Real quota is only visible in the Cloud console, so this is the upload count
    # against the well-known ~1,600 units one video insert costs.
    lines.append(f"📊 Uploads (24h): {done_today} · "
                 f"~{done_today * 1600:,} of 10,000 API units")
    lines.append(f"📥 Queue: {jobs.get('pending', 0)} ready · "
                 f"{jobs.get('scheduled', 0)} scheduled · "
                 f"{jobs.get('uploading', 0)} uploading")
    lines.append(f"❌ Failed: {failed}"
                 + (" — Reconnect YouTube, then it retries" if failed else " ✅"))
    lines.append("")
    lines.append(f"🏷 <code>{escape(_code_fingerprint())}</code> · /diag for detail")
    return "\n".join(lines)


def nightly_summary_loop() -> None:
    """Send the health summary every 24h; HEALTH_SUMMARY_SECONDS=0 disables it.

    The last-sent time lives in the database rather than in memory: a redeploy
    restarts this process, and an in-memory timer would reset every time and
    never reach 24 hours on a project that deploys often.
    """
    try:
        interval = int(os.environ.get("HEALTH_SUMMARY_SECONDS", 24 * 3600))
    except (TypeError, ValueError):
        interval = 24 * 3600
    if interval <= 0 or not OWNER_ID:
        return
    time.sleep(90)  # let the startup report land first
    while True:
        try:
            last = get_setting("last_summary_at")
            due = True
            if last:
                try:
                    due = (datetime.utcnow() - datetime.fromisoformat(last)
                           ).total_seconds() >= interval
                except ValueError:
                    due = True
            if due:
                notify(OWNER_ID, health_summary_html())
                set_setting("last_summary_at", datetime.utcnow().isoformat())
                print("[health] sent the summary to the owner")
        except Exception as exc:
            print(f"[health] summary failed: {exc}")
        time.sleep(min(interval, 1800))


def default_channel_id(uid: int) -> str:
    """The channel the user last picked, else their only/first channel."""
    row = db_query('SELECT last_channel_id FROM users WHERE user_id = ?', (uid,), fetch=True)
    if row and row[0][0]:
        if channel_row(uid, row[0][0]):
            return row[0][0]
    channels = user_channels(uid)
    return channels[0][0] if channels else ""


def set_default_channel(uid: int, channel_id: str) -> None:
    db_query('UPDATE users SET last_channel_id = ? WHERE user_id = ?', (channel_id, uid))


def save_channel_tokens(uid: int, tokens: dict, info: Optional[dict] = None) -> dict:
    """Persist freshly-issued tokens for one user's channel and return its info.

    Shared by BOTH the Flask callback and the manual paste flow so the two can
    never drift apart. ``info`` may be passed in when the caller already fetched
    it, saving a second channels.list round-trip.
    """
    if not yt_service:
        raise ValueError("YouTube API not configured")
    refresh = tokens.get("refresh_token", "") or ""
    expiry = datetime.utcnow() + timedelta(seconds=int(tokens.get("expires_in", 3600)))
    access = tokens.get("access_token", "") or ""
    if info is None:
        credentials = yt_service.build_credentials(access, refresh, expiry)
        info = yt_service.get_channel_info(credentials)

    db_query(
        'INSERT OR REPLACE INTO youtube_channels '
        '(user_id, channel_id, channel_name, access_token, refresh_token, token_expiry, '
        'thumbnail, connected_at, status, last_error) '
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'connected', NULL)",
        (uid, info["channel_id"], info["channel_name"], encrypt_data(access),
         encrypt_data(refresh) if refresh else "",
         expiry.isoformat(), info.get("thumbnail", ""), datetime.utcnow().isoformat()))
    set_default_channel(uid, info["channel_id"])
    audit(uid, "channel_connected", info["channel_id"])
    # Never serve a stale access token for this channel again.
    ACCESS_TOKEN_CACHE[(uid, info["channel_id"])] = (access, expiry)
    # A reconnect is the fix for jobs whose only problem was the dead token, so
    # the fix should also bring that work back rather than leave it failed.
    requeue_jobs_after_reconnect(uid, info["channel_id"])
    return info


# Wording that identifies a failure caused by the channel token rather than by
# the file, the metadata or the quota.
TOKEN_FAILURE_MARKERS = ("revoked", "reconnect", "invalid_grant", "unauthorized_client",
                         "invalid_client", "expired")


def requeue_jobs_after_reconnect(uid: int, channel_id: str) -> int:
    """Revive uploads that failed only because the channel token was dead.

    A grant that dies at 3am takes the job down with it: the worker burns every
    retry against a token Google has already refused, then gives up. Only jobs
    whose error names a token problem are revived - a corrupt file or an
    exhausted quota must stay failed.

    Returns how many jobs went back into the queue.
    """
    is_default = default_channel_id(uid) == channel_id
    rows = db_query(
        'SELECT job_id, channel_id, error_msg, file_path FROM upload_jobs '
        "WHERE user_id = ? AND status IN ('failed', 'scheduled')",
        (uid,), fetch=True) or []
    revived = []
    for job_id, job_channel, error, file_path in rows:
        if job_channel != channel_id and not (job_channel is None and is_default):
            continue
        blob = str(error or "").lower()
        if not any(marker in blob for marker in TOKEN_FAILURE_MARKERS):
            continue
        if file_path and not Path(file_path).exists():
            continue  # the file is gone; retrying would just fail again
        db_query('UPDATE upload_jobs SET status = "pending", retries = 0, '
                 'error_msg = NULL, scheduled_at = NULL WHERE job_id = ?', (job_id,))
        revived.append(int(job_id))

    if revived:
        listed = ", ".join(f"#{job}" for job in revived[:10])
        extra = f"\n…and {len(revived) - 10} more" if len(revived) > 10 else ""
        notify(uid,
               f"🔁 <b>{len(revived)} failed upload(s) requeued</b>\n\n"
               f"The channel is reconnected, so {listed} go back in the queue and "
               f"upload automatically.{extra}",
               types.InlineKeyboardMarkup(row_width=1)
               .add(Btn(f"{G['queue']}  View Queue", callback_data="queue_menu",
                        style="primary")))
    return len(revived)


def disconnect_channel(uid: int, channel_id: str) -> bool:
    """Forget one channel: revoke the token at Google, then delete the row."""
    if not channel_id:
        return False
    row = channel_row(uid, channel_id)
    if not row:
        return False
    refresh = decrypt_data(row[3]) if row[3] else ""
    if refresh and yt_service:
        # Best effort: the local delete is what actually disconnects the user.
        yt_service.revoke_token(refresh)
    db_query('DELETE FROM youtube_channels WHERE user_id = ? AND channel_id = ?',
             (uid, channel_id))
    ACCESS_TOKEN_CACHE.pop((uid, channel_id), None)
    if default_channel_id(uid) == channel_id:
        remaining = user_channels(uid)
        set_default_channel(uid, remaining[0][0] if remaining else "")
    audit(uid, "channel_disconnected", channel_id)
    return True


# ── Upload job helpers ──────────────────────────────────────────────

JOB_COLUMNS = (
    "job_id, user_id, channel_id, video_id, title, description, tags, "
    "privacy_status, status, error_msg, created_at, completed_at, file_path, "
    "source, source_url, scheduled_at, publish_at, retries, progress"
)


def get_job(job_id: int, uid: Optional[int] = None) -> Optional[tuple]:
    if uid is None:
        rows = db_query(f'SELECT {JOB_COLUMNS} FROM upload_jobs WHERE job_id = ?',
                        (job_id,), fetch=True)
    else:
        rows = db_query(f'SELECT {JOB_COLUMNS} FROM upload_jobs WHERE job_id = ? AND user_id = ?',
                        (job_id, uid), fetch=True)
    return rows[0] if rows else None


JOB_INDEX = {name: i for i, name in enumerate(JOB_COLUMNS.split(", "))}


def job_field(job: tuple, name: str, default=None):
    idx = JOB_INDEX.get(name)
    if idx is None or job is None or idx >= len(job):
        return default
    return job[idx] if job[idx] is not None else default


def create_job(uid: int, channel_id: str, title: str, file_path: str = "",
               source: str = "telegram", source_url: str = "",
               scheduled_at: Optional[str] = None, publish_at: Optional[str] = None,
               privacy: str = "private", description: str = "", tags: str = "",
               status: str = "pending") -> int:
    return db_insert(
        'INSERT INTO upload_jobs (user_id, channel_id, title, description, tags, '
        'privacy_status, status, created_at, file_path, source, source_url, '
        'scheduled_at, publish_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        (uid, channel_id, title, description, tags, privacy, status,
         datetime.utcnow().isoformat(), file_path, source, source_url,
         scheduled_at, publish_at))


def user_jobs(uid: int, statuses: Optional[List[str]] = None, limit: int = 50) -> List[tuple]:
    if statuses:
        placeholders = ",".join("?" for _ in statuses)
        return db_query(
            f'SELECT {JOB_COLUMNS} FROM upload_jobs WHERE user_id = ? '
            f'AND status IN ({placeholders}) ORDER BY job_id DESC LIMIT ?',
            (uid, *statuses, limit), fetch=True) or []
    return db_query(
        f'SELECT {JOB_COLUMNS} FROM upload_jobs WHERE user_id = ? '
        f'ORDER BY job_id DESC LIMIT ?', (uid, limit), fetch=True) or []


def due_jobs(now_iso: str, limit: int = 5) -> List[tuple]:
    return db_query(
        f'SELECT {JOB_COLUMNS} FROM upload_jobs WHERE status = "scheduled" '
        'AND scheduled_at IS NOT NULL AND scheduled_at <= ? ORDER BY scheduled_at LIMIT ?',
        (now_iso, limit), fetch=True) or []


def job_status_counts(uid: Optional[int] = None) -> List[tuple]:
    if uid is None:
        return db_query('SELECT status, COUNT(*) FROM upload_jobs GROUP BY status',
                        fetch=True) or []
    return db_query('SELECT status, COUNT(*) FROM upload_jobs WHERE user_id = ? '
                    'GROUP BY status', (uid,), fetch=True) or []


# ── TikTok source helpers ───────────────────────────────────────────

def user_sources(uid: int) -> List[tuple]:
    return db_query(
        'SELECT source_id, handle, channel_id, enabled, max_per_run, stagger_minutes, '
        'last_check, last_status, profile_url, privacy_status, title_template '
        'FROM tiktok_sources WHERE user_id = ? ORDER BY source_id', (uid,), fetch=True) or []


def get_source(source_id: int, uid: Optional[int] = None) -> Optional[tuple]:
    if uid is None:
        rows = db_query('SELECT * FROM tiktok_sources WHERE source_id = ?',
                        (source_id,), fetch=True)
    else:
        rows = db_query('SELECT * FROM tiktok_sources WHERE source_id = ? AND user_id = ?',
                        (source_id, uid), fetch=True)
    return rows[0] if rows else None


def add_source(uid: int, handle: str, profile_url: str, channel_id: str,
               privacy: str = "private", max_per_run: int = 1,
               stagger_minutes: int = 0, title_template: str = "{title}") -> int:
    return db_insert(
        'INSERT INTO tiktok_sources (user_id, handle, profile_url, channel_id, '
        'privacy_status, enabled, max_per_run, stagger_minutes, title_template, '
        'created_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)',
        (uid, handle, profile_url, channel_id, privacy, max_per_run,
         stagger_minutes, title_template, datetime.utcnow().isoformat()))


def mark_seen(source_id: int, video_id: str) -> None:
    db_query('INSERT OR IGNORE INTO tiktok_seen (source_id, video_id, seen_at) '
             'VALUES (?, ?, ?)', (source_id, video_id, datetime.utcnow().isoformat()))


def already_seen(source_id: int, video_id: str) -> bool:
    return bool(db_query('SELECT 1 FROM tiktok_seen WHERE source_id = ? AND video_id = ?',
                         (source_id, video_id), fetch=True))


# ── Saved downloads ("keep it, don't upload it") ────────────────────

def add_download(uid: int, path: str, name: str, size: int, source_url: str = "",
                 kind: str = "link") -> int:
    return db_insert(
        'INSERT INTO downloads (user_id, name, path, size, source_url, kind, created_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?)',
        (uid, name[:120], path, int(size or 0), source_url[:300], kind,
         datetime.utcnow().isoformat()))


def user_downloads(uid: int, limit: int = 25) -> List[tuple]:
    return db_query(
        'SELECT download_id, name, path, size, source_url, kind, created_at '
        'FROM downloads WHERE user_id = ? ORDER BY download_id DESC LIMIT ?',
        (uid, limit), fetch=True) or []


def get_download(download_id: int, uid: int) -> Optional[tuple]:
    rows = db_query(
        'SELECT download_id, name, path, size, source_url, kind, created_at '
        'FROM downloads WHERE download_id = ? AND user_id = ?', (download_id, uid), fetch=True)
    return rows[0] if rows else None


def count_downloads(uid: int) -> int:
    return length_or_zero(db_query('SELECT COUNT(*) FROM downloads WHERE user_id = ?',
                                   (uid,), fetch=True))


def delete_download(download_id: int, uid: int, remove_file: bool = True) -> bool:
    row = get_download(download_id, uid)
    if not row:
        return False
    if remove_file:
        _unlink_stored(row[2])
    db_query('DELETE FROM downloads WHERE download_id = ?', (download_id,))
    return True


def _unlink_stored(path: str) -> None:
    """Delete a file, but only inside our own storage directory."""
    if not path:
        return
    try:
        candidate = Path(path)
        if str(candidate.resolve()).startswith(str(STORAGE_DIR.resolve())) and candidate.exists():
            candidate.unlink()
    except OSError:
        pass


def make_download_token(path: str, uid: int, hours: int = 12) -> str:
    """Signed, expiring token so a big file can be fetched from a browser."""
    token = secrets.token_urlsafe(24)
    db_query('INSERT OR REPLACE INTO download_tokens (token, path, user_id, created_at, expires_at) '
             'VALUES (?, ?, ?, ?, ?)',
             (token, path, uid, datetime.utcnow().isoformat(),
              (datetime.utcnow() + timedelta(hours=hours)).isoformat()))
    return token


def resolve_download_token(token: str) -> Optional[tuple]:
    rows = db_query('SELECT path, user_id, expires_at FROM download_tokens WHERE token = ?',
                    (token,), fetch=True)
    if not rows:
        return None
    path, uid, expires_at = rows[0]
    try:
        if expires_at and datetime.fromisoformat(expires_at) < datetime.utcnow():
            db_query('DELETE FROM download_tokens WHERE token = ?', (token,))
            return None
    except ValueError:
        pass
    if not path or not Path(path).exists():
        return None
    return path, uid


def prune_download_tokens() -> int:
    """Drop expired share links; returns how many were removed."""
    now_iso = datetime.utcnow().isoformat()
    stale = db_query('SELECT COUNT(*) FROM download_tokens WHERE expires_at IS NOT NULL '
                     'AND expires_at < ?', (now_iso,), fetch=True)
    count = stale[0][0] if stale else 0
    if count:
        db_query('DELETE FROM download_tokens WHERE expires_at IS NOT NULL AND expires_at < ?',
                 (now_iso,))
    return int(count)


init_db()

# ═════════════════════════════════════════════════════════════════
#  ENCRYPTION SERVICE
# ═════════════════════════════════════════════════════════════════

_SECRET_FILE = STORAGE_DIR / "secret.key"


def _master_key() -> bytes:
    """Stable Fernet key.

    Derived from TOKEN_ENCRYPTION_KEY when set, otherwise from a generated
    key persisted next to the database. It deliberately does NOT depend on
    the bot token: swapping tokens (or hosting env vs .env) used to make
    every stored OAuth token undecryptable.
    """
    env_key = (os.environ.get("TOKEN_ENCRYPTION_KEY") or "").strip()
    if env_key:
        return hashlib.sha256(env_key.encode()).digest()
    try:
        if _SECRET_FILE.is_file():
            raw = _SECRET_FILE.read_text(encoding="utf-8").strip()
            if raw:
                return base64.urlsafe_b64decode(raw)
        raw = base64.urlsafe_b64encode(secrets.token_bytes(32))
        _SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SECRET_FILE.write_text(raw.decode(), encoding="utf-8")
        try:
            os.chmod(_SECRET_FILE, 0o600)
        except OSError:
            pass
        print("[setup] generated storage/secret.key for token encryption")
        return base64.urlsafe_b64decode(raw)
    except (OSError, ValueError):
        # Last resort - still better than crashing, but tokens will not
        # survive a restart when storage is read-only.
        return hashlib.sha256(f"{TOKEN}|{OWNER_ID}".encode()).digest()


_FERNET = Fernet(base64.urlsafe_b64encode(_master_key()))


def encrypt_data(data: str) -> str:
    return _FERNET.encrypt(data.encode()).decode()


def decrypt_data(data: str) -> str:
    return _FERNET.decrypt(data.encode()).decode()


# ═════════════════════════════════════════════════════════════════
#  SECURITY SCANNER
# ═════════════════════════════════════════════════════════════════

_SEC_PATTERNS = {
    "🔴 Data Theft": [
        (r'os\.walk\s*\(\s*["\'][/\\](?:root|home|etc|var|proc)["\']',
         "System directory scan"),
        (r'send_document\s*\(.*open\s*\(\s*["\'][/\\](?:root|etc|proc)',
         "System file exfiltration"),
    ],
    "🔴 Backdoor": [
        (r'subprocess\s*\.\s*(?:Popen|call|run)\s*\([^\n]*shell\s*=\s*True[^\n]*(?:input|stdin)',
         "Shell injection with user input"),
        (r'marshal\.loads\s*\(',
         "Marshalled bytecode execution"),
    ],
    "🟡 Obfuscation": [
        (r'base64\.b64decode\s*\(.*\)\s*[\)\s]*\bexec\b',
         "Base64 decode + execute"),
        (r'(?:\\x[0-9a-fA-F]{2}){6,}',
         "Long hex string obfuscation"),
    ],
}

_TOKEN_RE = re.compile(r'\b\d{8,10}:AA[A-Za-z0-9_-]{33}\b')


def scan_code(code: str, filename: str = "file.py") -> dict:
    findings = {}
    for cat, patterns in _SEC_PATTERNS.items():
        hits = []
        for pat, desc in patterns:
            if re.search(pat, code, re.IGNORECASE | re.MULTILINE):
                hits.append(desc)
        if hits:
            findings[cat] = hits

    tokens = _TOKEN_RE.findall(code)
    if tokens:
        findings.setdefault("🔴 Exposed Credentials", [])
        findings["🔴 Exposed Credentials"].append(f"Token found: {tokens[0][:15]}...")

    score = sum(10 * min(len(h), 3) for h in findings.values())
    score = min(score, 100)

    has_danger = any(c.startswith("🔴") for c in findings)
    if has_danger and score >= 50:
        verdict = "DANGEROUS"
    elif score >= 30:
        verdict = "SUSPICIOUS"
    else:
        verdict = "SAFE"

    return {"verdict": verdict, "score": score, "findings": findings}


def scan_file(file_path: str) -> dict:
    filename = os.path.basename(file_path)
    try:
        if filename.lower().endswith(('.zip', '.tar.gz', '.tgz', '.tar')):
            return {"verdict": "SAFE", "score": 0, "findings": {}}
        elif filename.lower().endswith(('.py', '.js', '.ts')):
            with open(file_path, 'r', errors='ignore') as f:
                return scan_code(f.read(), filename)
        else:
            return {"verdict": "SAFE", "score": 0, "findings": {}}
    except Exception as e:
        return {"verdict": "ERROR", "score": 50, "findings": {"Error": [str(e)]}}


# ═════════════════════════════════════════════════════════════════
#  YOUTUBE SERVICE
# ═════════════════════════════════════════════════════════════════

# Google OAuth endpoints, kept in one place so the authorization URL and the
# token exchange can never drift apart.
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
# A code that cannot belong to anyone. The OAuth self-check sends it to Google
# so the token endpoint names the misconfiguration instead of the user guessing.
OAUTH_PROBE_CODE = "4/0AX-self-check-throwaway-code"
REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"

# A Connect link / authorization state stays valid for this long.
OAUTH_STATE_TTL_SECONDS = 600

# Short-lived cache of refreshed access tokens: (uid, channel_id) -> (token, expiry).
# Saves a round-trip to Google on every API call while staying correct after the
# 1-hour access-token lifetime.
ACCESS_TOKEN_CACHE: Dict[Tuple[int, str], Tuple[str, datetime]] = {}


class OAuthError(Exception):
    """A failed Google OAuth call, carrying Google's own error code."""

    def __init__(self, error: str, description: str = "", status: int = 0):
        super().__init__(f"{error}: {description}" if description else error)
        self.error = error
        self.description = description
        self.status = status


def _oauth_error_hint(exc: OAuthError) -> str:
    """What to actually go and change, per Google error code.

    Google answers the token endpoint with the same HTTP 400 for four unrelated
    mistakes, so the code is the only useful part of the response.
    """
    if exc.error == "invalid_grant":
        return (" — the code was already used or expired (they last about a minute). "
                "Tap Connect YouTube and finish in one go.")
    if exc.error == "redirect_uri_mismatch":
        return (f" — {YT_REDIRECT_URI} must be listed byte-for-byte under "
                "Authorized redirect URIs in Google Cloud.")
    if exc.error == "invalid_client":
        return (" — YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET are wrong, carry "
                "stray whitespace, or belong to a different OAuth client.")
    if exc.error == "unauthorized_client":
        return (" — the OAuth client must be of type Web application, and the "
                "scopes must be added on the consent screen.")
    return f" — {exc.description}" if exc.description else ""


def _google_error(exc: Exception) -> Optional[OAuthError]:
    """Recover Google's own error code from a library exception, if it carries one.

    google-auth / requests report every token failure as the identical opaque
    text ``400 Client Error: Bad Request for url: https://oauth2.googleapis.com/token``
    — the same string whether the secret is wrong, the redirect URI is not
    registered, or the code was already spent. The JSON body distinguishes them,
    so dig it out rather than passing the plumbing on to the user.
    """
    for source in (exc, *getattr(exc, "args", ())):
        resp = getattr(source, "response", None)
        if resp is None:
            continue
        try:
            body = resp.json()
        except Exception:
            continue
        if not isinstance(body, dict):
            continue
        error = str(body.get("error") or "").strip()
        if not error:
            continue
        try:
            status = int(getattr(resp, "status_code", 0) or 0)
        except (TypeError, ValueError):
            status = 0
        return OAuthError(error, str(body.get("error_description") or "").strip(), status)
    return None


def extract_auth_code(raw: str) -> str:
    """Pull a usable authorization code out of a pasted URL (or a bare code).

    Google codes look like ``4/0ATsMZq...``; the ``/`` may arrive percent-encoded
    as ``%2F``. The value must be decoded EXACTLY ONCE: whatever we hand to
    requests is re-encoded into the form body, so decoding twice (or not at all)
    is the classic cause of ``invalid_grant`` / 400 on the token endpoint.
    """
    text = (raw or "").strip()
    match = re.search(r"code=([^&\s]+)", text)
    if match:
        # Full callback URL -> take the parameter and percent-decode it once.
        return unquote(match.group(1))
    # Not a URL: the user pasted the bare code. Never unquote here, because a
    # bare code may legitimately contain '+' or '%'.
    return text


class YouTubeService:
    def __init__(self):
        self.client_id = YT_CLIENT_ID
        self.client_secret = YT_CLIENT_SECRET
        self.redirect_uri = YT_REDIRECT_URI
        self.scopes = YT_SCOPES

    def get_auth_url(self, state: str) -> str:
        if not _YT_OK:
            return ""
        flow = Flow.from_client_config(
            {"web": {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }},
            scopes=self.scopes,
            # google-auth-oauthlib >=1.0 auto-enables PKCE, which embeds a
            # code_challenge in the auth URL.  But exchange_code() sends a
            # raw HTTP request (not via Flow.fetch_token), so the code_verifier
            # is lost — Google then rejects with "Missing code verifier".
            # Disable PKCE: the client_secret already authenticates the token
            # exchange, making PKCE redundant for this confidential client.
            autogenerate_code_verifier=False,
        )
        flow.redirect_uri = self.redirect_uri
        url, _ = flow.authorization_url(access_type="offline", state=state, prompt="consent")
        return url

    def exchange_code(self, code: str) -> dict:
        """Swap a one-time authorization code for access + refresh tokens.

        ``code`` must already be percent-decoded (see extract_auth_code()).
        Google codes are SINGLE USE and expire in ~60 seconds, so a code that a
        server-side /oauth callback already exchanged returns invalid_grant.
        """
        return self._token_request({
            "code": code,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            # MUST be byte-for-byte the same redirect_uri that produced the code,
            # otherwise Google answers redirect_uri_mismatch (HTTP 400).
            "redirect_uri": self.redirect_uri,
            "grant_type": "authorization_code",
        })

    def refresh_access_token(self, refresh_token: str) -> dict:
        """Trade a stored refresh token for a fresh 1-hour access token."""
        return self._token_request({
            "refresh_token": refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "refresh_token",
        })

    def probe_client_config(self) -> Optional[OAuthError]:
        """Ask Google whether THIS client id / secret / redirect_uri trio works.

        A throwaway code is sent on purpose: Google validates client
        authentication and the redirect URI before it looks at the code, so the
        error it returns *is* the diagnosis. ``invalid_grant`` means everything
        except the fake code was accepted, which is the healthy answer.
        """
        try:
            self._token_request({
                "code": OAUTH_PROBE_CODE,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": self.redirect_uri,
                "grant_type": "authorization_code",
            })
        except OAuthError as exc:
            return exc
        return None

    def _token_request(self, payload: dict) -> dict:
        """POST to Google's token endpoint, mapping failures to OAuthError."""
        try:
            resp = req_lib.post(TOKEN_ENDPOINT, data=payload, timeout=20)
        except Exception as exc:  # network / DNS / TLS failure
            print(f"[oauth] token request failed: {exc}")
            raise OAuthError("network_error", str(exc)) from exc

        if resp.status_code != 200:
            # Google always answers 400 with a JSON {error, error_description}.
            try:
                body = resp.json()
            except Exception:
                body = {}
            err = str(body.get("error") or f"http_{resp.status_code}")
            desc = str(body.get("error_description") or resp.text[:300])
            print(f"[oauth] token endpoint {resp.status_code}: {err} - {desc} "
                  f"(redirect_uri={payload.get('redirect_uri', '-')})")
            raise OAuthError(err, desc, resp.status_code)
        return resp.json()

    def revoke_token(self, token: str) -> bool:
        """Best-effort token revoke at Google (used by Disconnect)."""
        if not token:
            return False
        try:
            resp = req_lib.post(REVOKE_ENDPOINT, data={"token": token}, timeout=15)
            return resp.status_code == 200
        except Exception as exc:
            print(f"[oauth] revoke failed: {exc}")
            return False

    def build_credentials(self, access_token: str, refresh_token: str,
                          expiry: Optional[datetime] = None):
        return Credentials(
            token=access_token,
            refresh_token=refresh_token or None,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=self.client_id,
            client_secret=self.client_secret,
            scopes=self.scopes,
            expiry=expiry,
        )

    def credentials_for_channel(self, uid: int, channel_id: str):
        """Load (refreshing when needed) ONE user's OAuth credentials.

        Multi-user model: every Telegram user owns their own row in
        youtube_channels, so uploads always go to THEIR channel. There is no
        global refresh token anywhere in this bot.
        """
        row = channel_row(uid, channel_id)
        if not row:
            raise ValueError("Channel not connected. Use Connect YouTube first.")
        _cid, _name, enc_access, enc_refresh, expiry_iso = row
        access = decrypt_data(enc_access) if enc_access else ""
        refresh = decrypt_data(enc_refresh) if enc_refresh else ""
        expiry = None
        if expiry_iso:
            try:
                expiry = datetime.fromisoformat(expiry_iso)
            except ValueError:
                expiry = None

        # 1. Serve a still-valid token straight from the in-memory cache.
        cached = ACCESS_TOKEN_CACHE.get((uid, channel_id))
        if cached and cached[1] - timedelta(minutes=3) > datetime.utcnow():
            return self.build_credentials(cached[0], refresh, cached[1])

        # 2. Use the stored token while it still has >3 minutes left.
        need_refresh = (not access) or bool(
            expiry and expiry - timedelta(minutes=3) <= datetime.utcnow())

        if need_refresh and refresh:
            # 3. Exchange the refresh token for a fresh 1-hour access token.
            try:
                tok = self.refresh_access_token(refresh)
            except OAuthError as exc:
                if exc.error in ("invalid_grant", "invalid_client", "unauthorized_client"):
                    # Google revoked it: the user removed access, or the OAuth app
                    # is still in "Testing" mode where refresh tokens die after 7
                    # days. Flag the channel and tell its owner (once per grant).
                    mark_channel_revoked(uid, channel_id, exc.error)
                    raise ValueError(
                        "YouTube access was revoked or expired. "
                        "Tap Connect YouTube to link the channel again.") from exc
                raise ValueError(f"Could not refresh the YouTube token: {exc}") from exc

            access = tok.get("access_token", access)
            expires_in = int(tok.get("expires_in", 3600))
            new_expiry = datetime.utcnow() + timedelta(seconds=expires_in)
            db_query('UPDATE youtube_channels SET access_token = ?, token_expiry = ?, '
                     'status = ?, last_error = NULL WHERE user_id = ? AND channel_id = ?',
                     (encrypt_data(access), new_expiry.isoformat(), "connected",
                      uid, channel_id))
            expiry = new_expiry
            ACCESS_TOKEN_CACHE[(uid, channel_id)] = (access, expiry)
        elif need_refresh and not refresh:
            raise ValueError("Stored token expired and no refresh token - reconnect the channel.")

        return self.build_credentials(access, refresh, expiry)

    def get_channel_info(self, credentials) -> dict:
        youtube = build("youtube", "v3", credentials=credentials)
        resp = youtube.channels().list(part="snippet,statistics", mine=True).execute()
        if not resp.get("items"):
            raise ValueError("No YouTube channel found")
        ch = resp["items"][0]
        return {
            "channel_id": ch["id"],
            "channel_name": ch["snippet"]["title"],
            "thumbnail": ch["snippet"]["thumbnails"].get("default", {}).get("url", ""),
            "subscribers": ch.get("statistics", {}).get("subscriberCount"),
        }

    def upload_video(self, credentials, file_path: str, metadata: dict,
                     on_progress=None) -> dict:
        """Resumable upload. "publish_at" schedules a public premiere natively."""
        youtube = build("youtube", "v3", credentials=credentials)
        body = {
            "snippet": {
                "title": (metadata.get("title") or "Untitled")[:100],
                "description": metadata.get("description", ""),
                "tags": metadata.get("tags", []),
                "categoryId": metadata.get("category_id", "22"),
            },
            "status": {
                "privacyStatus": metadata.get("privacy_status", "private"),
                "selfDeclaredMadeForKids": False,
            },
        }
        publish_at = metadata.get("publish_at")
        if publish_at:
            if isinstance(publish_at, datetime):
                publish_at = publish_at.strftime("%Y-%m-%dT%H:%M:%SZ")
            body["status"]["privacyStatus"] = "private"
            body["status"]["publishAt"] = publish_at

        media = MediaFileUpload(file_path, mimetype=metadata.get("mime_type", "video/mp4"),
                                resumable=True, chunksize=10 * 1024 * 1024)
        req = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
        response = None
        while response is None:
            status, response = req.next_chunk()
            if status and on_progress:
                try:
                    on_progress(int(status.progress() * 100))
                except Exception:
                    pass
        return {
            "video_id": response["id"],
            "video_url": f"https://youtu.be/{response['id']}",
            "title": response["snippet"]["title"],
            "privacy_status": response.get("status", {}).get("privacyStatus", "private"),
        }

    def set_thumbnail(self, credentials, video_id: str, image_path: str) -> bool:
        youtube = build("youtube", "v3", credentials=credentials)
        try:
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(image_path, resumable=False)).execute()
            return True
        except Exception:
            return False

    def video_stats(self, credentials, video_id: str) -> dict:
        youtube = build("youtube", "v3", credentials=credentials)
        resp = youtube.videos().list(part="statistics,status", id=video_id).execute()
        items = resp.get("items", [])
        if not items:
            return {}
        stats = items[0].get("statistics", {})
        return {
            "views": stats.get("viewCount", "0"),
            "likes": stats.get("likeCount", "0"),
            "comments": stats.get("commentCount", "0"),
            "privacy": items[0].get("status", {}).get("privacyStatus", "?"),
        }

    def list_playlists(self, credentials) -> list:
        youtube = build("youtube", "v3", credentials=credentials)
        resp = youtube.playlists().list(part="snippet", mine=True, maxResults=50).execute()
        return [
            {"id": p["id"], "title": p["snippet"]["title"]}
            for p in resp.get("items", [])
        ]


yt_service = YouTubeService() if _YT_OK else None

# ═════════════════════════════════════════════════════════════════
#  BOT SETUP
# ═════════════════════════════════════════════════════════════════

bot = telebot.TeleBot(TOKEN, parse_mode="HTML", threaded=True, num_threads=8)
START_TIME = time.time()

# Conversation state lives in memory and expires, so a half-finished
# "send me a title" flow never hijacks a message an hour later.
USER_STATES: Dict[int, Dict[str, Any]] = {}
RUNNING_PROCS: Dict[Tuple[int, str], subprocess.Popen] = {}
STATE_TTL_SECONDS = 1800
# Cooldown for manual OAuth code exchange (prevents spam)
OAUTH_PASTE_COOLDOWN: Dict[int, float] = {}  # uid -> last attempt timestamp
OAUTH_PASTE_COOLDOWN_SECONDS = 30
UPLOAD_LOCK = threading.Lock()
BROADCAST_STOP = threading.Event()

SOURCE_LABELS = {
    "telegram": "Telegram upload",
    "link": "Link download",
    "tiktok": "TikTok auto-post",
    "manual": "Manual",
}
PRIVACY_LABELS = {"public": "🌐 Public", "private": "🔒 Private", "unlisted": "🔗 Unlisted"}
STATUS_LABELS = {
    "pending": "⏳ Queued", "scheduled": "🗓 Scheduled", "uploading": "📤 Uploading",
    "completed": "✅ Published", "failed": "❌ Failed",
}


def set_state(uid: int, state: str, **data) -> Dict[str, Any]:
    entry = {"state": state, "ts": time.time()}
    entry.update(data)
    USER_STATES[uid] = entry
    return entry


def get_state(uid: int) -> Dict[str, Any]:
    entry = USER_STATES.get(uid)
    if not entry:
        return {}
    if time.time() - float(entry.get("ts", 0)) > STATE_TTL_SECONDS:
        USER_STATES.pop(uid, None)
        return {}
    return entry


def clear_state(uid: int) -> None:
    USER_STATES.pop(uid, None)


# ── small formatting helpers ────────────────────────────────────────

def human_size(num_bytes: Optional[float]) -> str:
    if not num_bytes:
        return "?"
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f}{unit}" if unit != "B" else f"{int(size)}B"
        size /= 1024
    return f"{size:.1f}GB"


def fmt_when(iso: Optional[str]) -> str:
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(iso).strftime("%d %b %Y %H:%M UTC")
    except ValueError:
        return iso


def safe_name(text: str, fallback: str = "video", max_len: int = 70) -> str:
    """Filename-safe version of a title (no path separators or markup)."""
    cleaned = re.sub(r'[^\w.\- ]+', '', (text or "").replace("\n", " "))
    cleaned = re.sub(r'\.{2,}', '.', cleaned).lstrip('.').strip()
    cleaned = re.sub(r'\s+', "_", cleaned)[:max_len].strip("_")
    return cleaned or fallback


def parse_when(text: str) -> Optional[datetime]:
    """Parse '+30m', '+2h', '+3d' or 'YYYY-MM-DD HH:MM' into UTC."""
    raw = (text or "").strip().lower()
    if not raw:
        return None
    rel = re.fullmatch(r"\+?\s*(\d+)\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)", raw)
    if rel:
        amount = int(rel.group(1))
        unit = rel.group(2)[0]
        delta = {"m": timedelta(minutes=amount), "h": timedelta(hours=amount),
                 "d": timedelta(days=amount)}[unit]
        return datetime.utcnow() + delta
    for pattern in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%d-%m-%Y %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw.replace("t", "T"), pattern)
        except ValueError:
            continue
    return None


# ── safe Telegram wrappers (buttons must never fail silently) ───────

def safe_answer(call, text: str = "", alert: bool = False) -> None:
    try:
        bot.answer_callback_query(call.id, text or None, show_alert=alert)
    except Exception:
        pass


def safe_edit(call, text: str, kb=None) -> None:
    """Edit the callback message, falling back to a fresh message."""
    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id,
                              reply_markup=kb)
    except ApiTelegramException as exc:
        if "message is not modified" in str(exc):
            return
        try:
            bot.send_message(call.message.chat.id, text, reply_markup=kb)
        except Exception as send_exc:
            print(f"[ui] could not render menu: {send_exc}")
    except Exception as exc:
        print(f"[ui] could not edit message: {exc}")


def safe_send(chat_id: int, text: str, kb=None) -> None:
    try:
        bot.send_message(chat_id, text, reply_markup=kb, disable_web_page_preview=True)
    except Exception as exc:
        print(f"[ui] could not send message to {chat_id}: {exc}")


# ═════════════════════════════════════════════════════════════════
#  SOCIAL DOWNLOADER (yt-dlp)
# ═════════════════════════════════════════════════════════════════

URL_RE = re.compile(r'https?://[^\s<>"\']+', re.IGNORECASE)
SUPPORTED_HOSTS = (
    "youtube.com", "youtu.be", "youtube-nocookie.com", "tiktok.com", "vm.tiktok.com",
    "instagram.com", "facebook.com", "fb.watch", "twitter.com", "x.com", "vimeo.com",
    "dailymotion.com", "reddit.com", "twitch.tv", "pinterest.com", "snapchat.com",
    "likee.video", "kwai.com", "rumble.com", "odysee.com",
)


def is_supported_url(url: str) -> bool:
    low = url.lower()
    return any(host in low for host in SUPPORTED_HOSTS)


def extract_url(text: str) -> str:
    match = URL_RE.search(text or "")
    return match.group(0).rstrip('.,);:!?\'"') if match else ""


def _ytdlp_opts(extra: Optional[dict] = None) -> dict:
    opts: Dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 3,
        "socket_timeout": 30,
        "noplaylist": True,
    }
    if os.path.isfile(SOCIAL_COOKIES_FILE):
        opts["cookiefile"] = SOCIAL_COOKIES_FILE
    if extra:
        opts.update(extra)
    return opts


def best_format(max_height: Optional[int] = None) -> str:
    height = max_height or SOCIAL_MAX_HEIGHT
    if shutil.which("ffmpeg"):
        return f"bv*[height<={height}]+ba/b[height<={height}]/b"
    # Without ffmpeg only a single progressive file can be used.
    return f"b[ext=mp4][height<={height}]/b[ext=mp4]/b"


def probe_media(url: str) -> dict:
    """Return metadata for a link (or the first item of a profile/playlist)."""
    from yt_dlp import YoutubeDL

    with YoutubeDL(_ytdlp_opts({"skip_download": True,
                                "extract_flat": False if "/video/" in url else "in_playlist"})) as ydl:
        info = ydl.extract_info(url, download=False)
    entries = [e for e in (info.get("entries") or []) if e]
    first = entries[0] if entries else info
    return {
        "title": first.get("title") or info.get("title") or "Untitled",
        "uploader": first.get("uploader") or first.get("channel") or info.get("uploader") or "",
        "duration": first.get("duration") or 0,
        "extractor": (info.get("extractor_key") or info.get("extractor") or "media"),
        "id": first.get("id") or "",
        "url": first.get("webpage_url") or url,
        "playlist_size": len(entries),
        "thumbnail": first.get("thumbnail") or "",
    }


def download_media(url: str, dest_dir: Path, progress=None,
                   max_height: Optional[int] = None) -> tuple:
    """Download a video/picture link and return (path, info)."""
    from yt_dlp import YoutubeDL

    dest_dir.mkdir(parents=True, exist_ok=True)
    outtmpl = str(dest_dir / "%(title).60B_%(id)s.%(ext)s")

    def hook(data):
        if not progress or data.get("status") != "downloading":
            return
        total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
        got = data.get("downloaded_bytes") or 0
        if total:
            progress(min(100, int(got * 100 / total)))

    opts = _ytdlp_opts({
        "outtmpl": outtmpl,
        "format": best_format(max_height),
        "merge_output_format": "mp4",
        "progress_hooks": [hook],
        "noplaylist": "/video/" in url or "/status/" in url,
    })
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if info.get("entries"):
            info = [e for e in info["entries"] if e][0]
        path = Path(ydl.prepare_filename(info))
    if not path.exists():
        candidates = sorted(dest_dir.glob(f"*{info.get('id', '')}*"),
                            key=lambda p: p.stat().st_mtime, reverse=True)
        if candidates:
            path = candidates[0]
    if not path.exists():
        raise FileNotFoundError("download produced no file")
    return path, info


def tiktok_profile_url(handle_or_url: str) -> tuple:
    """Accept '@user', 'user', a profile link or a video link."""
    raw = (handle_or_url or "").strip()
    url = extract_url(raw)
    if url:
        if "/video/" in url:
            base = url.split("/video/", 1)[0]
            handle = base.rstrip("/").split("/")[-1].lstrip("@")
        else:
            base = url.rstrip("/")
            handle = base.split("/")[-1].lstrip("@")
        return handle, base
    handle = raw.lstrip("@").strip()
    return handle, f"https://www.tiktok.com/@{handle}"


# Telegram bots may upload/send files up to 50MB, and may download at most
# 20MB from getFile(). Anything larger has to travel as a browser link.
TG_SEND_LIMIT = 50 * 1024 * 1024
BULK_MAX_VIDEOS = int(os.environ.get("BULK_MAX_VIDEOS", 50))


def list_profile_videos(profile_url: str, limit: int = 10) -> List[dict]:
    """List the most recent videos of a profile (newest first when possible)."""
    from yt_dlp import YoutubeDL

    opts = _ytdlp_opts({"extract_flat": "in_playlist", "skip_download": True,
                        "playlistend": max(1, limit)})
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(profile_url, download=False)
    entries = [e for e in (info.get("entries") or []) if e]
    videos = [{
        "id": str(e.get("id") or ""),
        "title": e.get("title") or "TikTok video",
        "url": e.get("url") or e.get("webpage_url") or "",
    } for e in entries if e.get("id")]
    # TikTok ids grow with time, so numeric ids give a reliable newest-first
    # order even when the extractor returns the profile oldest-first.
    if videos and all(v["id"].isdigit() for v in videos):
        videos.sort(key=lambda v: int(v["id"]), reverse=True)
    return videos


# ═════════════════════════════════════════════════════════════════
#  UPLOAD RUNNER
# ═════════════════════════════════════════════════════════════════

def job_tags_list(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [t for t in raw if t]
    return [t.strip().lstrip("#") for t in re.split(r"[,\s]+", str(raw)) if t.strip()]


def notify(uid: int, text: str, kb=None) -> None:
    try:
        bot.send_message(uid, text, reply_markup=kb, disable_web_page_preview=True)
    except Exception:
        pass


def run_job_upload(job_id: int, quiet: bool = False) -> bool:
    """Upload a queued job to YouTube. Returns True when it succeeded."""
    job = get_job(job_id)
    if not job:
        return False

    uid = int(job_field(job, "user_id"))
    channel_id = job_field(job, "channel_id") or default_channel_id(uid)
    file_path = job_field(job, "file_path") or ""
    title = job_field(job, "title") or "Untitled"
    privacy = job_field(job, "privacy_status") or "private"
    publish_at = job_field(job, "publish_at")

    if not yt_service or not _YT_OK:
        db_query('UPDATE upload_jobs SET status = "failed", error_msg = ? WHERE job_id = ?',
                 ("YouTube API not configured", job_id))
        return False

    if not channel_id:
        db_query('UPDATE upload_jobs SET status = "failed", error_msg = ? WHERE job_id = ?',
                 ("No YouTube channel connected", job_id))
        notify(uid, f"❌ Job #{job_id} failed: no YouTube channel connected.",
               types.InlineKeyboardMarkup().add(
                   Btn(f"{G['play']}  Connect YouTube", callback_data="yt_connect", style="primary")))
        return False

    if not file_path or not Path(file_path).exists():
        db_query('UPDATE upload_jobs SET status = "failed", error_msg = ? WHERE job_id = ?',
                 ("Video file missing on disk", job_id))
        notify(uid, f"❌ Job #{job_id} failed: the video file is gone from storage.")
        return False

    with UPLOAD_LOCK:
        db_query('UPDATE upload_jobs SET status = "uploading", progress = 0, error_msg = NULL '
                 'WHERE job_id = ?', (job_id,))
        last_shown = {"pct": -20}

        def on_progress(pct: int) -> None:
            if pct - last_shown["pct"] >= 20:
                last_shown["pct"] = pct
                db_query('UPDATE upload_jobs SET progress = ? WHERE job_id = ?', (pct, job_id))
                if not quiet:
                    notify(uid, f"📤 Uploading #{job_id} — {pct}%")

        try:
            # YouTube only honours publishAt when it is at least a few
            # minutes ahead; otherwise upload right now.
            native_schedule = None
            if publish_at:
                try:
                    when = datetime.fromisoformat(publish_at)
                    if when > datetime.utcnow() + timedelta(minutes=15):
                        native_schedule = when
                except ValueError:
                    native_schedule = None

            credentials = yt_service.credentials_for_channel(uid, channel_id)
            metadata = {
                "title": title,
                "description": job_field(job, "description") or "",
                "tags": job_tags_list(job_field(job, "tags")),
                "privacy_status": privacy,
                "publish_at": native_schedule,
            }
            result = yt_service.upload_video(credentials, file_path, metadata,
                                             on_progress=on_progress)
        except Exception as exc:
            attempts = int(job_field(job, "retries") or 0) + 1
            message = str(exc)[:400]
            if attempts < MAX_JOB_RETRIES:
                retry_at = (datetime.utcnow() + timedelta(minutes=2 ** attempts)).isoformat()
                db_query('UPDATE upload_jobs SET status = "scheduled", retries = ?, error_msg = ?, '
                         'scheduled_at = ? WHERE job_id = ?',
                         (attempts, message, retry_at, job_id))
                notify(uid, f"⚠️ Upload #{job_id} failed (attempt {attempts}/{MAX_JOB_RETRIES}):\n"
                            f"<code>{escape(message)}</code>\n\n"
                            f"🔁 Retrying automatically at {fmt_when(retry_at)}")
            else:
                db_query('UPDATE upload_jobs SET status = "failed", retries = ?, error_msg = ? '
                         'WHERE job_id = ?', (attempts, message, job_id))
                notify(uid, f"❌ Upload #{job_id} failed permanently:\n"
                            f"<code>{escape(message)}</code>")
            return False

        db_query('UPDATE upload_jobs SET status = "completed", video_id = ?, progress = 100, '
                 'completed_at = ?, error_msg = NULL WHERE job_id = ?',
                 (result["video_id"], datetime.utcnow().isoformat(), job_id))
        audit(uid, "youtube_upload", f"job={job_id} video={result['video_id']}")

        # A thumbnail attached earlier (photo message) is applied now.
        thumb_row = db_query('SELECT thumb_path FROM upload_jobs WHERE job_id = ?',
                             (job_id,), fetch=True)
        thumb = thumb_row[0][0] if thumb_row else ""
        if thumb and Path(thumb).exists() and result.get("video_id"):
            try:
                yt_service.set_thumbnail(credentials, result["video_id"], thumb)
            except Exception as exc:
                print(f"[upload] thumbnail failed for job {job_id}: {exc}")

    channel = channel_row(uid, channel_id)
    when_line = f"\n🗓 Publishes: {fmt_when(publish_at)}" if publish_at else ""
    notify(uid,
           f"✅ <b>Uploaded!</b>\n\n"
           f"🎬 {escape(title)}\n"
           f"📺 {escape(channel[1] if channel else channel_id)}\n"
           f"🔗 {result['video_url']}{when_line}",
           types.InlineKeyboardMarkup(row_width=1).add(
               types.InlineKeyboardButton("▶️ Watch on YouTube", url=result["video_url"])))
    return True


def promote_due_jobs() -> int:
    """Move scheduled jobs whose time has come into the queue."""
    now_iso = datetime.utcnow().isoformat()
    due = due_jobs(now_iso)
    for job in due:
        db_query('UPDATE upload_jobs SET status = "pending" WHERE job_id = ?',
                 (job_field(job, "job_id"),))
    return len(due)


def upload_worker_loop() -> None:
    print("[worker] upload worker started")
    while True:
        try:
            promote_due_jobs()
            pending = db_query(
                f'SELECT {JOB_COLUMNS} FROM upload_jobs WHERE status = "pending" '
                'AND file_path IS NOT NULL ORDER BY job_id LIMIT 3', fetch=True) or []
            for job in pending:
                run_job_upload(int(job_field(job, "job_id")))
        except Exception as exc:
            print(f"[worker] error: {exc}")
            traceback.print_exc()
        time.sleep(SCHEDULER_TICK_SECONDS)


# ═════════════════════════════════════════════════════════════════
#  TIKTOK -> YOUTUBE AUTO POSTING
# ═════════════════════════════════════════════════════════════════

def poll_source(source_id: int, announce: bool = True) -> int:
    """Check one source and queue any new TikTok videos. Returns job count."""
    raw = get_source(int(source_id))
    if raw is None:
        return 0
    cells = dict(zip(
        ["source_id", "user_id", "handle", "profile_url", "channel_id", "privacy_status",
         "enabled", "interval_seconds", "max_per_run", "stagger_minutes", "title_template",
         "last_check", "last_status", "created_at"], raw))
    uid = int(cells["user_id"])
    handle = cells["handle"] or ""
    channel_id = cells["channel_id"]
    profile_url = cells["profile_url"] or f"https://www.tiktok.com/@{handle}"
    privacy = cells["privacy_status"] or "private"
    max_per_run = max(1, int(cells["max_per_run"] or 1))
    stagger = max(0, int(cells["stagger_minutes"] or 0))

    try:
        videos = list_profile_videos(profile_url, limit=max(10, max_per_run * 3))
    except Exception as exc:
        db_query('UPDATE tiktok_sources SET last_check = ?, last_status = ? WHERE source_id = ?',
                 (datetime.utcnow().isoformat(), f"error: {str(exc)[:180]}", int(source_id)))
        if announce:
            notify(uid, f"⚠️ TikTok check failed for @{escape(handle)}:\n"
                        f"<code>{escape(str(exc)[:300])}</code>\n\n"
                        f"TikTok often blocks data-center IPs — add a cookies file "
                        f"(<code>{escape(SOCIAL_COOKIES_FILE)}</code>) to fix this.")
        return 0

    queued = 0
    for video in videos:
        if queued >= max_per_run:
            break
        if already_seen(source_id, video["id"]):
            continue
        url = video["url"] or f"https://www.tiktok.com/@{handle}/video/{video['id']}"
        dest = STORAGE_DIR / "social" / str(uid) / "tiktok" / handle
        try:
            path, info = download_media(url, dest)
        except Exception as exc:
            mark_seen(source_id, video["id"])
            notify(uid, f"⚠️ Could not download TikTok {escape(video['id'])}: "
                        f"<code>{escape(str(exc)[:200])}</code>")
            continue
        mark_seen(source_id, video["id"])

        title = (video["title"] or info.get("title") or "TikTok video").strip()
        source_line = f"\n\nSource: {url}" if url else ""
        publish_at = None
        if stagger:
            publish_at = (datetime.utcnow() + timedelta(minutes=stagger * (queued + 1))).isoformat()
        # Extract hashtags from TikTok title and apply user defaults
        source_hashtags = extract_hashtags(title)
        defaults = get_user_defaults(uid)
        tk_tags = source_hashtags or "tiktok,shorts"
        tk_desc = f"{title}{source_line}"
        if defaults.get("tags"):
            tk_tags = defaults["tags"] + "," + tk_tags
        if defaults.get("description"):
            tk_desc = defaults["description"] + "\n\n" + tk_desc
        job_id = create_job(
            uid, channel_id, title[:100], file_path=str(path), source="tiktok",
            source_url=url, privacy=privacy, description=tk_desc,
            tags=tk_tags, publish_at=publish_at, status="pending")
        queued += 1
        # Each new video is also a YouTube Short candidate - keep it vertical.
        notify(uid, f"🎵 New TikTok from @{escape(handle)}\n"
                    f"🎬 {escape(title[:80])}\n"
                    f"📋 Job #{job_id} queued"
                    + (f"\n🗓 Scheduled: {fmt_when(publish_at)}" if publish_at else ""))

    db_query('UPDATE tiktok_sources SET last_check = ?, last_status = ? WHERE source_id = ?',
             (datetime.utcnow().isoformat(),
              f"ok: {queued} new" if queued else "ok: nothing new", source_id))
    return queued


def tiktok_loop() -> None:
    print("[tiktok] auto-post watcher started")
    while True:
        try:
            rows = db_query(
                'SELECT source_id, interval_seconds, last_check FROM tiktok_sources '
                'WHERE enabled = 1', fetch=True) or []
            for source_id, interval, last_check in rows:
                due = True
                if last_check:
                    try:
                        last = datetime.fromisoformat(last_check)
                        due = (datetime.utcnow() - last).total_seconds() >= max(
                            60, int(interval or TIKTOK_POLL_SECONDS))
                    except ValueError:
                        due = True
                if due:
                    poll_source(int(source_id))
        except Exception as exc:
            print(f"[tiktok] loop error: {exc}")
        time.sleep(60)


# ═════════════════════════════════════════════════════════════════
#  FLASK: keep-alive + YouTube OAuth callback
# ═════════════════════════════════════════════════════════════════

app_flask = Flask(__name__)


def page(title: str, body: str) -> str:
    return ("<!doctype html><html><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{escape(title)}</title>"
            "<style>body{font-family:system-ui,-apple-system,Segoe UI,sans-serif;"
            "background:#0f1115;color:#e8e8ea;display:flex;min-height:100vh;"
            "align-items:center;justify-content:center;margin:0;padding:24px}"
            "main{max-width:520px;background:#171a21;padding:28px 32px;border-radius:16px;"
            "border:1px solid #262a35}h1{font-size:22px;margin:0 0 12px}"
            "p{line-height:1.5;color:#b8bcc8;margin:8px 0}code{background:#0f1115;"
            "padding:2px 6px;border-radius:6px}</style></head><body><main>"
            f"<h1>{title}</h1>{body}</main></body></html>")


def oauth_redirect_target() -> str:
    return YT_REDIRECT_URI if YT_REDIRECT_URI else ""


@app_flask.route("/")
def home():
    channels = db_query('SELECT COUNT(*) FROM youtube_channels', fetch=True)[0][0]
    jobs = db_query('SELECT COUNT(*) FROM upload_jobs', fetch=True)[0][0]
    return (f"{BRAND} is running!\n"
            f"uptime={int(time.time() - START_TIME)}s channels={channels} jobs={jobs}\n"
            f"oauth_redirect={oauth_redirect_target()}\n"
            f"build={_code_fingerprint()}\n")


@app_flask.route("/health")
def health():
    return "alive"


@app_flask.route("/diag")
def diag_page():
    return page("Bot diagnostics",
                "<p>Bot is alive.</p>"
                f"<p>Build: <code>{escape(build_marker())}</code><br>"
                f"Token source: <code>{escape(value_source(BOT_TOKEN_ENV_KEY))}</code><br>"
                f"OAuth redirect URI: <code>{escape(oauth_redirect_target() or 'not set')}</code><br>"
                f"Storage dir: <code>{escape(str(STORAGE_DIR))}</code><br>"
                f"TikTok cookies file: <code>{escape(SOCIAL_COOKIES_FILE)}</code> "
                f"({'found' if os.path.isfile(SOCIAL_COOKIES_FILE) else 'missing'})</p>"
                '<p><a href="/oauth-check">Run the OAuth self-check →</a></p>')


@app_flask.route("/oauth-check")
def oauth_check_page():
    """The same self-check the Telegram button runs, for a browser tab."""
    body = oauth_self_check_html(check_reachability=False)
    return page("OAuth self-check", f"<p>{'<br>'.join(body.splitlines())}</p>")


def _handle_oauth_callback(state: str, code: str, error: str = ""):
    if error:
        return page("Connection failed", f"<p>Google returned: <code>{escape(error)}</code></p>")

    row = db_query('SELECT user_id, chat_id, created_at FROM oauth_states WHERE state = ?',
                   (state,), fetch=True)
    if not row:
        return page("Link expired",
                    "<p>This authorization link was already used or the bot restarted. "
                    "Open the bot and tap <b>Connect YouTube</b> again.</p>")
    uid = int(row[0][0])
    # States are single-use: burn it before doing anything else so a browser
    # refresh can never replay the (already consumed) authorization code.
    db_query('DELETE FROM oauth_states WHERE state = ?', (state,))

    # A stale state means the user sat on the Google page too long; Google's code
    # is dead by now, so say so instead of surfacing a raw Google error.
    created_at = row[0][2]
    if created_at:
        try:
            age = (datetime.utcnow() - datetime.fromisoformat(created_at)).total_seconds()
        except ValueError:
            age = 0.0
        if age > OAUTH_STATE_TTL_SECONDS:
            notify(uid, "⌛ That <b>Connect YouTube</b> link expired. Tap it again to retry.")
            return page("Link expired",
                        "<p>This authorization link is too old. Return to Telegram and "
                        "tap <b>Connect YouTube</b> again.</p>")

    if not code:
        return page("Connection failed",
                    "<p>Google did not send an authorization code. Try again from Telegram.</p>")

    try:
        # Normalise the code exactly like the manual paste flow does.
        tokens = yt_service.exchange_code(extract_auth_code(code))
        info = save_channel_tokens(uid, tokens)
    except Exception as exc:
        # Libraries get to report their failures in Google's own words too: this
        # is the path that used to show the user "400 Client Error: Bad Request".
        oauth_exc = exc if isinstance(exc, OAuthError) else _google_error(exc)
        if oauth_exc is None:
            print(f"[oauth] callback failed for uid={uid}: {exc}")
            notify(uid, f"❌ YouTube connection failed: <code>{escape(str(exc)[:300])}</code>")
            return page("Connection failed", f"<p>{escape(str(exc)[:400])}</p>")
        print(f"[oauth] callback exchange failed for uid={uid}: "
              f"{oauth_exc.error} - {oauth_exc.description}")
        hint = _oauth_error_hint(oauth_exc)
        notify(uid, f"❌ YouTube connection failed: "
                    f"<code>{escape(oauth_exc.error)}</code>{escape(hint)}")
        return page("Connection failed",
                    f"<p><b>{escape(oauth_exc.error)}</b>: "
                    f"{escape(oauth_exc.description[:300])}{escape(hint)}</p>")

    if not tokens.get("refresh_token"):
        # Without a refresh token the access token dies in ~1 hour and uploads
        # stop working, so tell the user how to force a new one.
        notify(uid,
               "⚠️ <b>Connected, but Google sent no refresh token.</b>\n\n"
               "Access will expire in about an hour. To fix it, revoke the app at "
               '<a href="https://myaccount.google.com/permissions">myaccount.google.com/permissions</a> '
               "and tap Connect YouTube again.")

    notify(uid,
           f"✅ <b>YouTube connected!</b>\n\n"
           f"📺 {escape(info['channel_name'])}\n"
           f"🆔 <code>{escape(info['channel_id'])}</code>\n\n"
           f"You can close the browser tab and come back to Telegram.",
           types.InlineKeyboardMarkup(row_width=1)
           .add(Btn(f"{G['upload']}  Upload Hub", callback_data="upload_hub", style="success"))
           .add(Btn(f"{G['tiktok']}  Auto-post TikTok", callback_data="tk_menu", style="primary")))
    return page("Channel connected",
                f"<p>✅ <b>{escape(info['channel_name'])}</b> is now connected.</p>"
                "<p>Return to Telegram — the bot already sent you a confirmation.</p>")


@app_flask.route("/dl/<token>")
def download_by_token(token: str):
    """Serve a stored download over HTTP for files too big for Telegram."""
    resolved = resolve_download_token(token)
    if not resolved:
        return page("Link expired",
                    "<p>This download link no longer works. Ask the bot for a fresh one.</p>"), 404
    path, _uid = resolved
    try:
        return send_file(path, as_attachment=True, download_name=Path(path).name)
    except Exception as exc:
        return page("Download failed", f"<p>{escape(str(exc))}</p>"), 500


@app_flask.route("/oauth/callback")
@app_flask.route("/oauth/youtube/callback")
@app_flask.route("/api/oauth/youtube/callback")
@app_flask.route("/connect")
def youtube_oauth_callback():
    args = request.args
    return _handle_oauth_callback(args.get("state", ""), args.get("code", ""),
                                  args.get("error", ""))


def start_keepalive():
    Thread(target=lambda: app_flask.run(host="0.0.0.0", port=HTTP_PORT, debug=False,
                                        use_reloader=False), daemon=True).start()


# ═════════════════════════════════════════════════════════════════
#  MENU BUILDERS (Colored Buttons!)
# ═════════════════════════════════════════════════════════════════

def main_menu_kb(uid: int) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    status = get_user_status(uid)
    has_channels = bool(user_channels(uid))

    # Row 1: YouTube account
    kb.add(
        Btn(f"{G['play']}  Connect YouTube", callback_data="yt_connect", style="primary"),
        Btn(f"{G['folder']}  My Channels", callback_data="yt_channels", style="primary"),
    )
    # Row 2: Upload hub + queue
    if has_channels:
        kb.add(
            Btn(f"{G['upload']}  Upload Hub", callback_data="upload_hub", style="success"),
            Btn(f"{G['queue']}  Queue & Schedule", callback_data="queue_menu", style="primary"),
        )
    else:
        kb.add(
            Btn(f"{G['upload']}  Upload Video", callback_data="yt_upload_now", style="primary"),
            Btn(f"{G['queue']}  Queue & Schedule", callback_data="queue_menu", style="primary"),
        )
    # Row 3: Manage videos + Downloads
    kb.add(
        Btn(f"{G['graph']}  My Videos", callback_data="manage_vids", style="primary"),
        Btn(f"{G['folder']}  My Downloads", callback_data="dl_list:0", style="primary"),
    )
    # Row 4: Downloads + TikTok automation
    kb.add(
        Btn(f"{G['social']}  Download / Save", callback_data="social_menu", style="success"),
        Btn(f"{G['tiktok']}  TikTok → YouTube", callback_data="tk_menu", style="success"),
    )
    # Row 5: Bot speed + File hosting
    kb.add(
        Btn(f"{G['bolt']}  Bot Speed", callback_data="bot_speed", style="primary"),
        Btn(f"{G['doc']}  Host File", callback_data="host_upload", style="primary"),
    )
    kb.add(Btn(f"{G['folder']}  My Files", callback_data="host_files", style="primary"))
    # Row 6: Support
    kb.add(
        Btn(f"{G['chat']}  Support", callback_data="support", style="success"),
        Btn(f"{G['key']}  Help", callback_data="help", style="primary"),
    )
    # Row 7: Admin
    if status in ("owner", "admin"):
        kb.add(
            Btn(f"{G['crown']}  Admin Panel", callback_data="admin_panel", style="danger"),
        )
    return kb


def channel_dashboard_text(uid: int, channel_id: str) -> str:
    """Rich dashboard shown after picking a channel."""
    name = channel_title(uid, channel_id)
    active = default_channel_id(uid)
    all_jobs = user_jobs(uid, limit=200)
    pending = sum(1 for j in all_jobs if job_field(j, "status") in ("pending", "scheduled"))
    completed = sum(1 for j in all_jobs if job_field(j, "status") == "completed")
    failed = sum(1 for j in all_jobs if job_field(j, "status") == "failed")
    privacy = PRIVACY_LABELS[default_privacy(uid)]
    lines = [
        f"📺 <b>{escape(name)}</b>",
        f"{'✅ Active channel' if channel_id == active else '📺 Available channel'}",
        "",
        f"📊 Queue: ⏳ {pending} · ✅ {completed} · ❌ {failed} · 🔒 {privacy}",
        "",
        f"<b>What would you like to do?</b>",
    ]
    return "\n".join(lines)


def channel_dashboard_kb(uid: int, channel_id: str) -> types.InlineKeyboardMarkup:
    """Full dashboard shown after picking a channel."""
    kb = types.InlineKeyboardMarkup(row_width=2)
    # Upload section
    kb.add(
        Btn(f"📹  Upload Short", callback_data="upload_short", style="success"),
        Btn(f"🎬  Upload Video", callback_data="yt_upload_now", style="primary"),
    )
    kb.add(
        Btn(f"🔗  From Link", callback_data="social_menu", style="primary"),
        Btn(f"📦  Bulk Upload", callback_data="bulk_links", style="primary"),
    )
    # Management section
    kb.add(
        Btn(f"📋  My Videos", callback_data="manage_vids", style="primary"),
        Btn(f"📊  Analytics", callback_data="analytics", style="success"),
    )
    kb.add(
        Btn(f"📈  Quick Stats", callback_data=f"ch_stats:{channel_id}", style="primary"),
        Btn(f"📅  Queue & Schedule", callback_data="queue_menu", style="primary"),
    )
    # Settings
    kb.add(
        Btn(f"🔒  Default Privacy", callback_data="ch_set_priv", style="primary"),
        Btn(f"📝  Default Tags", callback_data="ch_set_tags", style="primary"),
    )
    kb.add(
        Btn(f"🎵  TikTok Auto", callback_data="tk_menu", style="primary"),
        Btn(f"📥  My Downloads", callback_data="dl_list:0", style="primary"),
    )
    kb.add(
        Btn(f"{G['back']}  Channels", callback_data="yt_channels", style="danger"),
        Btn(f"{G['back']}  Main Menu", callback_data="main_menu", style="danger"),
    )
    return kb


def upload_hub_text(uid: int) -> str:
    """Upload type selection screen."""
    channel = channel_title(uid, default_channel_id(uid))
    privacy = PRIVACY_LABELS[default_privacy(uid)]
    defaults = get_user_defaults(uid)
    tag_line = f"🏷 Default tags: <code>{escape(defaults['tags'][:50])}</code>" if defaults.get("tags") else ""
    lines = [
        f"📤 <b>Upload Hub</b>",
        f"━━━━━━━━━━━━━━━",
        f"📺 Target: <b>{escape(channel)}</b>",
        f"🔒 Privacy: {privacy}",
    ]
    if tag_line:
        lines.append(tag_line)
    lines += [
        "",
        "<b>Choose what to upload:</b>",
        "",
        "📹 <b>YouTube Short</b> — Under 60s, vertical, gets #shorts tag",
        "🎬 <b>Regular Video</b> — Standard YouTube upload",
        "🔗 <b>From Link</b> — Download from TikTok / Instagram / YouTube",
        "📦 <b>Bulk Upload</b> — Many links at once",
        "",
        "💡 <i>Tip: use a photo as a thumbnail for your next upload!</i>",
    ]
    return "\n".join(lines)


def upload_hub_kb(uid: int) -> types.InlineKeyboardMarkup:
    """Upload type selection keyboard."""
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        Btn(f"📹  YouTube Short", callback_data="upload_short", style="success"),
        Btn(f"🎬  Regular Video", callback_data="yt_upload_now", style="primary"),
    )
    kb.add(
        Btn(f"🔗  From Link", callback_data="social_menu", style="primary"),
        Btn(f"📦  Bulk Upload", callback_data="bulk_links", style="primary"),
    )
    kb.add(
        Btn(f"🔒  Default Privacy", callback_data="ch_set_priv", style="primary"),
        Btn(f"📋  Queue", callback_data="queue_menu", style="primary"),
    )
    kb.add(Btn(f"{G['back']}  Back", callback_data="main_menu", style="danger"))
    return kb


MANAGE_VIDS_PER_PAGE = 5


def manage_videos_text(uid: int, page: int = 0, status_filter: str = "") -> str:
    """Video management list."""
    statuses = [status_filter] if status_filter else None
    all_jobs = user_jobs(uid, statuses=statuses, limit=200)
    total = len(all_jobs)

    filter_labels = {
        "": "All",
        "pending": "⏳ Pending",
        "scheduled": "📅 Scheduled",
        "completed": "✅ Completed",
        "failed": "❌ Failed",
    }

    lines = [
        f"📋 <b>My Videos</b> ({total} total)",
        f"━━━━━━━━━━━━━━━",
        f"Filter: <b>{filter_labels.get(status_filter, 'All')}</b>",
        "",
    ]

    start = page * MANAGE_VIDS_PER_PAGE
    page_jobs = all_jobs[start:start + MANAGE_VIDS_PER_PAGE]

    for job in page_jobs:
        status = job_field(job, "status")
        job_id = job_field(job, "job_id")
        title = str(job_field(job, "title") or "Untitled")[:35]
        lines.append(f"{STATUS_LABELS.get(status, status)} <b>#{job_id}</b> {escape(title)}")

    if not page_jobs:
        lines.append("<i>Nothing here yet — upload a video to get started!</i>")
    elif total > MANAGE_VIDS_PER_PAGE:
        lines.append(f"\n📄 Page {page + 1} of {(total + MANAGE_VIDS_PER_PAGE - 1) // MANAGE_VIDS_PER_PAGE}")

    return "\n".join(lines)


def manage_videos_kb(uid: int, page: int = 0, status_filter: str = "") -> types.InlineKeyboardMarkup:
    """Video management keyboard."""
    kb = types.InlineKeyboardMarkup(row_width=1)
    statuses = [status_filter] if status_filter else None
    all_jobs = user_jobs(uid, statuses=statuses, limit=200)
    start = page * MANAGE_VIDS_PER_PAGE
    page_jobs = all_jobs[start:start + MANAGE_VIDS_PER_PAGE]

    for job in page_jobs:
        status = job_field(job, "status")
        job_id = job_field(job, "job_id")
        title = str(job_field(job, "title") or "Untitled")[:26]
        label = f"{STATUS_LABELS.get(status, status)} #{job_id} · {title}"
        kb.add(Btn(label, callback_data=f"vid_view:{job_id}", style="primary"))

    # Filter row
    filter_row = types.InlineKeyboardMarkup(row_width=4)
    for label, val in (("All", ""), ("⏳", "pending"), ("✅", "completed"), ("❌", "failed")):
        style = "success" if val == status_filter or (not status_filter and val == "") else "primary"
        filter_row.row(Btn(label, callback_data=f"manage_filter:{val}", style=style))
    kb.add(*filter_row.keyboard[0])

    # Pagination
    total = len(all_jobs)
    pages = max(1, (total + MANAGE_VIDS_PER_PAGE - 1) // MANAGE_VIDS_PER_PAGE)
    if pages > 1:
        nav = types.InlineKeyboardMarkup(row_width=2)
        if page > 0:
            nav.row(Btn("⬅️ Prev", callback_data=f"manage_vids:{page - 1}", style="primary"))
        if page < pages - 1:
            nav.row(Btn("Next ➡️", callback_data=f"manage_vids:{page + 1}", style="primary"))
        kb.add(*nav.keyboard[0])

    # Footer
    kb.add(
        Btn(f"{G['upload']}  Upload Hub", callback_data="upload_hub", style="success"),
        Btn(f"{G['queue']}  Queue", callback_data="queue_menu", style="primary"),
    )
    kb.add(Btn(f"{G['back']}  Main Menu", callback_data="main_menu", style="danger"))
    return kb


def yt_channel_kb(channels: list, uid: Optional[int] = None) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=1)
    active = default_channel_id(uid) if uid else ""
    for ch in channels:
        marker = "✅" if ch["id"] == active else "📺"
        kb.add(Btn(f"{marker} {ch['name']}", callback_data=f"yt_select:{ch['id']}", style="primary"))
    for ch in channels:
        kb.add(Btn(f"{G['eye']}  Stats · {ch['name'][:22]}",
                   callback_data=f"ch_stats:{ch['id']}", style="primary"))
    for ch in channels:
        kb.add(Btn(f"{G['trash']}  Disconnect · {ch['name'][:20]}",
                   callback_data=f"yt_disconnect:{ch['id']}", style="danger"))
    kb.add(Btn(f"{G['plus']}  Add Channel", callback_data="yt_connect", style="success"))
    kb.add(Btn(f"{G['back']}  Back", callback_data="main_menu", style="danger"))
    return kb


def job_actions_kb(job_id: int, status: str = "pending", user_id: Optional[int] = None) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    if status in ("pending", "scheduled", "failed"):
        kb.add(Btn(f"{G['upload']}  Upload now", callback_data=f"queue_now:{job_id}", style="success"))
        label = "🗓 Reschedule" if status == "scheduled" else "🗓 Schedule"
        kb.add(Btn(label, callback_data=f"queue_sched:{job_id}", style="primary"))
    if status == "completed":
        kb.add(Btn(f"{G['graph']}  Video stats", callback_data=f"ch_vstats:{job_id}", style="primary"))
    kb.add(
        Btn(f"{G['settings']}  Title", callback_data=f"yt_edit_title:{job_id}", style="primary"),
        Btn(f"{G['settings']}  Tags", callback_data=f"yt_edit_tags:{job_id}", style="primary"),
    )
    kb.add(
        Btn(f"{G['settings']}  Description", callback_data=f"yt_edit_desc:{job_id}", style="primary"),
        Btn(f"{G['lock']}  Privacy", callback_data=f"yt_edit_privacy:{job_id}", style="primary"),
    )
    kb.add(Btn(f"{G['spark']}  Suggest Tags", callback_data="suggest_tags", style="primary"))
    kb.add(Btn(f"{G['settings']}  Save as Default", callback_data="save_as_default", style="primary"))
    kb.add(Btn(f"{G['trash']}  Remove", callback_data=f"queue_del:{job_id}", style="danger"))
    kb.add(Btn(f"{G['queue']}  Queue", callback_data="queue_menu", style="primary"))
    kb.add(Btn(f"{G['back']}  Main Menu", callback_data="main_menu", style="danger"))
    return kb


def yt_metadata_kb(job_id: int) -> types.InlineKeyboardMarkup:
    return job_actions_kb(job_id, status="pending")


def privacy_kb(prefix: str) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=1)
    for value in ("private", "unlisted", "public"):
        style = "success" if value == "public" else ("primary" if value == "unlisted" else "danger")
        kb.add(Btn(f"{PRIVACY_LABELS[value]}", callback_data=f"{prefix}:{value}", style=style))
    kb.add(Btn(f"{G['back']}  Back", callback_data="main_menu", style="primary"))
    return kb


def social_kb() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(Btn(f"{G['download']}  Paste one link", callback_data="social_help", style="primary"))
    kb.add(Btn(f"{G['tiktok']}  Bulk: a TikTok account", callback_data="bulk_tiktok",
               style="success"))
    kb.add(Btn(f"{G['queue']}  Bulk: many links at once", callback_data="bulk_links",
               style="primary"))
    kb.add(Btn(f"{G['folder']}  My downloads", callback_data="dl_list:0", style="primary"))
    kb.add(Btn(f"{G['back']}  Main Menu", callback_data="main_menu", style="danger"))
    return kb


def downloads_kb(uid: int, page: int = 0) -> types.InlineKeyboardMarkup:
    rows = downloads_page(uid, page)
    kb = types.InlineKeyboardMarkup(row_width=1)
    for row in rows:
        dl_id, name, _path, size, _src, _kind, _created = row
        kb.add(Btn(f"⬇️ {name[:26]} · {human_size(size)}",
                   callback_data=f"dl_send:{dl_id}", style="primary"))
        kb.add(Btn(f"🗑 Remove", callback_data=f"dl_del:{dl_id}", style="danger"))
    if rows:
        kb.add(Btn(f"🗂 Zip this page & send", callback_data=f"dl_zip:{page}", style="success"))
        kb.add(Btn(f"🔗 Share download links", callback_data=f"dl_links:{page}", style="primary"))
    total = count_downloads(uid)
    per_page = DOWNLOADS_PER_PAGE
    if page > 0:
        kb.row(Btn("⬅️ Prev", callback_data=f"dl_list:{page - 1}", style="primary"))
    if (page + 1) * per_page < total:
        kb.row(Btn("Next ➡️", callback_data=f"dl_list:{page + 1}", style="primary"))
    kb.row(Btn(f"{G['download']}  Download more", callback_data="social_menu", style="success"))
    kb.row(Btn(f"{G['back']}  Main Menu", callback_data="main_menu", style="danger"))
    return kb


DOWNLOADS_PER_PAGE = 5


def downloads_page(uid: int, page: int = 0) -> List[tuple]:
    """One page of the user's saved downloads, skipping vanished files."""
    rows = user_downloads(uid, limit=200)
    alive = [row for row in rows if row[2] and Path(row[2]).exists()]
    start = max(0, page) * DOWNLOADS_PER_PAGE
    return alive[start:start + DOWNLOADS_PER_PAGE]


def queue_kb(uid: int) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=1)
    jobs = [j for j in user_jobs(uid, limit=40)
            if job_field(j, "status") in ("pending", "scheduled", "uploading", "failed")]
    for job in jobs[:10]:
        status = job_field(job, "status")
        label = STATUS_LABELS.get(status, status)
        kb.add(Btn(f"{label} · #{job_field(job, 'job_id')} {str(job_field(job, 'title'))[:28]}",
                   callback_data=f"queue_job:{job_field(job, 'job_id')}", style="primary"))
    if jobs:
        kb.add(Btn(f"{G['play']}  Upload all now", callback_data="queue_run_all", style="success"))
        kb.add(Btn(f"{G['trash']}  Clear finished & failed", callback_data="queue_clear", style="danger"))
    kb.add(Btn(f"{G['back']}  Main Menu", callback_data="main_menu", style="danger"))
    return kb


def tk_kb(uid: int) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=1)
    sources = user_sources(uid)
    for src in sources:
        state = "▶️" if src[3] else "⏸"
        kb.add(Btn(f"{state} @{src[1]} · {src[2][:18]}",
                   callback_data=f"tk_source:{src[0]}", style="primary"))
    kb.add(Btn(f"{G['plus']}  Add TikTok username / link", callback_data="tk_add", style="success"))
    if sources:
        kb.add(Btn(f"{G['refresh']}  Check all sources now", callback_data="tk_check_all", style="primary"))
    kb.add(Btn(f"{G['back']}  Main Menu", callback_data="main_menu", style="danger"))
    return kb


def tk_source_kb(source_id: int, enabled: bool) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    toggle = "⏸ Pause" if enabled else "▶️ Resume"
    kb.add(Btn(toggle, callback_data=f"tk_toggle:{source_id}",
               style="danger" if enabled else "success"))
    kb.add(Btn(f"{G['refresh']}  Check now", callback_data=f"tk_check:{source_id}", style="primary"))
    kb.add(
        Btn(f"{G['folder']}  Channel", callback_data=f"tk_setch:{source_id}", style="primary"),
        Btn(f"{G['lock']}  Privacy", callback_data=f"tk_setpriv:{source_id}", style="primary"),
    )
    kb.add(
        Btn(f"{G['plus']}  Batch size", callback_data=f"tk_setmax:{source_id}", style="primary"),
        Btn(f"{G['clock']}  Interval", callback_data=f"tk_setint:{source_id}", style="primary"),
    )
    kb.add(Btn(f"{G['trash']}  Remove source", callback_data=f"tk_del:{source_id}", style="danger"))
    kb.add(Btn(f"{G['back']}  Back", callback_data="tk_menu", style="primary"))
    return kb


def admin_kb() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    # Toggles row
    fj_on = get_setting("force_join_enabled", "0") == "1"
    pl_on = get_setting("plans_enabled", "0") == "1"
    fj_label = f"🔒 Force-Join: {'ON ✅' if fj_on else 'OFF ❌'}"
    pl_label = f"📋 Plans: {'ON ✅' if pl_on else 'OFF ❌'}"
    kb.add(
        Btn(fj_label, callback_data="adm_toggle_fj",
            style="success" if fj_on else "danger"),
        Btn(pl_label, callback_data="adm_toggle_plans",
            style="success" if pl_on else "danger"),
    )
    kb.add(
        Btn(f"{G['graph']}  Stats", callback_data="adm_stats", style="primary"),
        Btn(f"{G['user']}  Users", callback_data="adm_users:0", style="primary"),
    )
    kb.add(
        Btn(f"{G['broadcast']}  Broadcast", callback_data="adm_broadcast", style="success"),
        Btn(f"{G['ban']}  Ban / Unban", callback_data="adm_ban", style="danger"),
    )
    kb.add(
        Btn(f"🔒  Force-Join", callback_data="adm_forcejoin", style="primary"),
        Btn(f"📋  Plans", callback_data="adm_plans", style="primary"),
    )
    kb.add(
        Btn(f"📺  YT Channel", callback_data="adm_set_yt_channel", style="primary"),
        Btn(f"{G['star']}  Premium", callback_data="adm_premium", style="success"),
    )
    kb.add(
        Btn(f"{G['settings']}  Bot Settings", callback_data="adm_bot_settings", style="primary"),
        Btn(f"{G['eye']}  Audit Log", callback_data="adm_audit", style="primary"),
    )
    kb.add(
        Btn(f"{G['diag']}  Diagnostics", callback_data="adm_diag", style="primary"),
        Btn(f"{G['back']}  Main Menu", callback_data="main_menu", style="danger"),
    )
    return kb


def confirm_kb(yes_cb: str, no_cb: str = "main_menu") -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        Btn(f"{G['ok']}  Confirm", callback_data=yes_cb, style="success"),
        Btn(f"{G['no']}  Cancel", callback_data=no_cb, style="danger"),
    )
    return kb


def back_kb(target: str = "main_menu") -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    kb.add(Btn(f"{G['back']}  Back", callback_data=target, style="danger"))
    return kb


# ═════════════════════════════════════════════════════════════════
#  COMMAND HANDLERS
# ═════════════════════════════════════════════════════════════════

HELP_TEXT = (
    f"📖 <b>How to use {BRAND}</b>\n"
    f"━━━━━━━━━━━━━━━\n\n"
    "<b>1. Connect your YouTube channel</b>\n"
    "   /connect → tap Authorize → done. You can connect several.\n\n"
    "<b>2. Upload Shorts or Videos</b>\n"
    "   • 📷 Tap <b>Upload Hub</b> and pick Short or Video\n"
    "   • Send video files straight to this chat\n"
    "   • Shorts get <code>#shorts</code> automatically!\n\n"
    "<b>3. Import from the web</b>\n"
    "   • Paste a link (YouTube, TikTok, Instagram, X, Facebook…)\n"
    "   • /dl &lt;link&gt; to force a download\n"
    "   • /tiktok @username for automatic TikTok → YouTube\n\n"
    "<b>4. Manage everything</b>\n"
    "   • 📋 <b>My Videos</b> — browse, edit, delete uploads\n"
    "   • 🗓 Schedule: pick a job → send <code>+2h</code> or a date\n"
    "   • ✏️ Edit title, description, tags, privacy on each job\n"
    "   • 📥 Everything waits in your Queue until you publish\n\n"
    "<b>Commands</b>\n"
    "/start · /menu — main menu\n"
    "/connect — connect a YouTube channel\n"
    "/upload — upload hub (shorts + videos)\n"
    "/queue — pending &amp; scheduled uploads\n"
    "/dl &lt;link&gt; — download from a social link\n"
    "/tiktok — TikTok → YouTube auto posting\n"
    "/cancel — cancel the current input\n"
    "/ping · /status — check the bot\n\n"
    f"Need a hand? {escape(SUPPORT_USR)}"
)


def _require_channel(chat_id: int, uid: int) -> bool:
    """True when a channel exists, otherwise nudge the user to connect one."""
    if user_channels(uid):
        return True
    safe_send(chat_id,
              "⚠️ <b>No YouTube channel connected yet.</b>\n\n"
              "Connect one and I can upload, schedule and auto-post for you.",
              types.InlineKeyboardMarkup().add(
                  Btn(f"{G['play']}  Connect YouTube", callback_data="yt_connect", style="primary")))
    return False


@bot.message_handler(commands=['start', 'menu', 'help'])
def cmd_start(message):
    uid = message.from_user.id
    name = message.from_user.first_name or "User"
    is_new = not db_query('SELECT 1 FROM users WHERE user_id = ?', (uid,), fetch=True)
    ensure_user(uid, message.from_user.username or "")

    status = get_user_status(uid)
    files = get_file_count(uid)
    limit = get_file_limit(uid)
    channels = len(user_channels(uid))
    queued = len([j for j in user_jobs(uid, limit=200)
                  if job_field(j, "status") in ("pending", "scheduled", "uploading")])

    status_emoji = {"owner": "👑 Owner", "admin": "🛡️ Admin", "premium": "⭐ Premium", "free": "🆓 Free"}
    status_text = status_emoji.get(status, "🆓 Free")

    if is_new and uid != OWNER_ID:
        notify(OWNER_ID, f"🎉 New user!\n👤 {escape(name)}\n🆔 <code>{uid}</code>\n"
                          f"✳️ @{escape(message.from_user.username or 'N/A')}")

    text = (
        f"🎬 <b>Welcome to {BRAND}!</b>\n"
        f"━━━━━━━━━━━━━━━\n"
        f"👤 {escape(name)} · 🆔 <code>{uid}</code>\n"
        f"🔰 {status_text} · 📁 {files}/{limit}\n"
        f"📺 Channels: {channels} · ⏳ Queued: {queued}\n\n"
        f"<b>What I can do:</b>\n\n"
        f"📹 Upload <b>Shorts</b> and <b>Videos</b> to YouTube\n"
        f"🔗 Import from TikTok / Instagram / X / YouTube links\n"
        f"🎵 Auto-post a TikTok account to YouTube\n"
        f"📋 Queue, schedule and manage all your uploads\n"
        f"📦 Bulk download and upload many videos at once\n"
        f"🖥️ Host and run .py / .js scripts\n\n"
        f"👇 Pick an option below, or send /help for the full guide."
    )
    bot.send_message(message.chat.id, text, reply_markup=main_menu_kb(uid),
                     disable_web_page_preview=True)


@bot.message_handler(commands=['ping'])
def cmd_ping(message):
    start = time.time()
    msg = bot.reply_to(message, "Pong!")
    latency = round((time.time() - start) * 1000, 2)
    uptime = int(time.time() - START_TIME)
    bot.edit_message_text(f"🏓 Pong! Latency: {latency}ms · uptime {uptime // 3600}h"
                          f"{(uptime % 3600) // 60}m", message.chat.id, msg.message_id)


@bot.message_handler(commands=['status'])
def cmd_status(message):
    uid = message.from_user.id
    ensure_user(uid, message.from_user.username or "")
    status = get_user_status(uid)
    files = get_file_count(uid)
    limit = get_file_limit(uid)
    counts = ", ".join(f"{STATUS_LABELS.get(s, s)} {n}" for s, n in job_status_counts(uid)) or "no jobs yet"
    sources = user_sources(uid)

    text = (
        f"📊 <b>Your Status</b>\n\n"
        f"🔰 Level: {status.title()}\n"
        f"📁 Files: {files} / {limit}\n"
        f"📺 Channels: {len(user_channels(uid))}\n"
        f"🎬 Jobs: {counts}\n"
        f"🎵 TikTok sources: {len(sources)}\n"
        f"🆔 ID: <code>{uid}</code>"
    )
    bot.reply_to(message, text, disable_web_page_preview=True)


@bot.message_handler(commands=['cancel'])
def cmd_cancel(message):
    uid = message.from_user.id
    had = bool(get_state(uid))
    clear_state(uid)
    bot.reply_to(message, "✘ Cancelled." if had else "Nothing to cancel.")


@bot.message_handler(commands=['connect'])
def cmd_connect(message):
    start_connect_flow(message.chat.id, message.from_user.id)


@bot.message_handler(commands=['upload'])
def cmd_upload(message):
    prompt_for_video(message.chat.id, message.from_user.id)


@bot.message_handler(commands=['queue', 'schedule'])
def cmd_queue(message):
    uid = message.from_user.id
    ensure_user(uid, message.from_user.username or "")
    bot.send_message(message.chat.id, queue_text(uid), reply_markup=queue_kb(uid),
                     disable_web_page_preview=True)


@bot.message_handler(commands=['jobs'])
def cmd_jobs(message):
    send_jobs(message.chat.id, message.from_user.id)


@bot.message_handler(commands=['dl'])
def cmd_dl(message):
    uid = message.from_user.id
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "Usage: <code>/dl https://tiktok.com/@user/video/123</code>")
        return
    handle_link(uid, message.chat.id, parts[1].strip())


@bot.message_handler(commands=['tiktok', 'auto'])
def cmd_tiktok(message):
    uid = message.from_user.id
    ensure_user(uid, message.from_user.username or "")
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) > 1 and parts[1].strip():
        add_tiktok_source(uid, message.chat.id, parts[1].strip())
        return
    bot.send_message(message.chat.id, tiktok_menu_text(uid), reply_markup=tk_kb(uid),
                     disable_web_page_preview=True)


@bot.message_handler(commands=['diag'])
def cmd_diag(message):
    uid = message.from_user.id
    if not is_admin(uid):
        bot.reply_to(message, "⚠️ Admin only.")
        return
    bot.reply_to(message, diag_text(), disable_web_page_preview=True)


@bot.message_handler(commands=['oauthcheck', 'oauth_check', 'oauth-self-check'])
def cmd_oauth_check(message):
    """One tap: why is Connect YouTube failing? Available to every user."""
    bot.reply_to(message, oauth_self_check_html(), disable_web_page_preview=True)


@bot.message_handler(commands=['summary', 'health'])
def cmd_summary(message):
    """The same numbers the nightly summary sends, on demand."""
    uid = message.from_user.id
    # The owner watches the whole bot; everyone else sees only their own work.
    bot.reply_to(message, health_summary_html(0 if is_admin(uid) else uid),
                 disable_web_page_preview=True)


@bot.message_handler(commands=['mychannel', 'dashboard', 'ch'])
def cmd_mychannel(message):
    """Open the channel dashboard directly."""
    uid = message.from_user.id
    ensure_user(uid, message.from_user.username or "")
    ch = default_channel_id(uid)
    if not ch:
        channels = user_channels(uid)
        if channels:
            ch = channels[0][0]
            set_default_channel(uid, ch)
    if not ch:
        bot.reply_to(message,
                     "⚠️ <b>No YouTube channel connected.</b>\n\n"
                     "Connect one first — it takes 20 seconds.",
                     reply_markup=types.InlineKeyboardMarkup().add(
                         Btn(f"{G['play']}  Connect YouTube", callback_data="yt_connect",
                             style="success")))
        return
    bot.send_message(message.chat.id, channel_dashboard_text(uid, ch),
                     reply_markup=channel_dashboard_kb(uid, ch))


@bot.message_handler(commands=['plan'])
def cmd_plan(message):
    """Show user their current plan and limits."""
    uid = message.from_user.id
    ensure_user(uid, message.from_user.username or "")
    plan = get_user_plan(uid)
    limits = get_plan_limits(plan)
    info = DEFAULT_PLANS.get(plan, DEFAULT_PLANS["free"])
    plan_display = info.get("display", plan.title())

    # Count today's uploads
    today = datetime.utcnow().strftime("%Y-%m-%d")
    row = db_query('SELECT COUNT(*) FROM upload_jobs WHERE user_id = ? '
                   'AND created_at LIKE ?', (uid, f"{today}%"), fetch=True)
    used = row[0][0] if row else 0

    # Current usage
    channels = len(user_channels(uid))
    tk_sources = len(user_sources(uid))
    max_ch = plan_max_channels(uid)
    max_tk = plan_max_tiktok(uid)

    def check(feature):
        return "✅" if plan_has_feature(uid, feature) else "🔒"

    text = (
        f"📋 <b>Your Plan</b>\n"
        f"━━━━━━━━━━━━━━━\n\n"
        f"Current: <b>{plan_display}</b>\n\n"
        f"<b>YouTube Management:</b>\n"
        f"📤 Uploads today: {used}/{limits['uploads']}\n"
        f"💾 Max file: {limits['size']}MB\n"
        f"📺 Channels: {channels}/{max_ch}\n"
        f"🎵 TikTok sources: {tk_sources}/{max_tk}\n\n"
        f"<b>Features:</b>\n"
        f"{check('upload')} Upload videos\n"
        f"{check('schedule')} Schedule uploads\n"
        f"{check('tiktok')} TikTok auto-post\n"
        f"{check('analytics')} Analytics dashboard\n"
        f"{check('bulk')} Bulk upload\n"
        f"{check('priority')} Priority queue\n\n"
        f"<b>All Plans:</b>\n"
    )
    for name, pinfo in DEFAULT_PLANS.items():
        marker = " 👈" if name == plan else ""
        text += (f"  {pinfo['display']} — {pinfo['uploads']}/day, "
                 f"{pinfo['size']}MB, {pinfo['max_channels']} ch{marker}\n")

    text += ("\n💡 <i>Upgrade for more uploads, channels, and features! "
             "Contact an admin.</i>")
    bot.reply_to(message, text, disable_web_page_preview=True)


@bot.message_handler(commands=['analytics', 'stats'])
def cmd_analytics(message):
    """Show video analytics dashboard."""
    uid = message.from_user.id
    ensure_user(uid, message.from_user.username or "")
    if not user_channels(uid):
        bot.reply_to(message, "⚠️ Connect a YouTube channel first.",
                     reply_markup=types.InlineKeyboardMarkup().add(
                         Btn(f"{G['play']}  Connect YouTube", callback_data="yt_connect",
                             style="success")))
        return
    show_analytics_dashboard(message.chat.id, uid)


# ═════════════════════════════════════════════════════════════════
#  YOUTUBE CALLBACK HANDLERS
# ═════════════════════════════════════════════════════════════════

def channel_title(uid: int, channel_id: str) -> str:
    row = channel_row(uid, channel_id)
    return row[1] if row else (channel_id or "none")


def job_card_text(job: tuple) -> str:
    uid = int(job_field(job, "user_id"))
    status = job_field(job, "status")
    job_id = job_field(job, "job_id")
    lines = [
        f"📋 <b>Job #{job_id}</b> · {STATUS_LABELS.get(status, status)}",
        f"🎬 {escape(str(job_field(job, 'title') or 'Untitled')[:90])}",
        f"📺 {escape(channel_title(uid, job_field(job, 'channel_id')))} · "
        f"{PRIVACY_LABELS.get(job_field(job, 'privacy_status'), '')}",
        f"📥 Source: {SOURCE_LABELS.get(job_field(job, 'source'), '?')}",
    ]
    path = job_field(job, "file_path") or ""
    size = file_size(path)
    if size:
        lines.append(f"💾 {human_size(size)} on disk")
    if job_field(job, "scheduled_at"):
        lines.append(f"🗓 Starts: {fmt_when(job_field(job, 'scheduled_at'))}")
    if job_field(job, "publish_at"):
        lines.append(f"🌐 Publishes: {fmt_when(job_field(job, 'publish_at'))}")
    if status == "uploading":
        lines.append(f"📤 Progress: {int(job_field(job, 'progress') or 0)}%")
    if job_field(job, "video_id"):
        lines.append(f"🔗 https://youtu.be/{job_field(job, 'video_id')}")
    if job_field(job, "source_url"):
        lines.append(f"↗️ {escape(str(job_field(job, 'source_url'))[:120])}")
    desc = job_field(job, "description") or ""
    if desc:
        desc_preview = desc[:80] + ("..." if len(desc) > 80 else "")
        lines.append(f"📝 {escape(desc_preview)}")
    tags = job_field(job, "tags") or ""
    if tags:
        tag_list = tags.split(",")[:5]
        tag_display = " ".join(f"#{t.strip()}" for t in tag_list if t.strip())
        extra = len(tags.split(",")) - 5
        if extra > 0:
            tag_display += f" +{extra} more"
        lines.append(f"🏷️ {escape(tag_display)}")
    if job_field(job, "error_msg"):
        lines.append(f"⚠️ {escape(str(job_field(job, 'error_msg'))[:220])}")
    return "\n".join(lines)


def file_size(path: str) -> int:
    """Size of a file on disk, or 0 when it is missing/unreadable."""
    try:
        return Path(path).stat().st_size if path and Path(path).exists() else 0
    except OSError:
        return 0


def queue_text(uid: int) -> str:
    jobs = [j for j in user_jobs(uid, limit=40)
            if job_field(j, "status") in ("pending", "scheduled", "uploading", "failed")]
    if not jobs:
        return ("📋 <b>Queue is empty</b>\n\n"
                "Send video files or paste a link and they will show up here.")
    active = [j for j in jobs if job_field(j, "status") in ("pending", "uploading")]
    scheduled = [j for j in jobs if job_field(j, "status") == "scheduled"]
    failed = [j for j in jobs if job_field(j, "status") == "failed"]
    text = (f"📋 <b>Upload Queue</b>\n\n"
            f"⏳ Waiting: {len(active)} · 🗓 Scheduled: {len(scheduled)}"
            f" · ❌ Failed: {len(failed)}\n\n")
    for job in jobs[:10]:
        status = job_field(job, "status")
        text += (f"{STATUS_LABELS.get(status, status)} <b>#{job_field(job, 'job_id')}</b> "
                 f"{escape(str(job_field(job, 'title') or 'Untitled')[:40])}\n")
    text += "\nTap a job to edit, schedule or upload it now."
    return text


def send_jobs(chat_id: int, uid: int) -> None:
    jobs = user_jobs(uid, limit=10)
    if not jobs:
        safe_send(chat_id, "📋 <b>No uploads yet</b>\n\nSend a video or paste a link to get started.",
                  back_kb())
        return
    kb = types.InlineKeyboardMarkup(row_width=1)
    text = "📋 <b>Recent Uploads</b>\n\n"
    for job in jobs:
        status = job_field(job, "status")
        text += (f"{STATUS_LABELS.get(status, status)} <b>#{job_field(job, 'job_id')}</b> "
                 f"{escape(str(job_field(job, 'title') or 'Untitled')[:40])}\n")
        if job_field(job, "video_id"):
            text += f"    🔗 https://youtu.be/{job_field(job, 'video_id')}\n"
    for job in jobs[:6]:
        kb.add(Btn(f"#{job_field(job, 'job_id')} · {str(job_field(job, 'title'))[:26]}",
                   callback_data=f"queue_job:{job_field(job, 'job_id')}", style="primary"))
    kb.add(Btn(f"{G['queue']}  Queue", callback_data="queue_menu", style="primary"))
    kb.add(Btn(f"{G['back']}  Main Menu", callback_data="main_menu", style="danger"))
    safe_send(chat_id, text, kb)


def diag_text() -> str:
    """Which token/secrets this process is actually using."""
    try:
        me = bot.get_me()
        identity = f"@{me.username} (id {me.id})" if me else "unknown"
    except Exception as exc:
        identity = f"getMe failed: {exc}"

    try:
        from yt_dlp.version import __version__ as ytdlp_version
    except Exception:
        ytdlp_version = "not installed"

    env_token = os.environ.get("BOT_TOKEN", "")
    file_token = DOTENV_VALUES.get("BOT_TOKEN", "")
    mismatch = bool(file_token) and bool(env_token) and env_token != file_token

    lines = [
        "🧪 <b>Diagnostics</b>",
        f"🤖 Bot: {identity}",
        f"🔑 Active token: <code>{mask_secret(TOKEN)}</code>",
        f"📄 Source: {value_source(BOT_TOKEN_ENV_KEY)}",
        f"📁 .env loaded: {'yes' if DOTENV_LOADED else 'no'}",
    ]
    if mismatch:
        lines.append("⚠️ The hosting BOT_TOKEN differs from the .env value!")
        lines.append("   Set PREFER_ENV_FILE=1 or update the host variable.")
    lines += [
        f"👑 Owner id: <code>{OWNER_ID or 'unset'}</code> (from {value_source('OWNER_ID')})",
        f"🏷 Build: <code>{escape(build_marker())}</code>",
        f"⏱ Uptime: {int((time.time() - START_TIME) // 60)} min",
        f"📺 YouTube API libs: {'✅' if _YT_OK else '❌'}",
        f"🎵 yt-dlp: {ytdlp_version}",
        f"🎞 ffmpeg: {'found' if shutil.which('ffmpeg') else 'missing (720p max, no merges)'}",
        f"🍪 Cookies file: <code>{escape(SOCIAL_COOKIES_FILE)}</code> "
        f"({'found' if os.path.isfile(SOCIAL_COOKIES_FILE) else 'missing'})",
        f"🔗 OAuth redirect: <code>{escape(YT_REDIRECT_URI or 'not set')}</code>",
        f"🌍 Public base URL: <code>{escape(PUBLIC_BASE_URL or YT_REDIRECT_URI or 'not set')}</code>",
        f"👥 Users: {count_users()} · 📺 Channels: "
        f"{db_query('SELECT COUNT(*) FROM youtube_channels', fetch=True)[0][0]}",
        f"🎬 Jobs: {length_or_zero(db_query('SELECT COUNT(*) FROM upload_jobs', fetch=True))}",
        f"🎵 TikTok sources: {db_query('SELECT COUNT(*) FROM tiktok_sources', fetch=True)[0][0]}",
    ]
    return "\n".join(lines)


def length_or_zero(rows) -> int:
    return rows[0][0] if rows else 0


def start_connect_flow(chat_id: int, uid: int) -> None:
    if not _YT_OK or not yt_service:
        safe_send(chat_id,
                  "⚠️ <b>YouTube API not configured.</b>\n\n"
                  "Set <code>YOUTUBE_CLIENT_ID</code> and <code>YOUTUBE_CLIENT_SECRET</code>, "
                  "then run <code>/diag</code>.")
        return
    if not YT_CLIENT_ID or not YT_CLIENT_SECRET:
        safe_send(chat_id,
                  "⚠️ <b>Missing YouTube OAuth credentials.</b>\n\n"
                  "Add <code>YOUTUBE_CLIENT_ID</code> and <code>YOUTUBE_CLIENT_SECRET</code> to "
                  "your environment, then try again.")
        return

    state = secrets.token_urlsafe(16)
    db_query('INSERT OR REPLACE INTO oauth_states (state, user_id, chat_id, created_at) '
             'VALUES (?, ?, ?, ?)', (state, uid, chat_id, datetime.utcnow().isoformat()))
    set_state(uid, "yt_auth_pending", oauth_state=state)
    auth_url = yt_service.get_auth_url(state)

    kb = types.InlineKeyboardMarkup(row_width=1)
    reachable, why = oauth_callback_reachable()
    if not reachable:
        # The browser flow would dead-end on a page that never loads, so lead with
        # the flow that does work and say why the other one cannot.
        kb.add(Btn(f"{G['queue']}  Paste Code Manually", callback_data="yt_paste_code",
                   style="primary"))
        kb.add(Btn("🔐 Try the browser flow anyway", url=auth_url, style="primary"))
        kb.add(Btn("🔍 Self-check: why is this failing?", callback_data="oauth_selfcheck",
                   style="success"))
        kb.add(Btn(f"{G['back']}  Back", callback_data="main_menu", style="danger"))
        safe_send(chat_id,
                  "📺 <b>Connect YouTube Channel</b>\n\n"
                  f"⚠️ The automatic redirect cannot work here: {escape(why)}\n\n"
                  "Use <b>Paste Code Manually</b> — it links the same channel without "
                  "needing a public URL:\n"
                  "1. Tap it, then tap <b>Open Google Auth</b>\n"
                  "2. Approve access\n"
                  "3. The page will fail to load — that is expected\n"
                  "4. Copy the full URL from the address bar and paste it here",
                  kb)
        return

    kb.add(Btn("🔐 Authorize YouTube in browser", url=auth_url, style="primary"))
    kb.add(Btn(f"{G['queue']}  Paste Code Manually", callback_data="yt_paste_code", style="primary"))
    kb.add(Btn("🔍 Self-check: why is this failing?", callback_data="oauth_selfcheck",
               style="success"))
    kb.add(Btn(f"{G['back']}  Back", callback_data="main_menu", style="danger"))
    safe_send(chat_id,
              "📺 <b>Connect YouTube Channel</b>\n\n"
              "1. Tap the blue button\n"
              "2. Pick the Google account that owns the channel\n"
              "3. Allow access — you'll land on a confirmation page\n\n"
              f"ℹ️ The redirect URL for this bot is\n<code>{escape(YT_REDIRECT_URI)}</code>\n"
              "It must be listed in Google Cloud → APIs &amp; Services → Credentials → "
              "Authorized redirect URIs.\n\n"
              "📋 <b>Hosting on KataBump, Pterodactyl, or no HTTPS?</b>\n"
              "If the redirect page fails to load, tap <b>Paste Code Manually</b> below, "
              "copy the URL from your browser's address bar, and paste it here.",
              kb)


def prompt_for_video(chat_id: int, uid: int) -> None:
    """Show the upload hub so the user picks the upload type first."""
    if not _require_channel(chat_id, uid):
        return
    safe_send(chat_id, upload_hub_text(uid), upload_hub_kb(uid))


def prompt_for_short(chat_id: int, uid: int) -> None:
    """Prompt for a YouTube Short upload."""
    if not _require_channel(chat_id, uid):
        return
    set_state(uid, "waiting_short")
    safe_send(chat_id,
              "📹 <b>Upload a YouTube Short</b>\n\n"
              "Send me a video file (under 60 seconds, ideally 9:16 vertical)\n"
              "and I will add the <code>#shorts</code> tag automatically.\n\n"
              "• Add a caption to use as the title\n"
              "• I will mark it as a Short for the algorithm\n"
              "• You can edit title/tags/description after\n\n"
              "⚠️ Telegram limits bot downloads to 20MB."
              " For bigger files, paste the link instead.",
              back_kb("upload_hub"))


@bot.callback_query_handler(func=lambda c: c.data == "main_menu")
def cb_main_menu(call):
    uid = call.from_user.id
    if is_banned(uid):
        safe_answer(call, "You are blocked from using this bot.", alert=True)
        return
    safe_answer(call)
    clear_state(uid)
    # Force-join check
    if not check_force_join(uid, call.message.chat.id):
        return
    # Upload limit check
    allowed, msg = check_upload_limit(uid)
    plan = get_user_plan(uid)
    plan_display = DEFAULT_PLANS.get(plan, {}).get("display", plan.title())
    text = f"🎬 <b>{BRAND}</b>\n\n{plan_display} · {msg}\n\nChoose an option:"
    safe_edit(call, text, main_menu_kb(uid))


@bot.callback_query_handler(func=lambda c: c.data == "yt_connect")
def cb_yt_connect(call):
    uid = call.from_user.id
    safe_answer(call)
    if is_banned(uid):
        safe_answer(call, "You are blocked from using this bot.", alert=True)
        return
    start_connect_flow(call.message.chat.id, uid)


@bot.callback_query_handler(func=lambda c: c.data == "yt_paste_code")
def cb_yt_paste_code(call):
    """Generate a fresh auth URL for manual code paste.

    Key: the state token is NOT stored in oauth_states, so the Flask
    callback on the server can't intercept the redirect.  When Google
    redirects to localhost the server's Flask fires but finds no state
    and returns "Link expired" WITHOUT consuming the code.  The user
    then pastes the URL here and the code is still valid.
    """
    uid = call.from_user.id
    safe_answer(call)
    if is_banned(uid):
        safe_answer(call, "You are blocked from using this bot.", alert=True)
        return
    if not _YT_OK or not yt_service:
        safe_answer(call, "\u274c YouTube API not configured.", alert=True)
        return
    if not YT_CLIENT_ID or not YT_CLIENT_SECRET:
        safe_answer(call, "\u274c Missing YouTube OAuth credentials.", alert=True)
        return

    # Generate a fresh state — deliberately NOT stored in oauth_states.
    # The Flask callback will see an unknown state and bail out without
    # exchanging the code, leaving it fresh for the paste handler.
    fresh_state = secrets.token_urlsafe(16)
    auth_url = yt_service.get_auth_url(fresh_state)

    set_state(uid, "yt_paste_code")
    safe_edit(call,
              "\U0001f4cb <b>Manual YouTube Authorization</b>\n\n"
              "Step 1: Tap <b>Open Google Auth</b> below\n"
              "Step 2: Sign in and allow access\n"
              "Step 3: The page may fail to load — <b>that's expected</b>\n"
              "Step 4: <b>Copy the full URL</b> from the browser address bar\n"
              "Step 5: Paste it back here\n\n"
              "<i>\u26a0\ufe0f This generates a fresh link so your server\'s callback\n"
              "can't intercept it. The code stays valid for you to paste.</i>",
              types.InlineKeyboardMarkup(row_width=1)
              .add(Btn("\U0001f517  Open Google Auth", url=auth_url, style="primary"))
              .add(Btn(f"{G['back']}  Back", callback_data="main_menu", style="danger")))


@bot.callback_query_handler(func=lambda c: c.data == "oauth_selfcheck")
def cb_oauth_selfcheck(call):
    """Answer first, then report: the live Google probe takes longer than
    Telegram's callback timeout, and an unanswered press just spins."""
    safe_answer(call, "Running the check…")
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(Btn(f"{G['play']}  Connect YouTube", callback_data="yt_connect", style="primary"))
    kb.add(Btn(f"{G['back']}  Main Menu", callback_data="main_menu", style="danger"))
    safe_send(call.message.chat.id, oauth_self_check_html(), kb)


@bot.callback_query_handler(func=lambda c: c.data == "yt_channels")
def cb_yt_channels(call):
    uid = call.from_user.id
    safe_answer(call)
    channels = user_channels(uid)

    if not channels:
        safe_edit(call,
                  "📺 <b>No connected channels</b>\n\n"
                  "Connect a YouTube channel and the bot can upload, schedule and "
                  "auto-post for you.",
                  types.InlineKeyboardMarkup(row_width=1)
                  .add(Btn(f"{G['play']}  Connect YouTube", callback_data="yt_connect",
                           style="success"))
                  .add(Btn(f"{G['back']}  Back", callback_data="main_menu", style="danger")))
        return

    ch_list = [{"id": c[0], "name": c[1]} for c in channels]
    active = default_channel_id(uid)
    text = (f"📺 <b>Your YouTube Channels</b> ({len(ch_list)})\n\n"
            f"✅ Active: <b>{escape(channel_title(uid, active))}</b>\n\n"
            "Tap a channel to make it the upload target.")
    safe_edit(call, text, yt_channel_kb(ch_list, uid))


@bot.callback_query_handler(func=lambda c: c.data.startswith("yt_select:"))
def cb_yt_select(call):
    uid = call.from_user.id
    channel_id = call.data.split(":", 1)[1]
    if not channel_row(uid, channel_id):
        safe_answer(call, "Channel not found — reconnect it.", alert=True)
        return
    set_default_channel(uid, channel_id)
    safe_answer(call, f"Active channel: {channel_title(uid, channel_id)}")
    safe_edit(call, channel_dashboard_text(uid, channel_id),
              channel_dashboard_kb(uid, channel_id))


@bot.callback_query_handler(func=lambda c: c.data.startswith("yt_disconnect:"))
def cb_yt_disconnect(call):
    """Confirm before forgetting a channel."""
    uid = call.from_user.id
    channel_id = call.data.split(":", 1)[1]
    if not channel_row(uid, channel_id):
        safe_answer(call, "Channel not found.", alert=True)
        return
    safe_answer(call)
    safe_edit(call,
              f"⚠️ <b>Disconnect {escape(channel_title(uid, channel_id))}?</b>\n\n"
              "The bot forgets the tokens and revokes access at Google.\n"
              "Queued uploads for this channel will fail until you reconnect it.",
              types.InlineKeyboardMarkup(row_width=1)
              .add(Btn(f"{G['trash']}  Yes, disconnect",
                       callback_data=f"yt_disconnect_yes:{channel_id}", style="danger"))
              .add(Btn(f"{G['back']}  Keep it", callback_data="yt_channels",
                       style="primary")))


@bot.callback_query_handler(func=lambda c: c.data.startswith("yt_disconnect_yes:"))
def cb_yt_disconnect_yes(call):
    """Delete the row and revoke the refresh token at Google."""
    uid = call.from_user.id
    channel_id = call.data.split(":", 1)[1]
    name = channel_title(uid, channel_id)
    if disconnect_channel(uid, channel_id):
        safe_answer(call, f"Disconnected {name}")
    else:
        safe_answer(call, "Channel already removed.", alert=True)

    channels = [{"id": c[0], "name": c[1]} for c in user_channels(uid)]
    if channels:
        safe_edit(call,
                  f"✅ <b>{escape(name)}</b> disconnected.\n\n"
                  f"📺 <b>Your YouTube Channels</b> ({len(channels)})",
                  yt_channel_kb(channels, uid))
    else:
        safe_edit(call,
                  f"✅ <b>{escape(name)}</b> disconnected.\n\n"
                  "No channels connected. Tap below to link one.",
                  types.InlineKeyboardMarkup(row_width=1)
                  .add(Btn(f"{G['play']}  Connect YouTube", callback_data="yt_connect",
                           style="success"))
                  .add(Btn(f"{G['back']}  Back", callback_data="main_menu",
                           style="danger")))


@bot.callback_query_handler(func=lambda c: c.data.startswith("ch_stats:"))
def cb_channel_stats(call):
    uid = call.from_user.id
    channel_id = call.data.split(":", 1)[1]
    safe_answer(call, "Fetching channel stats…")

    if not _YT_OK or not yt_service:
        safe_answer(call, "YouTube API not configured.", alert=True)
        return

    def worker():
        try:
            credentials = yt_service.credentials_for_channel(uid, channel_id)
            client = build("youtube", "v3", credentials=credentials)
            resp = client.channels().list(part="snippet,statistics", mine=True).execute()
            item = resp["items"][0]
            stats = item.get("statistics", {})
            text = (f"📺 <b>{escape(item['snippet']['title'])}</b>\n\n"
                    f"👥 Subscribers: {stats.get('subscriberCount', 'hidden')}\n"
                    f"🎬 Videos: {stats.get('videoCount', '?')}\n"
                    f"👀 Views: {stats.get('viewCount', '?')}\n"
                    f"🆔 <code>{escape(channel_id)}</code>")
        except Exception as exc:
            text = f"❌ Could not load stats: <code>{escape(str(exc)[:300])}</code>"
        safe_send(call.message.chat.id, text, back_kb("yt_channels"))

    threading.Thread(target=worker, daemon=True).start()


@bot.callback_query_handler(func=lambda c: c.data in ("yt_upload", "yt_upload_now"))
def cb_yt_upload(call):
    """Upload regular video — prompt to send a file."""
    uid = call.from_user.id
    safe_answer(call)
    if is_banned(uid):
        safe_answer(call, "You are blocked from using this bot.", alert=True)
        return
    if not user_channels(uid):
        safe_edit(call,
                  "⚠️ <b>No YouTube channel connected.</b>\n\n"
                  "Connect one first — it takes 20 seconds.",
                  types.InlineKeyboardMarkup(row_width=1)
                  .add(Btn(f"{G['play']}  Connect YouTube", callback_data="yt_connect",
                           style="success"))
                  .add(Btn(f"{G['back']}  Back", callback_data="main_menu", style="danger")))
        return
    set_state(uid, "waiting_video")
    safe_edit(call,
              "🎬 <b>Upload a Regular Video</b>\n\n"
              "Send me a video file and I will queue it for YouTube.\n\n"
              "• Add a <b>caption</b> to set the title\n"
              "• Send multiple files — each becomes a job\n"
              "• You can edit title, tags, description before upload\n\n"
              "⚠️ Telegram limits bot downloads to 20MB."
              " For bigger files, paste the link instead.",
              back_kb("upload_hub"))


@bot.callback_query_handler(func=lambda c: c.data == "yt_jobs")
def cb_yt_jobs(call):
    safe_answer(call)
    send_jobs(call.message.chat.id, call.from_user.id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("yt_confirm:"))
def cb_yt_confirm(call):
    """Start a real upload of a queued job."""
    uid = call.from_user.id
    job_id = int(call.data.split(":", 1)[1])

    job = get_job(job_id, uid)
    if not job:
        safe_answer(call, "Job not found.", alert=True)
        return

    if not default_channel_id(uid):
        safe_answer(call, "No channel connected.", alert=True)
        return

    if not job_field(job, "channel_id"):
        db_query('UPDATE upload_jobs SET channel_id = ? WHERE job_id = ?',
                 (default_channel_id(uid), job_id))

    db_query('UPDATE upload_jobs SET status = "pending" WHERE job_id = ?', (job_id,))
    safe_answer(call, "Queued — uploading now!")
    safe_edit(call,
              f"⏳ <b>Uploading job #{job_id}…</b>\n\n"
              f"🎬 {escape(str(job_field(job, 'title'))[:80])}\n"
              f"📺 {escape(channel_title(uid, job_field(job, 'channel_id') or default_channel_id(uid)))}\n\n"
              "I'll message you when it's live.",
              back_kb("queue_menu"))


@bot.callback_query_handler(func=lambda c: c.data == "yt_cancel")
def cb_yt_cancel(call):
    clear_state(call.from_user.id)
    safe_answer(call, "Cancelled")
    safe_edit(call, f"🎬 <b>{BRAND}</b>\n\nCancelled — nothing was uploaded.", main_menu_kb(call.from_user.id))


# ═════════════════════════════════════════════════════════════════
#  QUEUE · SCHEDULING · METADATA
# ═════════════════════════════════════════════════════════════════

def default_privacy(uid: int) -> str:
    row = db_query('SELECT default_privacy FROM users WHERE user_id = ?', (uid,), fetch=True)
    value = (row[0][0] if row and row[0][0] else "") or "private"
    return value if value in PRIVACY_LABELS else "private"


@bot.callback_query_handler(func=lambda c: c.data == "queue_menu")
def cb_queue_menu(call):
    uid = call.from_user.id
    safe_answer(call)
    safe_edit(call, queue_text(uid), queue_kb(uid))


@bot.callback_query_handler(func=lambda c: c.data.startswith("queue_job:"))
def cb_queue_job(call):
    uid = call.from_user.id
    job_id = int(call.data.split(":", 1)[1])
    job = get_job(job_id, uid)
    if not job:
        safe_answer(call, "Job not found.", alert=True)
        return
    safe_answer(call)
    safe_edit(call, job_card_text(job), job_actions_kb(job_id, job_field(job, "status")))


@bot.callback_query_handler(func=lambda c: c.data.startswith("queue_now:"))
def cb_queue_now(call):
    uid = call.from_user.id
    job_id = int(call.data.split(":", 1)[1])
    job = get_job(job_id, uid)
    if not job:
        safe_answer(call, "Job not found.", alert=True)
        return
    if not default_channel_id(uid):
        safe_answer(call, "Connect a YouTube channel first.", alert=True)
        return
    if not job_field(job, "channel_id"):
        db_query('UPDATE upload_jobs SET channel_id = ? WHERE job_id = ?',
                 (default_channel_id(uid), job_id))
    if not file_size(job_field(job, "file_path")):
        safe_answer(call, "Video file is missing on disk.", alert=True)
        return
    db_query('UPDATE upload_jobs SET status = "pending", scheduled_at = NULL, publish_at = NULL '
             'WHERE job_id = ?', (job_id,))
    safe_answer(call, "🚀 Queued for upload")
    safe_edit(call, job_card_text(get_job(job_id, uid) or job), job_actions_kb(job_id, "pending"))


@bot.callback_query_handler(func=lambda c: c.data.startswith("queue_sched:"))
def cb_queue_sched(call):
    uid = call.from_user.id
    job_id = int(call.data.split(":", 1)[1])
    if not get_job(job_id, uid):
        safe_answer(call, "Job not found.", alert=True)
        return
    safe_answer(call, "Pick a time")
    set_state(uid, "queue_sched", job_id=job_id)
    kb = types.InlineKeyboardMarkup(row_width=3)
    for label, minutes in (("+1h", 60), ("+6h", 360), ("+1d", 1440)):
        kb.add(Btn(label, callback_data=f"sch_quick:{job_id}:{minutes}", style="primary"))
    kb.add(Btn(f"{G['no']}  Cancel", callback_data=f"queue_job:{job_id}", style="danger"))
    safe_edit(call,
              f"🗓 <b>Schedule job #{job_id}</b>\n\n"
              "Quick options below, or send me a time:\n"
              "• <code>+90m</code>, <code>+3h</code>, <code>+2d</code>\n"
              "• <code>2026-09-20 18:30</code> (UTC)",
              kb)


@bot.callback_query_handler(func=lambda c: c.data.startswith("sch_quick:"))
def cb_schedule_quick(call):
    uid = call.from_user.id
    _, job_id, minutes = call.data.split(":")
    job_id, minutes = int(job_id), int(minutes)
    if not get_job(job_id, uid):
        safe_answer(call, "Job not found.", alert=True)
        return
    when = datetime.utcnow() + timedelta(minutes=minutes)
    apply_schedule(uid, job_id, when)
    safe_answer(call, f"Scheduled for {fmt_when(when.isoformat())}")
    safe_edit(call, job_card_text(get_job(job_id, uid)), job_actions_kb(job_id, "scheduled"))
    clear_state(uid)


def apply_schedule(uid: int, job_id: int, when: datetime) -> None:
    """Persist a schedule. YouTube gets a native public publish time too."""
    delay = (when - datetime.utcnow()).total_seconds()
    publish_at = when.isoformat() if delay > 900 else None
    db_query('UPDATE upload_jobs SET status = "scheduled", scheduled_at = ?, publish_at = ?, '
             'error_msg = NULL WHERE job_id = ?', (when.isoformat(), publish_at, job_id))
    audit(uid, "job_scheduled", f"job={job_id} at={when.isoformat()}")


@bot.callback_query_handler(func=lambda c: c.data.startswith("queue_del:"))
def cb_queue_del(call):
    uid = call.from_user.id
    job_id = int(call.data.split(":", 1)[1])
    job = get_job(job_id, uid)
    if not job:
        safe_answer(call, "Already gone.", alert=True)
        return
    path = job_field(job, "file_path") or ""
    if path and str(path).startswith(str(STORAGE_DIR)) and Path(path).exists():
        try:
            Path(path).unlink()
        except OSError:
            pass
    db_query('DELETE FROM upload_jobs WHERE job_id = ?', (job_id,))
    audit(uid, "job_deleted", str(job_id))
    safe_answer(call, "Removed")
    safe_edit(call, queue_text(uid), queue_kb(uid))


@bot.callback_query_handler(func=lambda c: c.data == "queue_run_all")
def cb_queue_run_all(call):
    uid = call.from_user.id
    if not default_channel_id(uid):
        safe_answer(call, "Connect a YouTube channel first.", alert=True)
        return
    jobs = [j for j in user_jobs(uid, limit=100)
            if job_field(j, "status") in ("pending", "scheduled", "failed")
            and file_size(job_field(j, "file_path"))]
    for job in jobs:
        job_id = int(job_field(job, "job_id"))
        channel = job_field(job, "channel_id") or default_channel_id(uid)
        db_query('UPDATE upload_jobs SET status = "pending", channel_id = ?, scheduled_at = NULL, '
                 'publish_at = NULL, error_msg = NULL WHERE job_id = ?', (channel, job_id))
    safe_answer(call, f"{len(jobs)} job(s) queued")
    safe_edit(call, queue_text(uid), queue_kb(uid))


@bot.callback_query_handler(func=lambda c: c.data == "queue_clear")
def cb_queue_clear(call):
    uid = call.from_user.id
    removed = 0
    for job in user_jobs(uid, limit=200):
        if job_field(job, "status") in ("completed", "failed"):
            path = job_field(job, "file_path") or ""
            if path and str(path).startswith(str(STORAGE_DIR)) and Path(path).exists():
                try:
                    Path(path).unlink()
                except OSError:
                    pass
            db_query('DELETE FROM upload_jobs WHERE job_id = ?', (job_field(job, "job_id"),))
            removed += 1
    safe_answer(call, f"Cleared {removed} job(s)")
    safe_edit(call, queue_text(uid), queue_kb(uid))


# ── Metadata editing ────────────────────────────────────────────────

EDIT_PROMPTS = {
    "title": ("📝 <b>Send the new title</b> (max 100 chars)", "yt_edit_title"),
    "desc": ("📝 <b>Send the new description</b>\n\nTip: add links, hashtags and a call to action.",
             "yt_edit_desc"),
    "tags": ("🏷 <b>Send tags</b> separated by commas or spaces (max 15)", "yt_edit_tags"),
}


@bot.callback_query_handler(func=lambda c: c.data.startswith("yt_edit_"))
def cb_edit_field(call):
    uid = call.from_user.id
    field, _, job_id_raw = call.data.partition(":")
    field = field.replace("yt_edit_", "")
    job_id = int(job_id_raw or 0)
    job = get_job(job_id, uid)
    if not job:
        safe_answer(call, "Job not found.", alert=True)
        return

    if field == "privacy":
        safe_answer(call, "Pick a privacy level")
        safe_edit(call, f"🔒 <b>Privacy for job #{job_id}</b>",
                  privacy_kb(f"ytset_priv_{job_id}"))
        return

    prompt, state_name = EDIT_PROMPTS.get(field, ("", ""))
    if not prompt:
        safe_answer(call, "Unknown field.", alert=True)
        return
    set_state(uid, state_name, job_id=job_id)
    safe_answer(call)
    safe_edit(call, f"{prompt}\n\n<i>Send /cancel to abort.</i>",
              types.InlineKeyboardMarkup().add(
                  Btn(f"{G['no']}  Cancel", callback_data=f"queue_job:{job_id}", style="danger")))


@bot.callback_query_handler(func=lambda c: c.data.startswith("ytset_priv_"))
def cb_job_privacy(call):
    uid = call.from_user.id
    prefix, value = call.data.rsplit(":", 1)
    job_id = int(prefix.rsplit("_", 1)[1])
    if value not in PRIVACY_LABELS or not get_job(job_id, uid):
        safe_answer(call, "Pick a valid privacy level.", alert=True)
        return
    db_query('UPDATE upload_jobs SET privacy_status = ? WHERE job_id = ?', (value, job_id))
    safe_answer(call, f"Privacy: {value}")
    safe_edit(call, job_card_text(get_job(job_id, uid)),
              job_actions_kb(job_id, job_field(get_job(job_id, uid), "status")))


@bot.callback_query_handler(func=lambda c: c.data.startswith("ch_vstats:"))
def cb_video_stats(call):
    uid = call.from_user.id
    job_id = int(call.data.split(":", 1)[1])
    job = get_job(job_id, uid)
    video_id = job_field(job or (), "video_id")
    channel_id = job_field(job or (), "channel_id") or default_channel_id(uid)
    if not video_id or not _YT_OK:
        safe_answer(call, "No video to inspect yet.", alert=True)
        return
    safe_answer(call, "Fetching stats…")

    def worker():
        try:
            credentials = yt_service.credentials_for_channel(uid, channel_id)
            stats = yt_service.video_stats(credentials, video_id)
            text = (f"📊 <b>Video stats</b>\n\n"
                    f"🎬 {escape(str(job_field(job, 'title'))[:80])}\n"
                    f"👀 Views: {stats.get('views', '?')}\n"
                    f"👍 Likes: {stats.get('likes', '?')}\n"
                    f"💬 Comments: {stats.get('comments', '?')}\n"
                    f"🔒 {stats.get('privacy', '?')}\n"
                    f"🔗 https://youtu.be/{video_id}")
        except Exception as exc:
            text = f"❌ {escape(str(exc)[:300])}"
        safe_send(call.message.chat.id, text, back_kb(f"queue_job:{job_id}"))

    threading.Thread(target=worker, daemon=True).start()


# ═════════════════════════════════════════════════════════════════
#  SOCIAL DOWNLOAD HANDLERS
# ═════════════════════════════════════════════════════════════════

def handle_link(uid: int, chat_id: int, raw: str) -> None:
    """Inspect a pasted link and offer to download + queue it."""
    url = extract_url(raw) or raw.strip()
    if not url.lower().startswith("http"):
        safe_send(chat_id, "❌ I need a full link starting with http:// or https://")
        return
    if not _YTDLP_OK:
        safe_send(chat_id,
                  "❌ <b>Link downloads are unavailable.</b>\n\n"
                  "The <code>yt-dlp</code> package is missing. Install it with "
                  "<code>pip install yt-dlp</code> and restart, or run without "
                  "<code>SKIP_AUTO_INSTALL</code> so the bot installs it itself.")
        return
    if not is_supported_url(url):
        safe_send(chat_id,
                  "❌ That site isn't supported yet.\n\n"
                  "Works with YouTube, TikTok, Instagram, X/Twitter, Facebook, "
                  "Reddit, Vimeo, Twitch, Pinterest and more.")
        return

    msg = bot.send_message(chat_id, "🔍 Reading the link…")

    def probe():
        try:
            info = probe_media(url)
        except Exception as exc:
            try:
                bot.edit_message_text(f"❌ Could not read that link:\n<code>{escape(str(exc)[:300])}</code>",
                                      chat_id, msg.message_id)
            except Exception:
                pass
            return

        set_state(uid, "dl_confirm", url=url, info=info)
        minutes = (info.get("duration") or 0) // 60
        text = (f"📥 <b>Ready to download</b>\n\n"
                f"🎬 {escape(str(info['title'])[:90])}\n"
                f"👤 {escape(str(info['uploader'])[:60])}\n"
                f"⏱ {minutes}:{(info.get('duration') or 0) % 60:02d} · "
                f"🌐 {escape(str(info['extractor']))}\n"
                + (f"📚 Playlist of {info['playlist_size']} — first item will be used\n"
                   if info.get("playlist_size", 0) > 1 else "")
                + f"\n📺 Target: {escape(channel_title(uid, default_channel_id(uid)) or 'no channel yet')}\n"
                  f"🔒 Privacy: {PRIVACY_LABELS[default_privacy(uid)]}")
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(Btn(f"{G['download']}  Download only (no upload)", callback_data="dl_only",
                   style="success"))
        if user_channels(uid):
            kb.add(Btn(f"{G['upload']}  Download & queue for YouTube", callback_data="dl_go",
                       style="primary"))
        kb.add(Btn(f"{G['lock']}  Privacy", callback_data="dl_privacy", style="primary"))
        kb.add(Btn(f"{G['no']}  Cancel", callback_data="dl_cancel", style="danger"))
        try:
            bot.edit_message_text(text, chat_id, msg.message_id, reply_markup=kb,
                                  disable_web_page_preview=True)
        except Exception:
            pass

    threading.Thread(target=probe, daemon=True).start()


@bot.callback_query_handler(func=lambda c: c.data == "social_menu")
def cb_social_menu(call):
    uid = call.from_user.id
    safe_answer(call)
    safe_edit(call,
              "📥 <b>Downloader</b>\n\n"
              "Two ways to use this:\n"
              f"{G['download']} <b>Download only</b> — the bot sends you the file, "
              "nothing gets uploaded\n"
              f"{G['upload']} <b>Download &amp; queue</b> — the file waits in your queue "
              "so you can publish it to YouTube\n\n"
              "Works with TikTok, Instagram, YouTube, X, Facebook, Reddit, Vimeo…\n"
              f"Saved files: <b>{count_downloads(uid)}</b>",
              social_kb())


@bot.callback_query_handler(func=lambda c: c.data == "social_help")
def cb_social_help(call):
    uid = call.from_user.id
    safe_answer(call)
    set_state(uid, "dl_url")
    safe_edit(call,
              "🔗 <b>Paste the link now</b> (or send <code>/dl &lt;link&gt;</code>)\n\n"
              "You'll then choose:\n"
              "• 📥 <b>Download only</b> — I send you the file, nothing uploaded\n"
              "• 📤 <b>Download &amp; queue</b> — it waits in your queue for YouTube\n\n"
              "Want many at once? Use bulk download for a whole TikTok account.",
              back_kb("social_menu"))


@bot.callback_query_handler(func=lambda c: c.data == "dl_go")
def cb_dl_go(call):
    uid = call.from_user.id
    state = get_state(uid)
    url = state.get("url")
    if not url:
        safe_answer(call, "Link expired — paste it again.", alert=True)
        return
    if not default_channel_id(uid):
        safe_answer(call, "Connect a YouTube channel first.", alert=True)
        start_connect_flow(call.message.chat.id, uid)
        return
    safe_answer(call, "Downloading…")
    clear_state(uid)
    start_link_download(uid, call.message.chat.id, url, edit_message=call.message.message_id)


@bot.callback_query_handler(func=lambda c: c.data == "dl_only")
def cb_dl_only(call):
    """Download without touching YouTube at all."""
    uid = call.from_user.id
    state = get_state(uid)
    url = state.get("url")
    if not url:
        safe_answer(call, "Link expired — paste it again.", alert=True)
        return
    safe_answer(call, "Downloading — no upload…")
    clear_state(uid)
    start_link_download(uid, call.message.chat.id, url, edit_message=call.message.message_id,
                        keep_only=True)


@bot.callback_query_handler(func=lambda c: c.data == "dl_privacy")
def cb_dl_privacy(call):
    safe_answer(call)
    safe_edit(call, "🔒 <b>Privacy for the next upload</b>", privacy_kb("dl_priv"))


@bot.callback_query_handler(func=lambda c: c.data.startswith("dl_priv:"))
def cb_dl_priv_set(call):
    uid = call.from_user.id
    value = call.data.split(":", 1)[1]
    if value not in PRIVACY_LABELS:
        safe_answer(call, "Invalid choice.", alert=True)
        return
    db_query('UPDATE users SET default_privacy = ? WHERE user_id = ?', (value, uid))
    state = get_state(uid)
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(Btn(f"{G['download']}  Download & queue", callback_data="dl_go", style="success"))
    kb.add(Btn(f"{G['no']}  Cancel", callback_data="dl_cancel", style="danger"))
    if state.get("url"):
        safe_answer(call, f"Privacy: {value}")
        safe_edit(call, f"🔒 Default privacy set to <b>{value}</b>.\n\nReady when you are.", kb)
    else:
        safe_answer(call, f"Privacy: {value}")
        safe_edit(call, f"🔒 Default privacy set to <b>{value}</b>.", back_kb("main_menu"))


@bot.callback_query_handler(func=lambda c: c.data == "dl_cancel")
def cb_dl_cancel(call):
    clear_state(call.from_user.id)
    safe_answer(call, "Cancelled")
    safe_edit(call, f"🎬 <b>{BRAND}</b>\n\nCancelled.", main_menu_kb(call.from_user.id))


# ── Bulk download handlers ──────────────────────────────────────────

def bulk_confirm_kb() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(Btn(f"{G['download']}  Download only (no upload)", callback_data="bulk_go",
               style="success"))
    kb.add(Btn(f"{G['upload']}  Download & queue for YouTube", callback_data="bulk_go_upload",
               style="primary"))
    kb.add(Btn(f"{G['no']}  Cancel", callback_data="bulk_cancel", style="danger"))
    return kb


def bulk_confirm_text(items: List[dict], heading: str) -> str:
    preview = "\n".join(f"• {escape(str(i.get('title') or i['url'])[:60])}"
                        for i in items[:5])
    more = f"\n… and {len(items) - 5} more" if len(items) > 5 else ""
    return (f"📦 <b>{escape(heading)}</b>\n\n"
            f"🎬 {len(items)} video(s) ready\n\n{preview}{more}\n\n"
            "Download only keeps them on the server and sends them to you — "
            "nothing is uploaded to YouTube unless you ask.")


@bot.callback_query_handler(func=lambda c: c.data == "bulk_tiktok")
def cb_bulk_tiktok(call):
    uid = call.from_user.id
    if not _YTDLP_OK:
        safe_answer(call, "yt-dlp is missing — see /diag.", alert=True)
        return
    safe_answer(call)
    set_state(uid, "bulk_tiktok")
    safe_edit(call,
              "🎵 <b>Bulk download a TikTok account</b>\n\n"
              "Send the username (or profile link) and how many videos you want, "
              "newest first:\n\n"
              "• <code>@animatrixxgamer 10</code>\n"
              "• <code>https://www.tiktok.com/@animatrixxgamer 25</code>\n\n"
              f"Defaults to 10, maximum {BULK_MAX_VIDEOS}. "
              "Nothing is uploaded — you choose after the download.",
              back_kb("social_menu"))


@bot.callback_query_handler(func=lambda c: c.data == "bulk_links")
def cb_bulk_links(call):
    uid = call.from_user.id
    safe_answer(call)
    set_state(uid, "bulk_links")
    safe_edit(call,
              "🔗 <b>Bulk download from links</b>\n\n"
              "Paste several links in one message — one per line (or separated by "
              "spaces). They can be a mix of TikTok, Instagram, YouTube and X.\n\n"
              f"Up to {BULK_MAX_VIDEOS} links at a time.",
              back_kb("social_menu"))


@bot.callback_query_handler(func=lambda c: c.data in ("bulk_go", "bulk_go_upload"))
def cb_bulk_go(call):
    uid = call.from_user.id
    state = get_state(uid)
    items = state.get("items") or []
    if not items:
        safe_answer(call, "Nothing queued — start again.", alert=True)
        return
    keep_only = call.data == "bulk_go"
    if not keep_only and not default_channel_id(uid):
        safe_answer(call, "Connect a YouTube channel first.", alert=True)
        return
    heading = state.get("heading") or "Bulk download"
    clear_state(uid)
    safe_answer(call, f"Downloading {len(items)}…")
    threading.Thread(target=bulk_download_worker,
                     kwargs=dict(uid=uid, chat_id=call.message.chat.id, items=items,
                                 keep_only=keep_only, heading=f"📥 <b>{escape(heading)}</b>"),
                     daemon=True).start()


@bot.callback_query_handler(func=lambda c: c.data == "bulk_cancel")
def cb_bulk_cancel(call):
    uid = call.from_user.id
    BULK_CANCELS.add(uid)
    clear_state(uid)
    safe_answer(call, "Stopped")
    safe_edit(call, f"🎬 <b>{BRAND}</b>\n\nBulk download cancelled.",
              main_menu_kb(uid))


# ── Saved downloads ─────────────────────────────────────────────────

@bot.callback_query_handler(func=lambda c: c.data.startswith("dl_list:"))
def cb_dl_list(call):
    uid = call.from_user.id
    page = max(0, int(call.data.split(":", 1)[1]))
    safe_answer(call)
    rows = downloads_page(uid, page)
    if not rows and page == 0:
        safe_edit(call,
                  "📂 <b>My downloads</b>\n\nNothing saved yet.\n\n"
                  "Use <b>Download only</b> and the file is kept for you here "
                  "instead of being uploaded.",
                  types.InlineKeyboardMarkup(row_width=1)
                  .add(Btn(f"{G['download']}  Download something", callback_data="social_menu",
                           style="success"))
                  .add(Btn(f"{G['back']}  Main Menu", callback_data="main_menu", style="danger")))
        return
    total = count_downloads(uid)
    text = (f"📂 <b>My downloads</b> — {total} saved\n\n"
            "Tap a file to receive it here again, or zip the page into one archive.\n"
            "Files over 50MB come as a browser link.")
    safe_edit(call, text, downloads_kb(uid, page))


@bot.callback_query_handler(func=lambda c: c.data.startswith("dl_send:"))
def cb_dl_send(call):
    uid = call.from_user.id
    download_id = int(call.data.split(":", 1)[1])
    if not get_download(download_id, uid):
        safe_answer(call, "Not found.", alert=True)
        return
    safe_answer(call, "Sending…")
    threading.Thread(target=deliver_download,
                     kwargs=dict(uid=uid, chat_id=call.message.chat.id,
                                 download_id=download_id), daemon=True).start()


@bot.callback_query_handler(func=lambda c: c.data.startswith("dl_del:"))
def cb_dl_del(call):
    uid = call.from_user.id
    download_id = int(call.data.split(":", 1)[1])
    if not delete_download(download_id, uid):
        safe_answer(call, "Already gone.", alert=True)
        return
    safe_answer(call, "Deleted")
    rows = downloads_page(uid, 0)
    if rows:
        safe_edit(call, "📂 <b>My downloads</b>", downloads_kb(uid, 0))
    else:
        safe_edit(call, "📂 <b>My downloads</b>\n\nEmpty now.", back_kb())


@bot.callback_query_handler(func=lambda c: c.data.startswith("dl_zip:"))
def cb_dl_zip_page(call):
    uid = call.from_user.id
    page = max(0, int(call.data.split(":", 1)[1]))
    paths = [row[2] for row in downloads_page(uid, page)]
    if not paths:
        safe_answer(call, "Nothing to zip.", alert=True)
        return
    safe_answer(call, "Zipping…")

    def worker():
        target = zip_paths(uid, paths)
        if not target:
            safe_send(call.message.chat.id, "❌ Could not build the zip.")
            return
        if send_document(call.message.chat.id, str(target), caption=target.name):
            safe_send(call.message.chat.id, f"🗂 Sent <b>{escape(target.name)}</b> "
                                           f"({human_size(file_size(str(target)))}) in one file.",
                      back_kb(f"dl_list:{page}"))
        else:
            link = share_url_for(str(target), uid)
            kb = types.InlineKeyboardMarkup(row_width=1)
            kb.add(types.InlineKeyboardButton("⬇️ Open zip link", url=link))
            kb.add(Btn(f"{G['back']}  Back", callback_data=f"dl_list:{page}", style="danger"))
            safe_send(call.message.chat.id,
                      f"⚠️ The zip is {human_size(file_size(str(target)))} — over the "
                      "50MB Telegram limit, so use the link.", kb)

    threading.Thread(target=worker, daemon=True).start()


@bot.callback_query_handler(func=lambda c: c.data.startswith("dl_links:"))
def cb_dl_links_page(call):
    uid = call.from_user.id
    page = max(0, int(call.data.split(":", 1)[1]))
    rows = downloads_page(uid, page)
    if not rows:
        safe_answer(call, "Nothing to share.", alert=True)
        return
    safe_answer(call)
    safe_send(call.message.chat.id, share_links_text(uid, rows), back_kb(f"dl_list:{page}"))


@bot.callback_query_handler(func=lambda c: c.data.startswith("dl_zip_ids:"))
def cb_dl_zip_ids(call):
    uid = call.from_user.id
    ids = [int(x) for x in call.data.split(":", 1)[1].split(",") if x.isdigit()]
    paths = [row[2] for row in (get_download(i, uid) for i in ids) if row and row[2]]
    if not paths:
        safe_answer(call, "Nothing to zip.", alert=True)
        return
    safe_answer(call, "Zipping…")

    def worker():
        target = zip_paths(uid, paths)
        if not target:
            safe_send(call.message.chat.id, "❌ Could not build the zip.")
            return
        if send_document(call.message.chat.id, str(target), caption=target.name):
            safe_send(call.message.chat.id,
                      f"🗂 Sent {escape(target.name)} ({human_size(file_size(str(target)))}).",
                      back_kb("dl_list:0"))
        else:
            link = share_url_for(str(target), uid)
            kb = types.InlineKeyboardMarkup(row_width=1)
            kb.add(types.InlineKeyboardButton("⬇️ Open zip link", url=link))
            kb.add(Btn(f"{G['back']}  Back", callback_data="dl_list:0", style="danger"))
            safe_send(call.message.chat.id,
                      f"⚠️ Zip is {human_size(file_size(str(target)))} — over 50MB, use the link.",
                      kb)

    threading.Thread(target=worker, daemon=True).start()


@bot.callback_query_handler(func=lambda c: c.data.startswith("dl_links_ids:"))
def cb_dl_links_ids(call):
    uid = call.from_user.id
    ids = [int(x) for x in call.data.split(":", 1)[1].split(",") if x.isdigit()]
    rows = [row for row in (get_download(i, uid) for i in ids) if row]
    if not rows:
        safe_answer(call, "Nothing to share.", alert=True)
        return
    safe_answer(call)
    safe_send(call.message.chat.id, share_links_text(uid, rows), back_kb("dl_list:0"))


@bot.callback_query_handler(func=lambda c: c.data.startswith("dl_queue:"))
def cb_dl_queue(call):
    """Turn a saved download into a YouTube job after the fact."""
    uid = call.from_user.id
    download_id = int(call.data.split(":", 1)[1])
    row = get_download(download_id, uid)
    if not row:
        safe_answer(call, "Not found.", alert=True)
        return
    if not default_channel_id(uid):
        safe_answer(call, "Connect a YouTube channel first.", alert=True)
        return
    path = row[2]
    if not path or not Path(path).exists():
        safe_answer(call, "File is gone.", alert=True)
        return
    title = Path(path).stem.replace("_", " ")[:100]
    job_id = create_job(uid, default_channel_id(uid), title, file_path=path,
                        source="link", source_url=row[4] or "",
                        privacy=default_privacy(uid))
    safe_answer(call, "Queued")
    safe_send(call.message.chat.id, job_card_text(get_job(job_id, uid)),
              job_actions_kb(job_id, "pending"))


def start_link_download(uid: int, chat_id: int, url: str, edit_message: Optional[int] = None,
                        keep_only: bool = False) -> None:
    """Download one link. keep_only=True never touches YouTube."""
    privacy = default_privacy(uid)
    channel_id = default_channel_id(uid)
    dest = STORAGE_DIR / "social" / str(uid)
    if edit_message:
        try:
            bot.edit_message_text("📥 <b>Downloading…</b> 0%", chat_id, edit_message)
        except Exception:
            edit_message = None
    msg_id = edit_message or bot.send_message(chat_id, "📥 <b>Downloading…</b> 0%").message_id
    last = {"pct": -25}

    def progress(pct: int) -> None:
        if pct - last["pct"] < 25:
            return
        last["pct"] = pct
        try:
            bot.edit_message_text(f"📥 <b>Downloading…</b> {pct}%", chat_id, msg_id)
        except Exception:
            pass

    def worker():
        try:
            path, info = download_media(url, dest, progress=progress)
        except Exception as exc:
            try:
                bot.edit_message_text(f"❌ Download failed:\n<code>{escape(str(exc)[:300])}</code>\n\n"
                                      "TikTok/Instagram often need cookies — see /diag.",
                                      chat_id, msg_id)
            except Exception:
                pass
            return

        title = (info.get("title") or Path(path).stem)[:100]
        if keep_only:
            download_id = add_download(uid, str(path), path.name, file_size(str(path)), url,
                                       kind="link")
            audit(uid, "keep_download", f"download={download_id} url={url}")
            deliver_download(uid, chat_id, download_id, edit_message_id=msg_id)
            return

        # Build description with source info and any extracted TikTok hashtags
        description = (f"{title}\n\n"
                       f"Source: {url}\n"
                       f"Original creator: {info.get('uploader') or 'unknown'}")
        # Extract hashtags from TikTok description if available
        source_hashtags = extract_hashtags(info.get("title", "") or "")
        tags = source_hashtags or ("viral,shorts" if (info.get("duration") or 999) <= 90 else "")
        # Apply saved user defaults
        defaults = get_user_defaults(uid)
        if not tags and defaults.get("tags"):
            tags = defaults["tags"]
        if defaults.get("description"):
            description = defaults["description"] + "\n\n" + description
        job_id = create_job(uid, channel_id, title, file_path=str(path), source="link",
                            source_url=url, privacy=privacy, description=description, tags=tags)
        audit(uid, "link_download", f"job={job_id} url={url}")
        job = get_job(job_id, uid)
        try:
            bot.edit_message_text(job_card_text(job) + "\n\nPick what happens next:",
                                  chat_id, msg_id, reply_markup=job_actions_kb(job_id, "pending"),
                                  disable_web_page_preview=True)
        except Exception:
            safe_send(chat_id, job_card_text(job), job_actions_kb(job_id, "pending"))

    threading.Thread(target=worker, daemon=True).start()


# ═════════════════════════════════════════════════════════════════
#  KEEP-ONLY DELIVERY (files back to Telegram, or as a link)
# ═════════════════════════════════════════════════════════════════

BULK_CANCELS: set = set()


def share_url_for(path: str, uid: int) -> str:
    token = make_download_token(path, uid)
    base = (PUBLIC_BASE_URL or f"http://localhost:{HTTP_PORT}").rstrip("/")
    return f"{base}/dl/{token}"


def send_document(chat_id: int, path: str, caption: str = "") -> bool:
    """Send a file to Telegram, or report that it is too big for bots."""
    size = file_size(path)
    if size > TG_SEND_LIMIT:
        return False
    try:
        with open(path, "rb") as handle:
            bot.send_document(chat_id, handle, caption=caption[:1000], timeout=600)
        return True
    except Exception as exc:
        print(f"[deliver] send_document failed for {path}: {exc}")
        return False


def deliver_download(uid: int, chat_id: int, download_id: int,
                     edit_message_id: Optional[int] = None) -> None:
    """Send one saved download, falling back to a browser link when huge."""
    row = get_download(download_id, uid)
    if not row:
        safe_send(chat_id, "❌ That download is gone.")
        return
    _id, name, path, size, source_url, _kind, _created = row
    if not path or not Path(path).exists():
        safe_send(chat_id, "❌ The file is no longer on disk — download it again.")
        return

    header = (f"🎬 <b>{escape(name)}</b>\n"
              f"💾 {human_size(size)}\n"
              f"📥 Saved — nothing was uploaded.")
    if edit_message_id:
        try:
            bot.edit_message_text(header, chat_id, edit_message_id)
        except Exception:
            pass
    else:
        safe_send(chat_id, header)

    if send_document(chat_id, path, caption=name):
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(Btn(f"{G['folder']}  My downloads", callback_data="dl_list:0", style="primary"))
        if user_channels(uid):
            kb.add(Btn(f"{G['upload']}  Also queue for YouTube",
                       callback_data=f"dl_queue:{download_id}", style="success"))
        kb.add(Btn(f"{G['back']}  Main Menu", callback_data="main_menu", style="danger"))
        safe_send(chat_id, "✅ Sent above." + ("" if source_url else ""), kb)
        return

    link = share_url_for(path, uid)
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("⬇️ Open download link", url=link))
    kb.add(Btn(f"{G['folder']}  My downloads", callback_data="dl_list:0", style="primary"))
    safe_send(chat_id,
              f"⚠️ <b>Too big for Telegram</b> ({human_size(size)} > 50MB).\n\n"
              "Use the button below to download it directly from the server.\n"
              f"Link valid for 12 hours.",
              kb)


def zip_paths(uid: int, paths: List[str]) -> Optional[Path]:
    """Bundle downloads into one zip (stored, since video barely compresses)."""
    if not paths:
        return None
    zip_dir = STORAGE_DIR / "zips"
    zip_dir.mkdir(parents=True, exist_ok=True)
    target = zip_dir / f"bulk_{uid}_{int(time.time())}.zip"
    try:
        with zipfile.ZipFile(target, "w", zipfile.ZIP_STORED) as archive:
            for path in paths:
                if path and Path(path).exists():
                    archive.write(path, arcname=Path(path).name)
    except Exception as exc:
        print(f"[zip] failed: {exc}")
        return None
    return target


def deliver_batch(uid: int, chat_id: int, paths: List[str], kind: str = "bulk",
                  heading: str = "") -> None:
    """Save a finished batch, send what fits, and offer link/zip options."""
    saved = []
    for path in paths:
        if not path:
            continue
        saved.append(add_download(uid, str(path), Path(path).name, file_size(str(path)),
                                  kind=kind))

    lines = [heading or "✅ <b>Download finished</b>", f"🎬 {len(saved)} file(s) saved"]
    too_big = []
    for download_id in saved:
        row = get_download(download_id, uid)
        if not row:
            continue
        size = row[3] or 0
        if send_document(chat_id, row[2], caption=row[1]):
            continue
        too_big.append(row)

    if too_big:
        lines.append(f"⚠️ {len(too_big)} file(s) are over 50MB and can't be sent "
                     "through Telegram.")
    kb = types.InlineKeyboardMarkup(row_width=1)
    if len(saved) > 1:
        kb.add(Btn(f"🗂 Zip the batch & send", callback_data=f"dl_zip_ids:{','.join(map(str, saved[:20]))}",
                   style="success"))
    if too_big or len(saved) > 1:
        kb.add(Btn(f"🔗 Share download links",
                   callback_data=f"dl_links_ids:{','.join(map(str, saved[:20]))}",
                   style="primary"))
    kb.add(Btn(f"{G['folder']}  My downloads", callback_data="dl_list:0", style="primary"))
    kb.add(Btn(f"{G['back']}  Main Menu", callback_data="main_menu", style="danger"))
    safe_send(chat_id, "\n".join(lines), kb)


def share_links_text(uid: int, rows: List[tuple]) -> str:
    text = "🔗 <b>Download links</b> (valid 12 hours)\n\n"
    for row in rows:
        _id, name, path, size, _src, _kind, _created = row
        if not path or not Path(path).exists():
            continue
        text += (f"• {escape(str(name)[:60])} · {human_size(size)}\n"
                 f"   {share_url_for(path, uid)}\n")
    if not PUBLIC_BASE_URL:
        text += ("\nℹ️ These are localhost links. Set <code>PUBLIC_BASE_URL</code> "
                 "to your host URL so they work from any device.")
    return text


# ── Bulk downloading ────────────────────────────────────────────────

def bulk_items_from_profile(handle_or_url: str, count: int) -> List[dict]:
    _handle, profile_url = tiktok_profile_url(handle_or_url)
    videos = list_profile_videos(profile_url, limit=max(1, count))
    return [{"url": v["url"] or f"{profile_url}/video/{v['id']}",
             "title": v.get("title") or ""} for v in videos[:count]]


def bulk_items_from_links(text: str) -> List[dict]:
    seen = set()
    items = []
    for raw in re.split(r'[\s,]+', text or ""):
        url = extract_url(raw)
        if not url or url in seen:
            continue
        seen.add(url)
        items.append({"url": url, "title": ""})
    return items


def bulk_download_worker(uid: int, chat_id: int, items: List[dict], keep_only: bool,
                         heading: str = "") -> None:
    """Download a batch sequentially, reporting progress, then deliver."""
    BULK_CANCELS.discard(uid)
    dest = STORAGE_DIR / "social" / str(uid) / "bulk"
    total = len(items)
    msg = bot.send_message(chat_id, f"📥 <b>0/{total}</b> — starting…")
    paths: List[str] = []
    failures: List[str] = []
    jobs: List[int] = []

    for index, item in enumerate(items, start=1):
        if uid in BULK_CANCELS:
            failures.append("cancelled by user")
            break
        try:
            bot.edit_message_text(
                f"📥 <b>{index - 1}/{total}</b> downloading…\n"
                f"{(item.get('title') or item['url'])[:70]}",
                chat_id, msg.message_id)
        except Exception:
            pass
        try:
            path, _info = download_media(item["url"], dest)
        except Exception as exc:
            failures.append(f"{item['url'][:60]} — {str(exc)[:80]}")
            continue
        paths.append(str(path))
        if not keep_only:
            title = (item.get("title") or Path(path).stem)[:100]
            jobs.append(create_job(uid, default_channel_id(uid), title, file_path=str(path),
                                   source="link", source_url=item["url"],
                                   privacy=default_privacy(uid)))
        try:
            bot.edit_message_text(f"📥 <b>{index}/{total}</b> done — "
                                  f"{human_size(file_size(str(path)))}",
                                  chat_id, msg.message_id)
        except Exception:
            pass

    audit(uid, "bulk_download",
          f"files={len(paths)} failed={len(failures)} keep_only={keep_only}")

    if not paths:
        detail = "\n".join(escape(f) for f in failures[:5]) or "unknown error"
        safe_send(chat_id, "❌ Nothing downloaded.\n\n" + detail)
        return

    summary = (heading or "📥 <b>Batch complete</b>")
    summary += f"\n\n✅ {len(paths)} downloaded"
    if failures:
        summary += f"\n⚠️ {len(failures)} failed"
    if jobs:
        summary += f"\n📋 Queued for YouTube: {len(jobs)} job(s)"

    if keep_only:
        deliver_batch(uid, chat_id, paths, kind="bulk", heading=summary)
    else:
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(Btn(f"{G['queue']}  Open queue", callback_data="queue_menu", style="primary"))
        kb.add(Btn(f"{G['folder']}  Save a copy too", callback_data="dl_list:0", style="primary"))
        safe_send(chat_id, summary, kb)
    if failures:
        safe_send(chat_id, "⚠️ Failed:\n" + "\n".join(escape(f) for f in failures[:5]))


# ═════════════════════════════════════════════════════════════════
#  TIKTOK → YOUTUBE AUTO-POST HANDLERS
# ═════════════════════════════════════════════════════════════════

def tiktok_menu_text(uid: int) -> str:
    sources = user_sources(uid)
    if not sources:
        return ("🎵 <b>TikTok → YouTube</b>\n\n"
                "Add a TikTok account with its @username or profile link and I will "
                "watch it, download every new video and publish it to your YouTube "
                "channel automatically.\n\n"
                "You can also send a single TikTok link and I'll queue it once.")
    text = f"🎵 <b>TikTok → YouTube</b> ({len(sources)})\n\n"
    for src in sources:
        state = "▶️ running" if src[3] else "⏸ paused"
        text += (f"• <b>@{escape(src[1])}</b> → {escape(channel_title(uid, src[2]))}\n"
                 f"   {state} · up to {src[4]}/check · last: {src[6][:16] if src[6] else 'never'}\n"
                 + (f"   ⚠️ {escape(str(src[7])[:80])}\n" if src[7] and str(src[7]).startswith("error") else ""))
    return text


def add_tiktok_source(uid: int, chat_id: int, handle_or_url: str) -> bool:
    if not _require_channel(chat_id, uid):
        return False
    handle, profile_url = tiktok_profile_url(handle_or_url)
    if not handle:
        safe_send(chat_id, "❌ Send a TikTok username (like <code>@creator</code>) or profile link.")
        return False

    msg = bot.send_message(chat_id, f"🔍 Checking @{handle} and their latest videos…")

    def worker():
        try:
            videos = list_profile_videos(profile_url, limit=5)
        except Exception as exc:
            try:
                bot.edit_message_text(
                    f"❌ Could not read <b>@{escape(handle)}</b>:\n"
                    f"<code>{escape(str(exc)[:280])}</code>\n\n"
                    "TikTok blocks many server IPs. Drop a cookies file at "
                    f"<code>{escape(SOCIAL_COOKIES_FILE)}</code> and try again.",
                    chat_id, msg.message_id)
            except Exception:
                pass
            return
        if not videos:
            try:
                bot.edit_message_text(f"⚠️ No public videos found for <b>@{escape(handle)}</b> "
                                      f"(private account?).", chat_id, msg.message_id)
            except Exception:
                pass
            return

        source_id = add_source(uid, handle, profile_url, default_channel_id(uid),
                              privacy=default_privacy(uid),
                              max_per_run=max(1, TIKTOK_MAX_PER_RUN))
        audit(uid, "tiktok_source_added", f"@{handle}")
        db_query('UPDATE tiktok_sources SET last_check = ? WHERE source_id = ?',
                 (datetime.utcnow().isoformat(), source_id))
        text = (f"✅ <b>@{escape(handle)} is being watched</b>\n\n"
                f"📺 Target: {escape(channel_title(uid, default_channel_id(uid)))}\n"
                f"🔒 {PRIVACY_LABELS[default_privacy(uid)]} · 1 video per check\n\n"
                f"Latest videos found: {len(videos)}\n"
                "I'll download new uploads and publish them automatically. "
                "Tweak the batch size, interval and channel in the source settings.")
        try:
            bot.edit_message_text(text, chat_id, msg.message_id, reply_markup=tk_kb(uid))
        except Exception:
            safe_send(chat_id, text, tk_kb(uid))

    threading.Thread(target=worker, daemon=True).start()
    return True


@bot.callback_query_handler(func=lambda c: c.data == "tk_menu")
def cb_tk_menu(call):
    uid = call.from_user.id
    safe_answer(call)
    safe_edit(call, tiktok_menu_text(uid), tk_kb(uid))


@bot.callback_query_handler(func=lambda c: c.data == "tk_add")
def cb_tk_add(call):
    uid = call.from_user.id
    if not _require_channel(call.message.chat.id, uid):
        safe_answer(call, "Connect YouTube first.", alert=True)
        return
    # Plan check: TikTok sources
    if not plan_has_feature(uid, "tiktok"):
        safe_answer(call, "Upgrade your plan to use TikTok auto-post.", alert=True)
        return
    max_tk = plan_max_tiktok(uid)
    current_tk = len(user_sources(uid))
    if current_tk >= max_tk:
        safe_answer(call, f"TikTok limit reached ({current_tk}/{max_tk}). Upgrade plan.", alert=True)
        return
    safe_answer(call)
    set_state(uid, "tk_add")
    safe_edit(call,
              "🎵 <b>Which TikTok account?</b>\n\n"
              "Send the username or the profile link, for example:\n"
              "• <code>@animatrixxgamer</code>\n"
              "• <code>https://www.tiktok.com/@animatrixxgamer</code>",
              back_kb("tk_menu"))


@bot.callback_query_handler(func=lambda c: c.data.startswith("tk_source:"))
def cb_tk_source(call):
    uid = call.from_user.id
    source_id = int(call.data.split(":", 1)[1])
    src = get_source(source_id, uid)
    if not src:
        safe_answer(call, "Source not found.", alert=True)
        return
    safe_answer(call)
    handle, channel_id, enabled = src[2], src[4], src[6]
    text = (f"🎵 <b>@{escape(handle)}</b>\n\n"
            f"📺 Channel: {escape(channel_title(uid, channel_id))}\n"
            f"🔒 Privacy: {PRIVACY_LABELS.get(src[5], src[5])}\n"
            f"📦 Batch: {src[8]} video(s) per check\n"
            f"⏱ Every {max(1, int(src[7] or TIKTOK_POLL_SECONDS) // 60)} min\n"
            f"🕒 Spacing: {src[9]} min\n"
            f"📈 Last check: {fmt_when(src[11])}\n"
            f"📄 Status: {escape(str(src[12] or 'never checked')[:80])}\n"
            f"🔗 {escape(src[3])}")
    safe_edit(call, text, tk_source_kb(source_id, bool(enabled)))


@bot.callback_query_handler(func=lambda c: c.data.startswith("tk_toggle:"))
def cb_tk_toggle(call):
    uid = call.from_user.id
    source_id = int(call.data.split(":", 1)[1])
    src = get_source(source_id, uid)
    if not src:
        safe_answer(call, "Source not found.", alert=True)
        return
    enabled = 0 if src[6] else 1
    db_query('UPDATE tiktok_sources SET enabled = ? WHERE source_id = ?', (enabled, source_id))
    safe_answer(call, "Resumed ▶️" if enabled else "Paused ⏸")
    safe_edit(call, tiktok_menu_text(uid), tk_kb(uid))


@bot.callback_query_handler(func=lambda c: c.data.startswith("tk_del:"))
def cb_tk_del(call):
    uid = call.from_user.id
    source_id = int(call.data.split(":", 1)[1])
    if not get_source(source_id, uid):
        safe_answer(call, "Already gone.", alert=True)
        return
    db_query('DELETE FROM tiktok_sources WHERE source_id = ?', (source_id,))
    db_query('DELETE FROM tiktok_seen WHERE source_id = ?', (source_id,))
    audit(uid, "tiktok_source_deleted", str(source_id))
    safe_answer(call, "Source removed")
    safe_edit(call, tiktok_menu_text(uid), tk_kb(uid))


@bot.callback_query_handler(func=lambda c: c.data.startswith("tk_check:") or c.data == "tk_check_all")
def cb_tk_check(call):
    uid = call.from_user.id
    if call.data == "tk_check_all":
        ids = [src[0] for src in user_sources(uid)]
    else:
        ids = [int(call.data.split(":", 1)[1])]
    if not ids:
        safe_answer(call, "Nothing to check.", alert=True)
        return
    safe_answer(call, "Checking TikTok now…")

    def worker():
        total = 0
        for source_id in ids:
            total += poll_source(source_id, announce=False)
        safe_send(call.message.chat.id,
                  f"🎵 Check finished — {total} new video(s) queued." if total else
                  "🎵 Check finished — nothing new.",
                  tk_kb(uid))

    threading.Thread(target=worker, daemon=True).start()


@bot.callback_query_handler(func=lambda c: c.data.startswith("tk_setch:"))
def cb_tk_setch(call):
    uid = call.from_user.id
    source_id = int(call.data.split(":", 1)[1])
    channels = user_channels(uid)
    if not channels:
        safe_answer(call, "Connect a channel first.", alert=True)
        return
    kb = types.InlineKeyboardMarkup(row_width=1)
    for ch in channels:
        kb.add(Btn(f"📺 {ch[1][:30]}", callback_data=f"tk_channel:{source_id}:{ch[0]}",
                   style="primary"))
    kb.add(Btn(f"{G['back']}  Back", callback_data=f"tk_source:{source_id}", style="primary"))
    safe_answer(call)
    safe_edit(call, "📺 <b>Publish these videos to…</b>", kb)


@bot.callback_query_handler(func=lambda c: c.data.startswith("tk_channel:"))
def cb_tk_channel(call):
    uid = call.from_user.id
    _, source_id, channel_id = call.data.split(":", 2)
    source_id = int(source_id)
    if not get_source(source_id, uid) or not channel_row(uid, channel_id):
        safe_answer(call, "Not found.", alert=True)
        return
    db_query('UPDATE tiktok_sources SET channel_id = ? WHERE source_id = ?', (channel_id, source_id))
    safe_answer(call, "Channel updated")
    safe_edit(call, tiktok_menu_text(uid), tk_kb(uid))


@bot.callback_query_handler(func=lambda c: c.data.startswith("tk_setpriv:"))
def cb_tk_setpriv(call):
    source_id = int(call.data.split(":", 1)[1])
    safe_answer(call)
    safe_edit(call, "🔒 <b>Privacy for this source</b>", privacy_kb(f"tk_priv_{source_id}"))


@bot.callback_query_handler(func=lambda c: c.data.startswith("tk_priv_"))
def cb_tk_priv_set(call):
    uid = call.from_user.id
    prefix, value = call.data.rsplit(":", 1)
    source_id = int(prefix.rsplit("_", 1)[1])
    if value not in PRIVACY_LABELS or not get_source(source_id, uid):
        safe_answer(call, "Invalid choice.", alert=True)
        return
    db_query('UPDATE tiktok_sources SET privacy_status = ? WHERE source_id = ?', (value, source_id))
    safe_answer(call, f"Privacy: {value}")
    safe_edit(call, tiktok_menu_text(uid), tk_kb(uid))


@bot.callback_query_handler(func=lambda c: c.data.startswith("tk_setmax:"))
def cb_tk_setmax(call):
    source_id = int(call.data.split(":", 1)[1])
    kb = types.InlineKeyboardMarkup(row_width=4)
    for n in (1, 2, 3, 5):
        kb.add(Btn(str(n), callback_data=f"tk_max:{source_id}:{n}", style="primary"))
    kb.add(Btn(f"{G['no']}  Cancel", callback_data=f"tk_source:{source_id}", style="danger"))
    safe_answer(call)
    safe_edit(call, "📦 <b>How many videos per check?</b>", kb)


@bot.callback_query_handler(func=lambda c: c.data.startswith("tk_max:"))
def cb_tk_max(call):
    uid = call.from_user.id
    _, source_id, n = call.data.split(":")
    source_id, n = int(source_id), max(1, min(10, int(n)))
    if not get_source(source_id, uid):
        safe_answer(call, "Not found.", alert=True)
        return
    db_query('UPDATE tiktok_sources SET max_per_run = ? WHERE source_id = ?', (n, source_id))
    safe_answer(call, f"{n} per check")
    safe_edit(call, tiktok_menu_text(uid), tk_kb(uid))


@bot.callback_query_handler(func=lambda c: c.data.startswith("tk_setint:"))
def cb_tk_setint(call):
    source_id = int(call.data.split(":", 1)[1])
    kb = types.InlineKeyboardMarkup(row_width=2)
    for label, minutes in (("15 min", 15), ("1 hour", 60), ("6 hours", 360), ("24 hours", 1440)):
        kb.add(Btn(label, callback_data=f"tk_int:{source_id}:{minutes}", style="primary"))
    kb.add(Btn(f"{G['no']}  Cancel", callback_data=f"tk_source:{source_id}", style="danger"))
    safe_answer(call)
    safe_edit(call, "⏱ <b>How often should I check?</b>", kb)


@bot.callback_query_handler(func=lambda c: c.data.startswith("tk_int:"))
def cb_tk_int(call):
    uid = call.from_user.id
    _, source_id, minutes = call.data.split(":")
    source_id, minutes = int(source_id), max(5, int(minutes))
    if not get_source(source_id, uid):
        safe_answer(call, "Not found.", alert=True)
        return
    db_query('UPDATE tiktok_sources SET interval_seconds = ? WHERE source_id = ?',
             (minutes * 60, source_id))
    safe_answer(call, f"Every {minutes} min")
    safe_edit(call, tiktok_menu_text(uid), tk_kb(uid))


# ═════════════════════════════════════════════════════════════════
#  ADMIN HANDLERS
# ═════════════════════════════════════════════════════════════════

def users_page(uid: int, page: int) -> tuple:
    per_page = 5
    rows = list_users(per_page, page * per_page)
    total = count_users()
    text = (f"👥 <b>Users</b> — {total} total\n"
            f"Page {page + 1}/{max(1, (total + per_page - 1) // per_page)}\n\n")
    kb = types.InlineKeyboardMarkup(row_width=1)
    for user_id, username, banned, created in rows:
        mark = "🚫" if banned else "👤"
        label = f"{mark} {username or 'no username'} · {user_id}"
        kb.add(Btn(label, callback_data=f"adm_user:{user_id}", style="danger" if banned else "primary"))
    if page > 0:
        kb.row(Btn("⬅️ Prev", callback_data=f"adm_users:{page - 1}", style="primary"))
    if (page + 1) * per_page < total:
        kb.row(Btn("Next ➡️", callback_data=f"adm_users:{page + 1}", style="primary"))
    kb.row(Btn(f"{G['back']}  Admin", callback_data="admin_panel", style="danger"))
    return text, kb


@bot.callback_query_handler(func=lambda c: c.data == "admin_panel")
def cb_admin_panel(call):
    uid = call.from_user.id
    if not is_admin(uid):
        safe_answer(call, "⚠️ Admin only!", alert=True)
        return
    safe_answer(call)
    safe_edit(call, "🔧 <b>Admin Panel</b>\n\nEverything works — pick a section.", admin_kb())


@bot.callback_query_handler(func=lambda c: c.data == "adm_stats")
def cb_adm_stats(call):
    uid = call.from_user.id
    if not is_admin(uid):
        safe_answer(call, "Admin only.", alert=True)
        return
    safe_answer(call)
    statuses = ", ".join(f"{STATUS_LABELS.get(s, s)} {n}" for s, n in job_status_counts()) or "none"
    text = (
        "📊 <b>Statistics</b>\n\n"
        f"👥 Users: {count_users()}\n"
        f"📁 Files: {length_or_zero(db_query('SELECT COUNT(*) FROM user_files', fetch=True))}\n"
        f"📺 Channels: {length_or_zero(db_query('SELECT COUNT(*) FROM youtube_channels', fetch=True))}\n"
        f"🎵 TikTok sources: {length_or_zero(db_query('SELECT COUNT(*) FROM tiktok_sources', fetch=True))}\n"
        f"🎬 Jobs: {statuses}\n"
        f"⏱ Uptime: {int((time.time() - START_TIME) // 60)} min\n"
        f"🧵 Active uploads: {'busy' if UPLOAD_LOCK.locked() else 'idle'}"
    )
    safe_edit(call, text, admin_kb())


@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_users"))
def cb_adm_users(call):
    if not is_admin(call.from_user.id):
        safe_answer(call, "Admin only.", alert=True)
        return
    page = int(call.data.split(":", 1)[1]) if ":" in call.data else 0
    safe_answer(call)
    text, kb = users_page(call.from_user.id, max(0, page))
    safe_edit(call, text, kb)


@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_user:"))
def cb_adm_user(call):
    if not is_admin(call.from_user.id):
        safe_answer(call, "Admin only.", alert=True)
        return
    target = int(call.data.split(":", 1)[1])
    rows = db_query('SELECT user_id, username, status, plan_expiry, COALESCE(banned, 0), created_at '
                    'FROM users WHERE user_id = ?', (target,), fetch=True)
    if not rows:
        safe_answer(call, "User not found.", alert=True)
        return
    user_id, username, status, expiry, banned, created = rows[0]
    jobs = length_or_zero(db_query('SELECT COUNT(*) FROM upload_jobs WHERE user_id = ?',
                                   (target,), fetch=True))
    text = (f"👤 <b>User {user_id}</b>\n\n"
            f"✳️ @{escape(username or 'none')}\n"
            f"🔰 {get_user_status(user_id).title()} · expiry {fmt_when(expiry)}\n"
            f"🎬 Jobs: {jobs} · 📁 Files: {get_file_count(user_id)}\n"
            f"🚫 Banned: {'yes' if banned else 'no'}\n"
            f"🗓 Joined: {fmt_when(created)}")
    kb = types.InlineKeyboardMarkup(row_width=1)
    if banned:
        kb.add(Btn(f"{G['ok']}  Unban", callback_data=f"adm_unban_user:{user_id}", style="success"))
    else:
        kb.add(Btn(f"{G['ban']}  Ban user", callback_data=f"adm_ban_user:{user_id}", style="danger"))
    kb.add(Btn(f"{G['star']}  Grant 30 days premium", callback_data=f"adm_grant:{user_id}",
               style="success"))
    kb.add(Btn(f"{G['trash']}  Revoke premium", callback_data=f"adm_revoke:{user_id}", style="danger"))
    kb.add(Btn(f"{G['back']}  Users", callback_data="adm_users:0", style="primary"))
    safe_answer(call)
    safe_edit(call, text, kb)


@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_ban_user:") or
                            c.data.startswith("adm_unban_user:"))
def cb_adm_ban_toggle(call):
    if not is_admin(call.from_user.id):
        safe_answer(call, "Admin only.", alert=True)
        return
    ban = call.data.startswith("adm_ban_user:")
    target = int(call.data.split(":", 1)[1])
    if target == OWNER_ID:
        safe_answer(call, "Cannot ban the owner.", alert=True)
        return
    set_banned(target, ban)
    audit(call.from_user.id, "ban" if ban else "unban", str(target))
    safe_answer(call, "Banned" if ban else "Unbanned")
    if ban:
        notify(target, "🚫 You have been blocked from using this bot.")
    cb_adm_user(call)


@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_grant:") or
                            c.data.startswith("adm_revoke:"))
def cb_adm_premium(call):
    if not is_admin(call.from_user.id):
        safe_answer(call, "Admin only.", alert=True)
        return
    grant = call.data.startswith("adm_grant:")
    target = int(call.data.split(":", 1)[1])
    if grant:
        set_premium(target, 30)
        notify(target, "⭐ An admin gave you 30 days of premium. Enjoy!")
    else:
        db_query('UPDATE users SET status = "free", plan_expiry = NULL WHERE user_id = ?', (target,))
    audit(call.from_user.id, "premium_grant" if grant else "premium_revoke", str(target))
    safe_answer(call, "Premium granted" if grant else "Premium revoked")
    cb_adm_user(call)


@bot.callback_query_handler(func=lambda c: c.data == "adm_ban")
def cb_adm_ban(call):
    if not is_admin(call.from_user.id):
        safe_answer(call, "Admin only.", alert=True)
        return
    safe_answer(call, "Tap a user to ban or unban")
    text, kb = users_page(call.from_user.id, 0)
    safe_edit(call, text, kb)


@bot.callback_query_handler(func=lambda c: c.data == "adm_premium")
def cb_adm_premium_menu(call):
    if not is_admin(call.from_user.id):
        safe_answer(call, "Admin only.", alert=True)
        return
    safe_answer(call, "Tap a user to grant premium")
    text, kb = users_page(call.from_user.id, 0)
    safe_edit(call, text, kb)


@bot.callback_query_handler(func=lambda c: c.data == "adm_broadcast")
def cb_adm_broadcast(call):
    if not is_admin(call.from_user.id):
        safe_answer(call, "Admin only.", alert=True)
        return
    safe_answer(call)
    set_state(call.from_user.id, "adm_broadcast")
    safe_edit(call,
              "📢 <b>Broadcast</b>\n\n"
              "Send the message to deliver to every user. HTML formatting is allowed.\n\n"
              "Send /cancel to abort.",
              back_kb("admin_panel"))


@bot.callback_query_handler(func=lambda c: c.data == "adm_broadcast_cancel")
def cb_adm_broadcast_cancel(call):
    clear_state(call.from_user.id)
    safe_answer(call, "Broadcast cancelled")
    safe_edit(call, "🔧 <b>Admin Panel</b>", admin_kb())


@bot.callback_query_handler(func=lambda c: c.data == "adm_broadcast_go")
def cb_adm_broadcast_go(call):
    uid = call.from_user.id
    if not is_admin(uid):
        safe_answer(call, "Admin only.", alert=True)
        return
    state = get_state(uid)
    text = state.get("broadcast_text")
    if not text:
        safe_answer(call, "Nothing to send.", alert=True)
        return
    safe_answer(call, "Broadcasting…")
    clear_state(uid)

    def worker():
        rows = db_query('SELECT user_id FROM users', fetch=True) or []
        sent = failed = 0
        for (user_id,) in rows:
            try:
                bot.send_message(user_id, text, disable_web_page_preview=True)
                sent += 1
            except Exception:
                failed += 1
            time.sleep(0.05)
        audit(uid, "broadcast", f"sent={sent} failed={failed}")
        safe_send(call.message.chat.id, f"📢 Broadcast done — sent {sent}, failed {failed}.",
                  admin_kb())

    threading.Thread(target=worker, daemon=True).start()


@bot.callback_query_handler(func=lambda c: c.data == "adm_settings")
def cb_adm_settings(call):
    if not is_admin(call.from_user.id):
        safe_answer(call, "Admin only.", alert=True)
        return
    free = get_setting("free_file_limit", str(FREE_LIMIT))
    premium = get_setting("premium_file_limit", str(PREMIUM_LIMIT))
    text = ("⚙️ <b>Settings</b>\n\n"
            f"📁 Free file limit: {free}\n"
            f"⭐ Premium file limit: {premium}\n"
            f"🎵 TikTok poll: {TIKTOK_POLL_SECONDS}s · batch {TIKTOK_MAX_PER_RUN}\n"
            f"⏱ Scheduler tick: {SCHEDULER_TICK_SECONDS}s · retries {MAX_JOB_RETRIES}\n"
            f"🎞 ffmpeg: {'yes' if shutil.which('ffmpeg') else 'no'}\n"
            f"🍪 Cookies file: {'found' if os.path.isfile(SOCIAL_COOKIES_FILE) else 'missing'}\n\n"
            "Tap to change a limit.")
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(Btn("📁 Set free file limit", callback_data="adm_set_free", style="primary"))
    kb.add(Btn("⭐ Set premium file limit", callback_data="adm_set_prem", style="primary"))
    kb.add(Btn(f"{G['back']}  Admin", callback_data="admin_panel", style="danger"))
    safe_answer(call)
    safe_edit(call, text, kb)


@bot.callback_query_handler(func=lambda c: c.data in ("adm_set_free", "adm_set_prem"))
def cb_adm_set_limit(call):
    uid = call.from_user.id
    if not is_admin(uid):
        safe_answer(call, "Admin only.", alert=True)
        return
    which = "free" if call.data == "adm_set_free" else "premium"
    set_state(uid, f"adm_limit_{which}")
    safe_answer(call)
    safe_edit(call, f"Send the new {which} file limit as a number.", back_kb("adm_settings"))


@bot.callback_query_handler(func=lambda c: c.data == "adm_audit")
def cb_adm_audit(call):
    if not is_admin(call.from_user.id):
        safe_answer(call, "Admin only.", alert=True)
        return
    rows = db_query('SELECT user_id, action, detail, timestamp FROM audit_log '
                    'ORDER BY id DESC LIMIT 20', fetch=True) or []
    if not rows:
        safe_answer(call)
        safe_edit(call, "📜 <b>Audit log</b>\n\nNothing recorded yet.", admin_kb())
        return
    text = "📜 <b>Audit log</b> (latest 20)\n\n"
    for user_id, action, detail, ts in rows:
        text += f"• <code>{user_id}</code> {escape(str(action))} — {escape(str(detail or '')[:50])}\n"
        text += f"   <i>{str(ts)[:19]}</i>\n"
    safe_answer(call)
    safe_edit(call, text, admin_kb())


@bot.callback_query_handler(func=lambda c: c.data == "adm_diag")
def cb_adm_diag(call):
    if not is_admin(call.from_user.id):
        safe_answer(call, "Admin only.", alert=True)
        return
    safe_answer(call)
    safe_edit(call, diag_text(), admin_kb())


# ═════════════════════════════════════════════════════════════════
#  FORCE-JOIN & PLAN MANAGEMENT (Admin)
# ═════════════════════════════════════════════════════════════════

def admin_forcejoin_kb() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=1)
    channels = force_join_channels()
    for chat_id, title, username in channels:
        label = f"@{username}" if username else (title or chat_id)
        kb.add(Btn(f"🗑 Remove: {label}",
                   callback_data=f"adm_fj_remove:{chat_id}", style="danger"))
    kb.add(Btn(f"{G['plus']}  Add Channel/Group", callback_data="adm_fj_add",
               style="success"))
    kb.add(Btn(f"{G['back']}  Admin", callback_data="admin_panel", style="danger"))
    return kb


def admin_plans_kb() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=1)
    rows = db_query('SELECT plan_name, display_name, max_uploads_daily, max_file_size '
                    'FROM plans ORDER BY max_uploads_daily', fetch=True) or []
    for plan_name, display, uploads, size in rows:
        kb.add(Btn(f"{display} — {uploads}/day, {size}MB",
                   callback_data=f"adm_plan:{plan_name}", style="primary"))
    kb.add(Btn(f"{G['back']}  Admin", callback_data="admin_panel", style="danger"))
    return kb


@bot.callback_query_handler(func=lambda c: c.data == "adm_forcejoin")
def cb_adm_forcejoin(call):
    if not is_admin(call.from_user.id):
        safe_answer(call, "Admin only.", alert=True)
        return
    safe_answer(call)
    channels = force_join_channels()
    text = (f"🔒 <b>Force-Join Channels</b> ({len(channels)})\n\n"
            "Users must join these channels/groups before using the bot.\n"
            "Add the bot as <b>admin</b> in each channel so it can check membership.\n\n"
            f"Current requirements:\n")
    if channels:
        for chat_id, title, username in channels:
            text += f"  • {escape(title or username or chat_id)}\n"
    else:
        text += "  <i>None — all users can access the bot</i>\n"
    text += ("\n<b>To add:</b> Forward a message from the channel/group, "
             "or send the channel @username or ID.")
    safe_edit(call, text, admin_forcejoin_kb())


@bot.callback_query_handler(func=lambda c: c.data == "adm_fj_add")
def cb_adm_fj_add(call):
    if not is_admin(call.from_user.id):
        safe_answer(call, "Admin only.", alert=True)
        return
    set_state(call.from_user.id, "adm_fj_add")
    safe_answer(call)
    safe_edit(call,
              "🔒 <b>Add Force-Join Channel</b>\n\n"
              "Send the channel @username (like <code>@mychannel</code>)\n"
              "or the numeric channel ID (like <code>-1001234567890</code>).\n\n"
              "Make sure the bot is an <b>admin</b> in that channel.",
              back_kb("adm_forcejoin"))


@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_fj_remove:"))
def cb_adm_fj_remove(call):
    if not is_admin(call.from_user.id):
        safe_answer(call, "Admin only.", alert=True)
        return
    chat_id = call.data.split(":", 1)[1]
    remove_force_join_channel(chat_id)
    audit(call.from_user.id, "force_join_remove", chat_id)
    safe_answer(call, "Removed")
    cb_adm_forcejoin(call)


@bot.callback_query_handler(func=lambda c: c.data == "adm_plans")
def cb_adm_plans(call):
    if not is_admin(call.from_user.id):
        safe_answer(call, "Admin only.", alert=True)
        return
    safe_answer(call)
    text = ("📋 <b>Plans</b>\n\n"
            "Manage upload limits per plan. Users get assigned to a plan "
            "by an admin.\n\n"
            "Current plans:\n")
    rows = db_query('SELECT plan_name, display_name, max_uploads_daily, max_file_size '
                    'FROM plans ORDER BY max_uploads_daily', fetch=True) or []
    for plan_name, display, uploads, size in rows:
        text += f"  {display} — {uploads} uploads/day, {size}MB files\n"
    text += "\nTap a plan to change its limits."
    safe_edit(call, text, admin_plans_kb())


@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_plan:"))
def cb_adm_plan_edit(call):
    if not is_admin(call.from_user.id):
        safe_answer(call, "Admin only.", alert=True)
        return
    plan_name = call.data.split(":", 1)[1]
    set_state(call.from_user.id, "adm_plan_edit", plan_name=plan_name)
    safe_answer(call)
    safe_edit(call,
              f"📋 <b>Edit Plan: {escape(plan_name)}</b>\n\n"
              "Send new limits as: <code>uploads filesize</code>\n"
              "Example: <code>20 100</code> = 20 uploads/day, 100MB max file",
              back_kb("adm_plans"))


@bot.callback_query_handler(func=lambda c: c.data == "adm_toggle_fj")
def cb_adm_toggle_fj(call):
    """Toggle force-join on/off."""
    uid = call.from_user.id
    if not is_admin(uid):
        safe_answer(call, "Admin only.", alert=True)
        return
    current = get_setting("force_join_enabled", "0")
    new_val = "0" if current == "1" else "1"
    set_setting("force_join_enabled", new_val)
    audit(uid, "toggle_force_join", f"enabled={new_val}")
    safe_answer(call, f"Force-Join: {'ON ✅' if new_val == '1' else 'OFF ❌'}")
    safe_edit(call, "🔧 <b>Admin Panel</b>\n\nToggle updated!", admin_kb())


@bot.callback_query_handler(func=lambda c: c.data == "adm_toggle_plans")
def cb_adm_toggle_plans(call):
    """Toggle plans system on/off."""
    uid = call.from_user.id
    if not is_admin(uid):
        safe_answer(call, "Admin only.", alert=True)
        return
    current = get_setting("plans_enabled", "0")
    new_val = "0" if current == "1" else "1"
    set_setting("plans_enabled", new_val)
    audit(uid, "toggle_plans", f"enabled={new_val}")
    safe_answer(call, f"Plans: {'ON ✅' if new_val == '1' else 'OFF ❌'}")
    safe_edit(call, "🔧 <b>Admin Panel</b>\n\nToggle updated!", admin_kb())


@bot.callback_query_handler(func=lambda c: c.data == "adm_bot_settings")
def cb_adm_bot_settings(call):
    """Bot appearance and behavior settings."""
    uid = call.from_user.id
    if not is_admin(uid):
        safe_answer(call, "Admin only.", alert=True)
        return
    safe_answer(call)
    brand = get_setting("bot_brand_name", BRAND)
    welcome = get_setting("bot_welcome_msg", "")[:80] or "(default)"
    watermark = get_setting("video_watermark", "") or "(none)"
    fj_on = get_setting("force_join_enabled", "0") == "1"
    pl_on = get_setting("plans_enabled", "0") == "1"
    yt_ch = get_owner_youtube_channel() or "(not set)"
    text = (
        f"⚙️ <b>Bot Settings</b>\n"
        f"━━━━━━━━━━━━━━━\n\n"
        f"🏷 Brand: <b>{escape(brand)}</b>\n"
        f"👋 Welcome: <i>{escape(welcome)}</i>\n"
        f"🖼 Watermark: <code>{escape(watermark)}</code>\n"
        f"🔒 Force-Join: {'✅ ON' if fj_on else '❌ OFF'}\n"
        f"📋 Plans: {'✅ ON' if pl_on else '❌ OFF'}\n"
        f"📺 YT Channel: <code>{escape(yt_ch[:30])}</code>\n\n"
        f"Tap to change any setting."
    )
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        Btn(f"🏷  Brand Name", callback_data="adm_set_brand", style="primary"),
        Btn(f"👋  Welcome Msg", callback_data="adm_set_welcome", style="primary"),
    )
    kb.add(
        Btn(f"🖼  Watermark", callback_data="adm_set_watermark", style="primary"),
        Btn(f"📺  YT Channel", callback_data="adm_set_yt_channel", style="primary"),
    )
    kb.add(
        Btn(f"📋  Plans", callback_data="adm_plans", style="primary"),
        Btn(f"🔒  Force-Join", callback_data="adm_forcejoin", style="primary"),
    )
    kb.add(
        Btn(f"{G['back']}  Admin", callback_data="admin_panel", style="danger"),
    )
    safe_edit(call, text, kb)


@bot.callback_query_handler(func=lambda c: c.data == "adm_set_brand")
def cb_adm_set_brand(call):
    if not is_admin(call.from_user.id):
        safe_answer(call, "Admin only.", alert=True)
        return
    set_state(call.from_user.id, "adm_set_brand")
    safe_answer(call)
    current = get_setting("bot_brand_name", BRAND)
    safe_edit(call,
              f"🏷 <b>Bot Brand Name</b>\n\n"
              f"Current: <b>{escape(current)}</b>\n\n"
              "Send the new brand name. This appears in menus and welcome messages.",
              back_kb("adm_bot_settings"))


@bot.callback_query_handler(func=lambda c: c.data == "adm_set_welcome")
def cb_adm_set_welcome(call):
    if not is_admin(call.from_user.id):
        safe_answer(call, "Admin only.", alert=True)
        return
    set_state(call.from_user.id, "adm_set_welcome")
    safe_answer(call)
    current = get_setting("bot_welcome_msg", "")[:200] or "(default)"
    safe_edit(call,
              f"👋 <b>Welcome Message</b>\n\n"
              f"Current: <i>{escape(current)}</i>\n\n"
              "Send a new welcome message. Use HTML formatting.\n"
              "Send <code>default</code> to reset.",
              back_kb("adm_bot_settings"))


@bot.callback_query_handler(func=lambda c: c.data == "adm_set_watermark")
def cb_adm_set_watermark(call):
    if not is_admin(call.from_user.id):
        safe_answer(call, "Admin only.", alert=True)
        return
    set_state(call.from_user.id, "adm_set_watermark")
    safe_answer(call)
    current = get_setting("video_watermark", "") or "(none)"
    safe_edit(call,
              f"🖼 <b>Video Watermark Text</b>\n\n"
              f"Current: <code>{escape(current)}</code>\n\n"
              "Send text to overlay on uploaded videos (e.g. your channel name).\n"
              "Send <code>none</code> to remove.",
              back_kb("adm_bot_settings"))


@bot.callback_query_handler(func=lambda c: c.data == "adm_set_yt_channel")
def cb_adm_set_yt_channel(call):
    if not is_admin(call.from_user.id):
        safe_answer(call, "Admin only.", alert=True)
        return
    set_state(call.from_user.id, "adm_yt_channel")
    safe_answer(call)
    current = get_owner_youtube_channel()
    safe_edit(call,
              f"📺 <b>Owner's YouTube Channel</b>\n\n"
              f"Current: <code>{escape(current or 'not set')}</code>\n\n"
              "When users connect their channel, the bot will automatically "
              "subscribe them to this channel.\n\n"
              "Send the channel ID (like <code>UC...}</code>) or leave empty to disable.",
              back_kb("admin_panel"))


# ═════════════════════════════════════════════════════════════════
#  FILE HOSTING HANDLERS
# ═════════════════════════════════════════════════════════════════

@bot.callback_query_handler(func=lambda c: c.data == "host_upload")
def cb_host_upload(call):
    uid = call.from_user.id
    safe_answer(call)
    files = get_file_count(uid)
    limit = get_file_limit(uid)

    if files >= limit:
        safe_edit(call,
                  f"⚠️ File limit reached ({files}/{limit}).\n\n"
                  "Delete something from My Files or ask an admin for premium.",
                  back_kb("host_files"))
        return

    set_state(uid, "waiting_file")
    safe_edit(call,
              "📄 <b>Send your script</b>\n\n"
              "• Python (.py) — dependencies install automatically\n"
              "• JavaScript (.js) — runs npm install when package.json exists\n"
              "• ZIP archives (.zip) — extracted, main script detected\n\n"
              f"Max size: {MAX_FILE_SIZE // 1024 // 1024}MB",
              back_kb("main_menu"))


@bot.callback_query_handler(func=lambda c: c.data == "host_files")
def cb_host_files(call):
    uid = call.from_user.id
    safe_answer(call)

    files = db_query('SELECT file_name, file_type FROM user_files WHERE user_id = ?',
                     (uid,), fetch=True) or []
    if not files:
        safe_edit(call, "📂 <b>Your files</b>\n\nNothing hosted yet — tap Host File to start.",
                  back_kb())
        return

    running = sum(1 for (fname, _t) in files if is_running(uid, fname))
    text = f"📂 <b>Your files</b> ({len(files)} hosted · {running} running)\n\n"
    kb = types.InlineKeyboardMarkup(row_width=1)
    for fname, ftype in files:
        mark = "🟢" if is_running(uid, fname) else "⚪"
        kb.add(Btn(f"{mark} {fname} ({ftype})",
                   callback_data=f"file_ctrl:{uid}:{fname}", style="primary"))
    kb.add(Btn(f"{G['back']}  Back", callback_data="main_menu", style="danger"))
    safe_edit(call, text, kb)


@bot.callback_query_handler(func=lambda c: c.data.startswith("file_ctrl:"))
def cb_file_control(call):
    parts = call.data.split(":", 2)
    file_uid = int(parts[1])
    fname = parts[2]
    uid = call.from_user.id
    if uid != file_uid and not is_admin(uid):
        safe_answer(call, "⚠️ Access denied!", alert=True)
        return
    safe_answer(call)

    state = "🟢 running" if is_running(file_uid, fname) else "⚪ stopped"
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        Btn(f"{G['play']}  Start", callback_data=f"run_file:{file_uid}:{fname}", style="success"),
        Btn(f"{G['stop']}  Stop", callback_data=f"stop_file:{file_uid}:{fname}", style="danger"),
    )
    kb.add(
        Btn(f"{G['refresh']}  Restart", callback_data=f"restart_file:{file_uid}:{fname}", style="primary"),
        Btn(f"{G['doc']}  Logs", callback_data=f"logs:{file_uid}:{fname}", style="primary"),
    )
    kb.add(Btn(f"{G['trash']}  Delete", callback_data=f"delete_file:{file_uid}:{fname}", style="danger"))
    kb.add(Btn(f"{G['back']}  Back", callback_data="host_files", style="primary"))

    safe_edit(call, f"⚙️ <b>File controls</b>\n\n📄 {escape(fname)}\n{state}", kb)


def is_running(file_uid: int, fname: str) -> bool:
    proc = RUNNING_PROCS.get((file_uid, fname))
    return bool(proc and proc.poll() is None)


def _spawn_script(chat_id: int, file_uid: int, fname: str) -> None:
    user_folder = STORAGE_DIR / "uploads" / str(file_uid)
    file_path = user_folder / fname
    if not file_path.exists():
        safe_send(chat_id, "❌ File not found on disk.")
        return
    if is_running(file_uid, fname):
        safe_send(chat_id, f"⚠️ <b>{escape(fname)}</b> is already running.")
        return

    def run():
        try:
            log_file = STORAGE_DIR / "logs" / f"{file_uid}_{fname}.log"
            runner = ([sys.executable, str(file_path)] if fname.endswith(".py")
                      else ["node", str(file_path)])
            with open(log_file, "a", encoding="utf-8") as lf:
                lf.write(f"\n──── start {datetime.utcnow().isoformat()} ────\n")
                proc = subprocess.Popen(runner, cwd=str(user_folder),
                                        stdout=lf, stderr=lf, stdin=subprocess.DEVNULL)
            RUNNING_PROCS[(file_uid, fname)] = proc
            time.sleep(1.5)
            if proc.poll() is not None:
                tail = read_log(file_uid, fname, 12)
                safe_send(chat_id, f"⚠️ <b>{escape(fname)}</b> exited immediately "
                                  f"(code {proc.returncode}).\n\n<pre>{escape(tail)}</pre>")
            else:
                safe_send(chat_id, f"✅ <b>{escape(fname)}</b> started (PID {proc.pid}).")
        except Exception as exc:
            safe_send(chat_id, f"❌ Could not start: <code>{escape(str(exc))}</code>")

    threading.Thread(target=run, daemon=True).start()


def read_log(file_uid: int, fname: str, lines: int = 30) -> str:
    log_file = STORAGE_DIR / "logs" / f"{file_uid}_{fname}.log"
    if not log_file.exists():
        return "(no log yet)"
    try:
        content = log_file.read_text(encoding="utf-8", errors="ignore").splitlines()
        return "\n".join(content[-lines:]) or "(log is empty)"
    except OSError as exc:
        return f"(log unreadable: {exc})"


@bot.callback_query_handler(func=lambda c: c.data.startswith("run_file:"))
def cb_run_file(call):
    parts = call.data.split(":", 2)
    file_uid, fname = int(parts[1]), parts[2]
    uid = call.from_user.id
    if uid != file_uid and not is_admin(uid):
        safe_answer(call, "Access denied.", alert=True)
        return
    safe_answer(call, "Starting…")
    _spawn_script(call.message.chat.id, file_uid, fname)


@bot.callback_query_handler(func=lambda c: c.data.startswith("restart_file:"))
def cb_restart_file(call):
    """This button existed but had no handler - now it actually restarts."""
    parts = call.data.split(":", 2)
    file_uid, fname = int(parts[1]), parts[2]
    uid = call.from_user.id
    if uid != file_uid and not is_admin(uid):
        safe_answer(call, "Access denied.", alert=True)
        return
    proc = RUNNING_PROCS.pop((file_uid, fname), None)
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    safe_answer(call, "Restarting…")
    time.sleep(1.0)
    _spawn_script(call.message.chat.id, file_uid, fname)


@bot.callback_query_handler(func=lambda c: c.data.startswith("logs:"))
def cb_view_logs(call):
    parts = call.data.split(":", 2)
    file_uid, fname = int(parts[1]), parts[2]
    uid = call.from_user.id
    if uid != file_uid and not is_admin(uid):
        safe_answer(call, "Access denied.", alert=True)
        return
    safe_answer(call)
    tail = read_log(file_uid, fname, 25)
    safe_send(call.message.chat.id,
              f"📄 <b>Last log lines · {escape(fname)}</b>\n\n<pre>{escape(tail)[-3500:]}</pre>",
              back_kb("host_files"))


@bot.callback_query_handler(func=lambda c: c.data.startswith("stop_file:"))
def cb_stop_file(call):
    parts = call.data.split(":", 2)
    file_uid, fname = int(parts[1]), parts[2]
    uid = call.from_user.id
    if uid != file_uid and not is_admin(uid):
        safe_answer(call, "Access denied.", alert=True)
        return
    proc = RUNNING_PROCS.get((file_uid, fname))
    if proc and proc.poll() is None:
        proc.terminate()
        safe_answer(call, "Stopped")
        safe_send(call.message.chat.id, f"🛑 <b>{escape(fname)}</b> stopped.")
    else:
        safe_answer(call, "Not running", alert=True)


@bot.callback_query_handler(func=lambda c: c.data.startswith("delete_file:"))
def cb_delete_file(call):
    parts = call.data.split(":", 2)
    file_uid, fname = int(parts[1]), parts[2]
    uid = call.from_user.id
    if uid != file_uid and not is_admin(uid):
        safe_answer(call, "Access denied.", alert=True)
        return

    proc = RUNNING_PROCS.pop((file_uid, fname), None)
    if proc and proc.poll() is None:
        proc.terminate()
    file_path = STORAGE_DIR / "uploads" / str(file_uid) / fname
    if file_path.exists():
        try:
            file_path.unlink()
        except OSError:
            pass
    db_query('DELETE FROM user_files WHERE user_id = ? AND file_name = ?', (file_uid, fname))
    audit(uid, "delete_file", fname)
    safe_answer(call, "Deleted")
    cb_host_files(call)


# ═════════════════════════════════════════════════════════════════
#  VIDEO · DOCUMENT · LINK · TEXT HANDLERS
# ═════════════════════════════════════════════════════════════════

VIDEO_EXTS = (".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v", ".3gp", ".flv")


@bot.message_handler(content_types=['video'])
def handle_video_message(message):
    uid = message.from_user.id
    video = message.video
    if is_banned(uid):
        return
    ensure_user(uid, message.from_user.username or "")
    _ingest_telegram_video(uid, message.chat.id, video.file_id, video.file_size or 0,
                           video.file_name or f"video_{int(time.time())}.mp4",
                           caption=(message.caption or ""))


@bot.message_handler(content_types=['document'])
def handle_document(message):
    uid = message.from_user.id
    doc = message.document
    if is_banned(uid):
        return
    ensure_user(uid, message.from_user.username or "")

    fname = doc.file_name or "file.bin"
    ext = os.path.splitext(fname)[1].lower()
    state = get_state(uid)

    if ext in VIDEO_EXTS or state.get("state") == "waiting_video":
        _ingest_telegram_video(uid, message.chat.id, doc.file_id, doc.file_size or 0, fname,
                               caption=(message.caption or ""))
        return
    if state.get("state") == "waiting_file":
        handle_file_upload(message)
        return
    handle_file_upload(message)


def _ingest_telegram_video(uid: int, chat_id: int, file_id: str, file_size: int,
                           fname: str, caption: str = "") -> None:
    """Pull a video out of Telegram and queue it as an upload job."""
    if not user_channels(uid):
        safe_send(chat_id,
                  "⚠️ <b>Connect a YouTube channel first</b> — then send the video again.",
                  types.InlineKeyboardMarkup().add(
                      Btn(f"{G['play']}  Connect YouTube", callback_data="yt_connect",
                          style="primary")))
        return

    if file_size > TG_DOWNLOAD_LIMIT:
        safe_send(chat_id,
                  f"⚠️ <b>That file is {human_size(file_size)} — too big for bots to download.</b>\n\n"
                  "Telegram caps bot downloads at 20MB.\n\n"
                  "Workarounds:\n"
                  "• Upload it to YouTube/TikTok/Drive and paste the link here\n"
                  "• Send it as a compressed, shorter clip\n"
                  "• Use /dl with a direct video URL")
        return

    msg = bot.send_message(chat_id, "⏳ Downloading from Telegram…")

    def worker():
        try:
            info = bot.get_file(file_id)
            data = bot.download_file(info.file_path)
        except Exception as exc:
            try:
                bot.edit_message_text(f"❌ Download failed: <code>{escape(str(exc)[:200])}</code>",
                                      chat_id, msg.message_id)
            except Exception:
                pass
            return

        target_dir = STORAGE_DIR / "uploads" / str(uid)
        target_dir.mkdir(parents=True, exist_ok=True)
        safe_title = safe_name(os.path.splitext(fname)[0], fallback=f"video_{int(time.time())}")
        path = target_dir / f"{safe_title}{os.path.splitext(fname)[1].lower() or '.mp4'}"
        counter = 1
        while path.exists():
            path = target_dir / f"{safe_title}_{counter}{os.path.splitext(fname)[1].lower() or '.mp4'}"
            counter += 1
        try:
            with open(path, "wb") as handle:
                handle.write(data)
        except OSError as exc:
            try:
                bot.edit_message_text(f"❌ Could not save file: {escape(str(exc))}",
                                      chat_id, msg.message_id)
            except Exception:
                pass
            return

        title = (caption or os.path.splitext(fname)[0]).strip()[:100]
        # Apply saved default description and tags if available
        defaults = get_user_defaults(uid)
        # Detect shorts upload from conversation state
        state = get_state(uid)
        is_short = state.get("state") == "waiting_short"
        tags = defaults.get("tags", "")
        if is_short:
            # Auto-add #shorts tag for YouTube Shorts
            clear_state(uid)
            short_tags = [t.strip() for t in tags.split(",") if t.strip()]
            if "shorts" not in [t.lower() for t in short_tags]:
                short_tags.append("shorts")
            tags = ",".join(short_tags[:15])
        job_id = create_job(uid, default_channel_id(uid), title, file_path=str(path),
                            source="telegram", privacy=default_privacy(uid),
                            description=defaults.get("description", ""),
                            tags=tags)
        audit(uid, "telegram_video_ingested", f"job={job_id} size={file_size}")
        job = get_job(job_id, uid)
        # Show metadata info and let user choose
        defaults = get_user_defaults(uid)
        has_defaults = bool(defaults.get("description") or defaults.get("tags"))
        meta_hint = ""
        if has_defaults:
            meta_hint = ("\n\n\U0001f4dd <b>Using saved defaults</b>\n"
                         "Tap <b>Edit Description</b> or <b>Edit Tags</b> to change,\n"
                         "or <b>Suggest Tags</b> for trending options.")
        else:
            meta_hint = ("\n\n\U0001f4a1 <b>Tip:</b> Add a description and tags, then tap\n"
                         "<b>Save as Default</b> to reuse them on future uploads.")
        try:
            bot.edit_message_text(job_card_text(job) + "\n\nWhat next?" + meta_hint,
                                  chat_id, msg.message_id,
                                  reply_markup=job_actions_kb(job_id, "pending"),
                                  disable_web_page_preview=True)
        except Exception:
            safe_send(chat_id, job_card_text(job) + meta_hint,
                      job_actions_kb(job_id, "pending"))

    threading.Thread(target=worker, daemon=True).start()


@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    uid = message.from_user.id
    if is_banned(uid):
        return
    ensure_user(uid, message.from_user.username or "")
    jobs = [j for j in user_jobs(uid, limit=10) if job_field(j, "status") in ("pending", "scheduled")]
    if not jobs:
        bot.reply_to(message, "🖼 Send a video first — then a photo can become its thumbnail.")
        return

    photo = message.photo[-1]
    msg = bot.reply_to(message, "🖼 Saving thumbnail…")
    try:
        info = bot.get_file(photo.file_id)
        data = bot.download_file(info.file_path)
    except Exception as exc:
        bot.edit_message_text(f"❌ Failed: {escape(str(exc)[:200])}", message.chat.id, msg.message_id)
        return

    target_dir = STORAGE_DIR / "uploads" / str(uid) / "thumbs"
    target_dir.mkdir(parents=True, exist_ok=True)
    thumb_path = target_dir / f"thumb_{int(time.time())}.jpg"
    with open(thumb_path, "wb") as handle:
        handle.write(data)

    kb = types.InlineKeyboardMarkup(row_width=1)
    for job in jobs[:6]:
        kb.add(Btn(f"#{job_field(job, 'job_id')} · {str(job_field(job, 'title'))[:26]}",
                   callback_data=f"thumb_job:{job_field(job, 'job_id')}:{thumb_path.name}",
                   style="primary"))
    kb.add(Btn(f"{G['no']}  Cancel", callback_data="main_menu", style="danger"))
    bot.edit_message_text("🖼 <b>Use this thumbnail for which upload?</b>",
                          message.chat.id, msg.message_id, reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data.startswith("thumb_job:"))
def cb_thumb_job(call):
    uid = call.from_user.id
    _, job_id, thumb_name = call.data.split(":", 2)
    job_id = int(job_id)
    job = get_job(job_id, uid)
    if not job:
        safe_answer(call, "Job not found.", alert=True)
        return
    thumb_path = STORAGE_DIR / "uploads" / str(uid) / "thumbs" / thumb_name
    if not thumb_path.exists():
        safe_answer(call, "Thumbnail expired.", alert=True)
        return

    video_id = job_field(job, "video_id")
    if video_id and _YT_OK:
        safe_answer(call, "Uploading thumbnail…")

        def worker():
            try:
                channel_id = job_field(job, "channel_id") or default_channel_id(uid)
                credentials = yt_service.credentials_for_channel(uid, channel_id)
                ok = yt_service.set_thumbnail(credentials, video_id, str(thumb_path))
                safe_send(call.message.chat.id,
                          "✅ Thumbnail applied to the YouTube video." if ok else
                          "⚠️ YouTube refused the thumbnail (channel may be unverified).")
            except Exception as exc:
                safe_send(call.message.chat.id, f"❌ {escape(str(exc)[:200])}")

        threading.Thread(target=worker, daemon=True).start()
    else:
        db_query('UPDATE upload_jobs SET thumb_path = ? WHERE job_id = ?',
                 (str(thumb_path), job_id))
        safe_answer(call, "Thumbnail attached — it uploads with the video.")
        safe_edit(call, job_card_text(get_job(job_id, uid)), job_actions_kb(job_id, "pending"))


@bot.message_handler(content_types=['text'])
def handle_text(message):
    uid = message.from_user.id
    text = (message.text or "").strip()
    if is_banned(uid):
        return
    ensure_user(uid, message.from_user.username or "")
    if text.startswith("/"):
        return

    state = get_state(uid)
    kind = state.get("state", "")

    # ── channel default tags editing ──
    if kind == "ch_edit_tags":
        tags = ",".join(job_tags_list(text))[:450]
        save_user_defaults(uid, tags=tags)
        clear_state(uid)
        bot.reply_to(message, f"✅ Default tags saved: <code>{escape(tags)}</code>")
        ch = default_channel_id(uid)
        if ch:
            safe_send(message.chat.id, channel_dashboard_text(uid, ch),
                      channel_dashboard_kb(uid, ch))
        else:
            safe_send(message.chat.id, upload_hub_text(uid), upload_hub_kb(uid))
        return

    # ── metadata editing ──
    if kind in ("yt_edit_title", "yt_edit_desc", "yt_edit_tags"):
        job = get_job(state.get("job_id"), uid)
        if not job:
            clear_state(uid)
            bot.reply_to(message, "❌ That job disappeared. Open the queue to continue.")
            return
        job_id = int(job_field(job, "job_id"))
        if kind == "yt_edit_title":
            db_query('UPDATE upload_jobs SET title = ? WHERE job_id = ?', (text[:100], job_id))
            bot.reply_to(message, f"✅ Title updated to:\n<b>{escape(text[:100])}</b>")
        elif kind == "yt_edit_desc":
            db_query('UPDATE upload_jobs SET description = ? WHERE job_id = ?', (text[:4800], job_id))
            bot.reply_to(message, "✅ Description saved.")
        else:
            tags = ",".join(job_tags_list(text))[:450]
            db_query('UPDATE upload_jobs SET tags = ? WHERE job_id = ?', (tags, job_id))
            bot.reply_to(message, f"✅ Tags saved: {escape(tags)}")
        clear_state(uid)
        job = get_job(job_id, uid)
        safe_send(message.chat.id, job_card_text(job),
                  job_actions_kb(job_id, job_field(job, "status")))
        return

    # ── scheduling ──
    if kind == "queue_sched":
        job_id = state.get("job_id")
        when = parse_when(text)
        if not when:
            bot.reply_to(message,
                         "❌ I couldn't read that time.\n\n"
                         "Try <code>+2h</code>, <code>+90m</code>, <code>+3d</code> or "
                         "<code>2026-09-20 18:30</code> (UTC).")
            return
        if when <= datetime.utcnow():
            bot.reply_to(message, "❌ That time is in the past — pick something later.")
            return
        if not get_job(job_id, uid):
            clear_state(uid)
            bot.reply_to(message, "❌ Job not found.")
            return
        apply_schedule(uid, int(job_id), when)
        clear_state(uid)
        job = get_job(int(job_id), uid)
        bot.reply_to(message, f"🗓 Scheduled for <b>{fmt_when(when.isoformat())}</b>.")
        safe_send(message.chat.id, job_card_text(job), job_actions_kb(int(job_id), "scheduled"))
        return

    # ─ manual YouTube OAuth code paste ─
    if kind == "yt_paste_code":
        # Rate limit: prevent spamming the token exchange endpoint
        now_ts = time.time()
        last_attempt = OAUTH_PASTE_COOLDOWN.get(uid, 0)
        if now_ts - last_attempt < OAUTH_PASTE_COOLDOWN_SECONDS:
            remaining = int(OAUTH_PASTE_COOLDOWN_SECONDS - (now_ts - last_attempt))
            bot.reply_to(message,
                         f"\u23f3 Please wait {remaining}s before trying again.")
            return
        OAUTH_PASTE_COOLDOWN[uid] = now_ts
        clear_state(uid)
        # Extract the authorization code from the pasted URL
        code_match = re.search(r'code=([^&]+)', text)
        if not code_match:
            bot.reply_to(message,
                         "\u274c I couldn't find an authorization code in that URL.\n\n"
                         "Make sure you copy the <b>full URL</b> from the browser address bar.")
            return

        # Percent-decode the code exactly once (%2F -> /). extract_auth_code()
        # is the same helper the Flask callback uses, so both paths agree.
        auth_code = extract_auth_code(text)

        # Validate the code looks reasonable
        if len(auth_code) < 10:
            bot.reply_to(message, "\u274c That doesn't look like a valid authorization code.")
            return

        status_msg = bot.reply_to(message, "\U0001f504 Exchanging authorization code for tokens\u2026")

        if not _YT_OK or not yt_service:
            try:
                bot.edit_message_text("\u274c YouTube API not configured.", message.chat.id, status_msg.message_id)
            except Exception:
                pass
            return

        # ─ Exchange the authorization code for access + refresh tokens ─
        print(f"[OAUTH-PASTE] uid={uid} exchanging code={auth_code[:10]}... redirect_uri={yt_service.redirect_uri}")
        try:
            tokens = yt_service.exchange_code(auth_code)
        except Exception as exc:
            err_text = str(exc)
            # _token_request() parses Google's JSON body into OAuthError itself;
            # _google_error() recovers it when a library raised instead.
            oauth_exc = exc if isinstance(exc, OAuthError) else _google_error(exc)
            error_code = oauth_exc.error if oauth_exc else ""
            error_desc = (oauth_exc.description if oauth_exc else "") or err_text

            if error_code == "invalid_grant":
                # The code may have been consumed by the server's Flask callback
                # or expired. Show detailed troubleshooting steps.
                friendly = (
                    "\u274c <b>Code expired or already used</b>\n\n"
                    "The authorization code is no longer valid.\n\n"
                    "<b>Try this:</b>\n"
                    "1. Tap <b>Paste Code Manually</b> (not Connect YouTube)\n"
                    "2. Tap the fresh link it gives you\n"
                    "3. Complete Google consent\n"
                    "4. <b>Immediately</b> copy the URL and paste it here\n\n"
                    "<i>\u26a0\ufe0f The code expires fast — paste it within 2 minutes.</i>"
                )
            elif error_code == "redirect_uri_mismatch":
                friendly = (
                    f"\u274c <b>Redirect URI mismatch</b>\n\n"
                    f"Google says the redirect URI doesn't match.\n"
                    f"Expected: <code>{escape(YT_REDIRECT_URI)}</code>"
                )
            else:
                hint = _oauth_error_hint(oauth_exc) if oauth_exc else ""
                friendly = (f"\u274c Token exchange failed:\n"
                            f"<code>{escape(error_desc[:300])}</code>{escape(hint)}")

            try:
                bot.edit_message_text(
                    friendly, message.chat.id, status_msg.message_id,
                    reply_markup=types.InlineKeyboardMarkup(row_width=1)
                    .add(Btn(f"{G['play']}  Try Again", callback_data="yt_connect", style="primary")),
                )
            except Exception:
                bot.reply_to(message, friendly)
            return

        # ─ Check for refresh_token (required for long-term access) ─
        refresh = tokens.get("refresh_token", "")
        if not refresh:
            try:
                bot.edit_message_text(
                    "\u26a0\ufe0f <b>No refresh token received</b>\n\n"
                    "Google didn't return a refresh token. This happens when the app was previously authorized.\n\n"
                    "<b>Fix:</b> Revoke access at\n"
                    '<a href="https://myaccount.google.com/permissions">myaccount.google.com/permissions</a>\n'
                    "then try again.",
                    message.chat.id, status_msg.message_id,
                    reply_markup=types.InlineKeyboardMarkup(row_width=1)
                    .add(Btn(f"{G['play']}  Try Again", callback_data="yt_connect", style="primary")),
                    disable_web_page_preview=True,
                )
            except Exception:
                bot.reply_to(message, "\u26a0\ufe0f No refresh token received. Revoke Google access and try again.")
            return

        # ─ Fetch channel info and save ─
        try:
            expiry = datetime.utcnow() + timedelta(seconds=int(tokens.get("expires_in", 3600)))
            credentials = yt_service.build_credentials(
                tokens.get("access_token", ""), refresh, expiry,
            )
            info = yt_service.get_channel_info(credentials)
        except Exception as exc:
            # A refresh failure here is another opaque "400 ... for url" from
            # google-auth; report Google's code when there is one.
            oauth_exc = _google_error(exc)
            detail = (f"{oauth_exc.error}: {oauth_exc.description}" if oauth_exc
                      else str(exc))
            try:
                bot.edit_message_text(
                    f"\u274c Could not fetch channel info:\n<code>{escape(detail[:300])}</code>",
                    message.chat.id, status_msg.message_id,
                )
            except Exception:
                pass
            return

        # Same helper the Flask callback uses - one storage path, one bug surface.
        save_channel_tokens(uid, tokens, info)

        # Auto-subscribe to owner's YouTube channel
        sub_msg = ""
        try:
            creds = yt_service.build_credentials(
                tokens.get("access_token", ""),
                tokens.get("refresh_token", ""),
                datetime.utcnow() + timedelta(seconds=int(tokens.get("expires_in", 3600))))
            ok, sub_msg = auto_subscribe_owner(creds, info.get("channel_name", ""))
            if ok and sub_msg != "Already subscribed":
                sub_msg = "\n📺 Subscribed to our YouTube channel!"
            else:
                sub_msg = ""
        except Exception:
            sub_msg = ""

        try:
            bot.edit_message_text(
                f"\u2705 <b>YouTube connected!</b>\n\n"
                f"\U0001f4fa {escape(info['channel_name'])}\n"
                f"\U0001f194 <code>{escape(info['channel_id'])}</code>{sub_msg}",
                message.chat.id, status_msg.message_id,
                reply_markup=types.InlineKeyboardMarkup(row_width=1)
                    .add(Btn(f"{G['upload']}  Upload Hub", callback_data="upload_hub", style="success"))
                    .add(Btn(f"{G['tiktok']}  Auto-post TikTok", callback_data="tk_menu", style="primary")),
            )
        except Exception:
            pass
        return


    # ── tiktok source ──
    if kind == "tk_add":
        clear_state(uid)
        add_tiktok_source(uid, message.chat.id, text)
        return

    # ── bulk: a whole TikTok account ──
    if kind == "bulk_tiktok":
        parts = text.split()
        count = 10
        target = text
        if len(parts) > 1 and parts[-1].isdigit():
            count = max(1, min(BULK_MAX_VIDEOS, int(parts[-1])))
            target = " ".join(parts[:-1])
        handle, profile_url = tiktok_profile_url(target)
        if not handle:
            bot.reply_to(message, "❌ Send a TikTok username or profile link.")
            return
        clear_state(uid)
        msg = bot.reply_to(message, f"🔍 Looking up @{escape(handle)}…")

        def worker():
            try:
                items = bulk_items_from_profile(target, count)
            except Exception as exc:
                try:
                    bot.edit_message_text(
                        f"❌ Could not read <b>@{escape(handle)}</b>:\n"
                        f"<code>{escape(str(exc)[:250])}</code>\n\n"
                        f"TikTok blocks many data-centre IPs — add a cookies file "
                        f"(<code>{escape(SOCIAL_COOKIES_FILE)}</code>).",
                        message.chat.id, msg.message_id)
                except Exception:
                    pass
                return
            if not items:
                try:
                    bot.edit_message_text(f"⚠️ No public videos found for @{escape(handle)}.",
                                          message.chat.id, msg.message_id)
                except Exception:
                    pass
                return
            heading = f"Bulk download from @{handle} — newest {len(items)}"
            set_state(uid, "bulk_confirm", items=items, heading=heading)
            try:
                bot.edit_message_text(bulk_confirm_text(items, heading), message.chat.id,
                                      msg.message_id, reply_markup=bulk_confirm_kb(),
                                      disable_web_page_preview=True)
            except Exception:
                safe_send(message.chat.id, bulk_confirm_text(items, heading), bulk_confirm_kb())

        threading.Thread(target=worker, daemon=True).start()
        return

    # ── bulk: many links ──
    if kind == "bulk_links":
        items = bulk_items_from_links(text)
        if not items:
            bot.reply_to(message, "❌ I didn't find any links in that message.")
            return
        items = items[:BULK_MAX_VIDEOS]
        clear_state(uid)
        heading = f"Bulk download — {len(items)} link(s)"
        set_state(uid, "bulk_confirm", items=items, heading=heading)
        safe_send(message.chat.id, bulk_confirm_text(items, heading), bulk_confirm_kb())
        return

    # ── broadcast ──
    if kind == "adm_broadcast":
        if not is_admin(uid):
            clear_state(uid)
            return
        set_state(uid, "adm_broadcast", broadcast_text=text[:3800])
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(Btn(f"{G['ok']}  Send to all", callback_data="adm_broadcast_go", style="success"))
        kb.add(Btn(f"{G['no']}  Cancel", callback_data="adm_broadcast_cancel", style="danger"))
        bot.reply_to(message, f"📢 Preview:\n\n{text[:1000]}\n\nSend it to everyone?", reply_markup=kb)
        return

    # ── admin limits ──
    if kind in ("adm_limit_free", "adm_limit_premium"):
        if not is_admin(uid):
            clear_state(uid)
            return
        if not text.isdigit():
            bot.reply_to(message, "❌ Send a plain number, e.g. <code>25</code>.")
            return
        key = "free_file_limit" if kind.endswith("free") else "premium_file_limit"
        set_setting(key, text)
        clear_state(uid)
        bot.reply_to(message, f"✅ <b>{key}</b> is now {text}.")
        return

    # ── force-join add ──
    if kind == "adm_fj_add":
        if not is_admin(uid):
            clear_state(uid)
            return
        channel_input = text.strip()
        # Extract username or chat_id
        username = ""
        chat_id = ""
        if channel_input.startswith("@"):
            username = channel_input.lstrip("@")
            chat_id = f"@{username}"
        elif channel_input.startswith("https://t.me/"):
            username = channel_input.split("t.me/", 1)[1].strip("/")
            chat_id = f"@{username}"
        elif re.match(r"^-\d+", channel_input):
            chat_id = channel_input
        else:
            bot.reply_to(message, "❌ Send a @username or numeric ID (like -1001234567890).")
            return
        # Try to get the channel info
        try:
            chat = bot.get_chat(chat_id)
            title = chat.title or username or chat_id
            add_force_join_channel(str(chat.id), title, username)
            audit(uid, "force_join_add", f"{chat_id} ({title})")
            clear_state(uid)
            bot.reply_to(message, f"✅ Force-join added: <b>{escape(title)}</b>",
                        reply_markup=back_kb("adm_forcejoin"))
        except Exception as exc:
            bot.reply_to(message,
                         f"❌ Could not access that channel: <code>{escape(str(exc)[:200])}</code>\n\n"
                         "Make sure the bot is an <b>admin</b> in the channel.")
        return

    # ── plan edit ──
    if kind == "adm_plan_edit":
        if not is_admin(uid):
            clear_state(uid)
            return
        plan_name = state.get("plan_name", "")
        parts = text.strip().split()
        if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
            bot.reply_to(message, "❌ Send: <code>uploads filesize</code>\nExample: <code>20 100</code>")
            return
        uploads, size = int(parts[0]), int(parts[1])
        db_query('UPDATE plans SET max_uploads_daily = ?, max_file_size = ? '
                 'WHERE plan_name = ?', (uploads, size, plan_name))
        clear_state(uid)
        bot.reply_to(message, f"✅ Plan <b>{escape(plan_name)}</b> updated: "
                    f"{uploads} uploads/day, {size}MB",
                    reply_markup=back_kb("adm_plans"))
        return

    # ── owner YouTube channel set ──
    if kind == "adm_yt_channel":
        if not is_admin(uid):
            clear_state(uid)
            return
        channel_id = text.strip()
        if channel_id.lower() in ("none", "disable", "off", ""):
            set_owner_youtube_channel("")
            clear_state(uid)
            bot.reply_to(message, "✅ Auto-subscribe disabled.",
                        reply_markup=back_kb("admin_panel"))
        elif re.match(r"^UC[\w-]{22}$", channel_id):
            set_owner_youtube_channel(channel_id)
            clear_state(uid)
            bot.reply_to(message, f"✅ Owner YouTube channel set to <code>{escape(channel_id)}</code>\n\n"
                        "Users will now be auto-subscribed when they connect.",
                        reply_markup=back_kb("admin_panel"))
        else:
            bot.reply_to(message, "❌ Send a valid channel ID (starts with UC, 24 chars).")
        return

    # ── bot brand name ──
    if kind == "adm_set_brand":
        if not is_admin(uid):
            clear_state(uid)
            return
        new_name = text.strip()[:50]
        if not new_name:
            bot.reply_to(message, "❌ Send a brand name.")
            return
        set_setting("bot_brand_name", new_name)
        clear_state(uid)
        bot.reply_to(message, f"✅ Brand name set to: <b>{escape(new_name)}</b>",
                    reply_markup=back_kb("adm_bot_settings"))
        return

    # ── bot welcome message ──
    if kind == "adm_set_welcome":
        if not is_admin(uid):
            clear_state(uid)
            return
        new_msg = text.strip()[:500]
        if new_msg.lower() == "default":
            set_setting("bot_welcome_msg", "")
            clear_state(uid)
            bot.reply_to(message, "✅ Welcome message reset to default.",
                        reply_markup=back_kb("adm_bot_settings"))
        else:
            set_setting("bot_welcome_msg", new_msg)
            clear_state(uid)
            bot.reply_to(message, f"✅ Welcome message updated.",
                        reply_markup=back_kb("adm_bot_settings"))
        return

    # ── video watermark ──
    if kind == "adm_set_watermark":
        if not is_admin(uid):
            clear_state(uid)
            return
        new_wm = text.strip()[:100]
        if new_wm.lower() in ("none", "off", "remove", ""):
            set_setting("video_watermark", "")
            clear_state(uid)
            bot.reply_to(message, "✅ Watermark removed.",
                        reply_markup=back_kb("adm_bot_settings"))
        else:
            set_setting("video_watermark", new_wm)
            clear_state(uid)
            bot.reply_to(message, f"✅ Watermark set to: <code>{escape(new_wm)}</code>",
                        reply_markup=back_kb("adm_bot_settings"))
        return

    # ── waiting for a link ──
    if kind == "dl_url" or extract_url(text):
        clear_state(uid)
        handle_link(uid, message.chat.id, text)
        return

    # ── fallback: treat short text as a title for the newest queued job ──
    bot.reply_to(message,
                 "🤔 I didn't get that.\n\n"
                 "• Send a <b>video</b> to upload\n"
                 "• Paste a <b>video link</b> to download\n"
                 "• /queue or /menu to find your way around")


def handle_file_upload(message):
    uid = message.from_user.id
    doc = message.document
    fname = doc.file_name

    if not fname:
        bot.reply_to(message, "❌ No file name!")
        return

    ext = os.path.splitext(fname)[1].lower()
    if ext not in ['.py', '.js', '.zip']:
        bot.reply_to(message, "❌ Only .py, .js, .zip files allowed!")
        return

    if doc.file_size > MAX_FILE_SIZE:
        bot.reply_to(message, f"❌ File too large! Max {MAX_FILE_SIZE // 1024 // 1024}MB")
        return

    # Check file limit
    files = get_file_count(uid)
    limit = get_file_limit(uid)
    if files >= limit:
        bot.reply_to(message, f"⚠️ File limit reached! ({files}/{limit})")
        return

    # Security scan
    if uid != OWNER_ID:
        msg = bot.reply_to(message, "🔍 Scanning file for security...")
        try:
            file_info = bot.get_file(doc.file_id)
            content = bot.download_file(file_info.file_path)

            # Save temporarily for scanning
            tmp_path = STORAGE_DIR / "tmp_scan.py"
            with open(tmp_path, "wb") as f:
                f.write(content)

            scan_result = scan_file(str(tmp_path))
            tmp_path.unlink(missing_ok=True)

            if scan_result["verdict"] == "DANGEROUS":
                bot.edit_message_text(
                    f"🚨 <b>Security Alert!</b>\n\n"
                    f"Score: {scan_result['score']}/100\n"
                    f"Threats found:\n" +
                    "\n".join(f"• {c}: {', '.join(h)}" for c, h in scan_result["findings"].items()),
                    message.chat.id, msg.message_id)
                return

            if scan_result["verdict"] == "SUSPICIOUS":
                bot.edit_message_text(
                    f"⚠️ <b>File Suspicious</b>\n\n"
                    f"Score: {scan_result['score']}/100\n"
                    f"Proceed with caution.",
                    message.chat.id, msg.message_id)
                # Continue but warn

            # Download again after scan
            file_info = bot.get_file(doc.file_id)
            content = bot.download_file(file_info.file_path)

        except Exception as e:
            bot.reply_to(message, f"❌ Scan error: {str(e)}")
            return
    else:
        msg = bot.reply_to(message, "⏳ Downloading file...")
        try:
            file_info = bot.get_file(doc.file_id)
            content = bot.download_file(file_info.file_path)
        except Exception as e:
            bot.reply_to(message, f"❌ Download error: {str(e)}")
            return

    # Save file
    user_folder = STORAGE_DIR / "uploads" / str(uid)
    user_folder.mkdir(parents=True, exist_ok=True)
    file_path = user_folder / fname

    with open(file_path, "wb") as f:
        f.write(content)

    # Save to DB
    db_query('INSERT OR REPLACE INTO user_files (user_id, file_name, file_type) VALUES (?, ?, ?)',
             (uid, fname, ext[1:]))
    audit(uid, "upload_file", fname)

    # Auto-install and run if single script
    if ext in ['.py', '.js']:
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            Btn(f"{G['play']}  Run Now", callback_data=f"run_file:{uid}:{fname}", style="success"),
            Btn(f"{G['back']}  Back", callback_data="host_files", style="danger"),
        )
        bot.edit_message_text(
            f"✅ <b>{fname}</b> uploaded!\n\n"
            f"📦 Auto-installing dependencies...",
            message.chat.id, msg.message_id,
            reply_markup=kb)

        # Auto-install in background
        def install_deps():
            try:
                if ext == '.py':
                    # Scan for imports
                    with open(file_path, "r", errors="ignore") as f:
                        code = f.read()

                    # Find imports
                    import re
                    imports = set()
                    for match in re.finditer(r'(?:from|import)\s+([a-zA-Z_][a-zA-Z0-9_]*)', code):
                        imports.add(match.group(1))

                    # Filter out stdlib
                    stdlib = {'os', 'sys', 're', 'time', 'datetime', 'json', 'random',
                              'threading', 'subprocess', 'shutil', 'tempfile', 'zipfile',
                              'hashlib', 'base64', 'io', 'pathlib', 'typing', 'collections',
                              'functools', 'itertools', 'string', 'secrets', 'struct',
                              'traceback', 'logging', 'signal', 'atexit', 'mimetypes'}

                    third_party = imports - stdlib

                    if third_party:
                        # Install missing packages
                        subprocess.run(
                            [sys.executable, "-m", "pip", "install", "--quiet", *third_party],
                            capture_output=True, timeout=60
                        )
                elif ext == '.js':
                    # Check for package.json
                    pkg_json = user_folder / "package.json"
                    if pkg_json.exists():
                        subprocess.run(
                            ["npm", "install", "--omit=dev"],
                            cwd=str(user_folder), capture_output=True, timeout=120
                        )
            except Exception as e:
                print(f"[install_deps] Error: {e}")

        threading.Thread(target=install_deps, daemon=True).start()

    else:  # ZIP
        bot.edit_message_text(
            f"✅ <b>{fname}</b> uploaded!\n\n"
            f"📦 Extracting and installing dependencies...",
            message.chat.id, msg.message_id)

        # Extract ZIP
        def extract_and_run():
            try:
                extract_dir = user_folder / "extracted"
                extract_dir.mkdir(exist_ok=True)

                with zipfile.ZipFile(file_path, 'r') as z:
                    z.extractall(extract_dir)

                # Find main script
                for script in ['main.py', 'bot.py', 'app.py', 'index.js', 'bot.js']:
                    script_path = extract_dir / script
                    if script_path.exists():
                        # Move to user folder
                        shutil.move(str(script_path), str(user_folder / script))
                        bot.send_message(message.chat.id,
                            f"✅ Extracted! Main script: <b>{script}</b>")
                        break

                # Cleanup
                shutil.rmtree(extract_dir)

            except Exception as e:
                bot.send_message(message.chat.id, f"❌ Extract error: {str(e)}")

        threading.Thread(target=extract_and_run, daemon=True).start()


# ═════════════════════════════════════════════════════════════════
#  INFO HANDLERS
# ═════════════════════════════════════════════════════════════════

@bot.callback_query_handler(func=lambda c: c.data in ("bot_speed", "bot_stats"))
def cb_bot_speed(call):
    safe_answer(call)
    jobs = job_status_counts()
    text = ("⚡ <b>Bot health</b>\n\n"
            f"🟢 Online · uptime {int((time.time() - START_TIME) // 60)} min\n"
            f"🧵 Upload worker: {'busy' if UPLOAD_LOCK.locked() else 'idle'}\n"
            f"🎬 Jobs: " + (", ".join(f"{STATUS_LABELS.get(s, s)} {n}" for s, n in jobs) or "none"))
    safe_edit(call, text, back_kb())


@bot.callback_query_handler(func=lambda c: c.data == "help")
def cb_help(call):
    safe_answer(call)
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(Btn(f"{G['play']}  Connect YouTube", callback_data="yt_connect", style="primary"))
    kb.add(Btn(f"{G['upload']}  Upload a video", callback_data="yt_upload_now", style="success"))
    kb.add(Btn(f"{G['tiktok']}  TikTok → YouTube", callback_data="tk_menu", style="success"))
    kb.add(Btn(f"{G['back']}  Back", callback_data="main_menu", style="danger"))
    safe_edit(call, HELP_TEXT, kb)


@bot.callback_query_handler(func=lambda c: c.data == "support")
def cb_support(call):
    safe_answer(call)
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("📞 Contact support",
                                      url=f"https://t.me/{SUPPORT_USR.replace('@', '')}"))
    if UPDATE_CH:
        kb.add(types.InlineKeyboardButton("📣 Updates channel", url=UPDATE_CH))
    kb.add(Btn(f"{G['back']}  Back", callback_data="main_menu", style="danger"))
    safe_edit(call, f"💬 <b>Support</b>\n\nPing {escape(SUPPORT_USR)} and we'll help you out.", kb)


@bot.callback_query_handler(func=lambda c: c.data == "suggest_tags")
def cb_suggest_tags(call):
    """Generate trending tags from the title of the most recent pending job."""
    uid = call.from_user.id
    safe_answer(call)
    jobs = user_jobs(uid, statuses=["pending"], limit=1)
    if not jobs:
        safe_answer(call, "No pending jobs to suggest tags for.", alert=True)
        return
    title = job_field(jobs[0], "title") or "Untitled"
    suggested = suggest_tags(title)
    safe_send(call.message.chat.id,
              "\U0001f3af <b>Suggested tags</b> for:\n<i>{}</i>\n\n"
              "<code>{}</code>\n\n"
              "Copy these tags or tap <b>Edit Tags</b> to use them.".format(
                  escape(title[:80]), escape(suggested)),
              types.InlineKeyboardMarkup(row_width=1)
              .add(Btn(f"{G['settings']}  Apply to Job", callback_data="apply_suggested_tags", style="primary"))
              .add(Btn(f"{G['back']}  Queue", callback_data="queue_menu", style="danger")))


@bot.callback_query_handler(func=lambda c: c.data == "apply_suggested_tags")
def cb_apply_suggested_tags(call):
    """Apply suggested tags to the most recent pending job."""
    uid = call.from_user.id
    safe_answer(call)
    jobs = user_jobs(uid, statuses=["pending"], limit=1)
    if not jobs:
        safe_answer(call, "No pending jobs.", alert=True)
        return
    title = job_field(jobs[0], "title") or "Untitled"
    suggested = suggest_tags(title)
    job_id = int(job_field(jobs[0], "job_id"))
    db_query("UPDATE upload_jobs SET tags = ? WHERE job_id = ?", (suggested, job_id))
    safe_answer(call, "Tags applied!")
    safe_edit(call, job_card_text(get_job(job_id, uid)),
              job_actions_kb(job_id, "pending"))


@bot.callback_query_handler(func=lambda c: c.data == "save_as_default")
def cb_save_as_default(call):
    """Save current job description and tags as user defaults."""
    uid = call.from_user.id
    safe_answer(call)
    jobs = user_jobs(uid, limit=1)
    if not jobs:
        safe_answer(call, "No jobs to save from.", alert=True)
        return
    desc = job_field(jobs[0], "description") or ""
    tags = job_field(jobs[0], "tags") or ""
    save_user_defaults(uid, description=desc, tags=tags)
    safe_answer(call, "Defaults saved!", alert=True)


# ═════════════════════════════════════════════════════════════════
#  ANALYTICS DASHBOARD
# ═════════════════════════════════════════════════════════════════

def show_analytics_dashboard(chat_id: int, uid: int, edit_msg_id: Optional[int] = None) -> None:
    """Fetch and display channel + video analytics."""
    ch_id = default_channel_id(uid)
    if not ch_id or not _YT_OK or not yt_service:
        safe_send(chat_id, "⚠️ Connect a YouTube channel and make sure the API is configured.")
        return

    msg = safe_send(chat_id, "📊 <b>Loading analytics…</b>")
    msg_id = msg.message_id if msg else edit_msg_id

    def worker():
        try:
            credentials = yt_service.credentials_for_channel(uid, ch_id)
            client = build("youtube", "v3", credentials=credentials)

            # ── Channel stats ──
            ch_resp = client.channels().list(
                part="snippet,statistics,contentDetails",
                mine=True).execute()
            if not ch_resp.get("items"):
                safe_send(chat_id, "❌ No channel found.")
                return
            ch = ch_resp["items"][0]
            ch_stats = ch.get("statistics", {})
            ch_name = ch["snippet"]["title"]
            subs = ch_stats.get("subscriberCount", "0")
            total_videos = ch_stats.get("videoCount", "0")
            total_views = ch_stats.get("viewCount", "0")

            # ── Recent videos (last 10) ──
            uploads_id = ch["contentDetails"]["relatedPlaylists"]["uploads"]
            pl_resp = client.playlistItems().list(
                part="snippet",
                playlistId=uploads_id,
                maxResults=10).execute()
            video_ids = []
            video_titles = []
            for item in pl_resp.get("items", []):
                vid = item["snippet"]["resourceId"]["videoId"]
                video_ids.append(vid)
                video_titles.append(item["snippet"]["title"][:50])

            # ── Video-level stats ──
            total_video_views = 0
            total_video_likes = 0
            total_video_comments = 0
            top_video = ("", "", 0)
            video_details = []

            if video_ids:
                stats_resp = client.videos().list(
                    part="statistics,snippet",
                    id=",".join(video_ids)).execute()
                for i, vitem in enumerate(stats_resp.get("items", [])):
                    vs = vitem.get("statistics", {})
                    views = int(vs.get("viewCount", 0))
                    likes = int(vs.get("likeCount", 0))
                    comments = int(vs.get("commentCount", 0))
                    total_video_views += views
                    total_video_likes += likes
                    total_video_comments += comments
                    title = vitem["snippet"]["title"][:40]
                    vid_id = vitem["id"]
                    video_details.append((vid_id, title, views, likes, comments))
                    if views > top_video[2]:
                        top_video = (vid_id, title, views)

            # ── Build the dashboard text ──
            def fmt_num(n):
                n = int(n)
                if n >= 1_000_000:
                    return f"{n/1_000_000:.1f}M"
                if n >= 1_000:
                    return f"{n/1_000:.1f}K"
                return str(n)

            lines = [
                f"📊 <b>Analytics Dashboard</b>",
                f"━━━━━━━━━━━━━━━",
                f"📺 <b>{escape(ch_name)}</b>",
                "",
                f"<b>Channel Overview</b>",
                f"👥 Subscribers: <b>{fmt_num(subs)}</b>",
                f"🎬 Total videos: <b>{total_videos}</b>",
                f"👀 Total views: <b>{fmt_num(total_views)}</b>",
                "",
            ]

            if top_video[0]:
                lines.append(
                    f"🔥 <b>Top video (last 10)</b>\n"
                    f"   {escape(top_video[1])}\n"
                    f"   👀 {fmt_num(top_video[2])} views\n"
                    f"   🔗 https://youtu.be/{top_video[0]}")
                lines.append("")

            lines.append(f"<b>Recent Videos Performance</b>")
            for vid_id, title, views, likes, comments in video_details[:8]:
                lines.append(
                    f"🎬 {escape(title)}\n"
                    f"   👀 {fmt_num(views)} · 👍 {fmt_num(likes)} · 💬 {fmt_num(comments)}")

            if video_details:
                avg_views = total_video_views // max(1, len(video_details))
                avg_likes = total_video_likes // max(1, len(video_details))
                lines += [
                    "",
                    f"<b>Averages (last {len(video_details)})</b>",
                    f"👀 Avg views: {fmt_num(avg_views)} · 👍 Avg likes: {fmt_num(avg_likes)}",
                ]

            lines += [
                "",
                "💡 <i>Tip: Post consistently, use Shorts, and engage with comments!</i>",
            ]

            text = "\n".join(lines)
            kb = types.InlineKeyboardMarkup(row_width=2)
            if top_video[0]:
                kb.add(types.InlineKeyboardButton(
                    "🔥 Watch top video", url=f"https://youtu.be/{top_video[0]}"))
            kb.add(
                Btn(f"{G['refresh']}  Refresh", callback_data="analytics_refresh",
                    style="primary"),
                Btn(f"{G['back']}  Dashboard", callback_data="ch_dash", style="primary"),
            )
            kb.add(Btn(f"{G['back']}  Main Menu", callback_data="main_menu", style="danger"))

            if msg_id:
                try:
                    bot.edit_message_text(text, chat_id, msg_id, reply_markup=kb,
                                          disable_web_page_preview=True)
                except Exception:
                    safe_send(chat_id, text, kb)
            else:
                safe_send(chat_id, text, kb)

        except Exception as exc:
            err = f"❌ Analytics failed: <code>{escape(str(exc)[:300])}</code>"
            if msg_id:
                try:
                    bot.edit_message_text(err, chat_id, msg_id,
                                          reply_markup=back_kb("ch_dash"))
                except Exception:
                    safe_send(chat_id, err)
            else:
                safe_send(chat_id, err)

    threading.Thread(target=worker, daemon=True).start()


# ═════════════════════════════════════════════════════════════════
#  UPLOAD HUB · CHANNEL DASHBOARD · MANAGE VIDEOS
# ═════════════════════════════════════════════════════════════════

@bot.callback_query_handler(func=lambda c: c.data == "force_join_check")
def cb_force_join_check(call):
    """Re-check force-join after user claims to have joined."""
    uid = call.from_user.id
    safe_answer(call, "Checking…")
    if check_force_join(uid, call.message.chat.id):
        safe_answer(call, "✅ All checks passed!")
        safe_edit(call, f"🎬 <b>{BRAND}</b>\n\nChoose an option:", main_menu_kb(uid))


@bot.callback_query_handler(func=lambda c: c.data == "upload_hub")
def cb_upload_hub(call):
    """Upload type selection screen."""
    uid = call.from_user.id
    safe_answer(call)
    if is_banned(uid):
        safe_answer(call, "You are blocked from using this bot.", alert=True)
        return
    if not user_channels(uid):
        safe_edit(call,
                  "⚠️ <b>No YouTube channel connected.</b>\n\n"
                  "Connect one first — it takes 20 seconds.",
                  types.InlineKeyboardMarkup(row_width=1)
                  .add(Btn(f"{G['play']}  Connect YouTube", callback_data="yt_connect",
                           style="success"))
                  .add(Btn(f"{G['back']}  Back", callback_data="main_menu", style="danger")))
        return
    safe_edit(call, upload_hub_text(uid), upload_hub_kb(uid))


@bot.callback_query_handler(func=lambda c: c.data == "upload_short")
def cb_upload_short(call):
    """Upload a YouTube Short — set state for shorts-aware ingest."""
    uid = call.from_user.id
    safe_answer(call)
    if is_banned(uid):
        safe_answer(call, "You are blocked from using this bot.", alert=True)
        return
    if not user_channels(uid):
        safe_edit(call,
                  "⚠️ <b>No YouTube channel connected.</b>\n\n"
                  "Connect one first — it takes 20 seconds.",
                  types.InlineKeyboardMarkup(row_width=1)
                  .add(Btn(f"{G['play']}  Connect YouTube", callback_data="yt_connect",
                           style="success"))
                  .add(Btn(f"{G['back']}  Back", callback_data="main_menu", style="danger")))
        return
    prompt_for_short(call.message.chat.id, uid)


@bot.callback_query_handler(func=lambda c: c.data in ("manage_vids",) or
                            c.data.startswith("manage_vids:"))
def cb_manage_vids(call):
    """Video management list with filtering."""
    uid = call.from_user.id
    safe_answer(call)
    page = 0
    if ":" in call.data:
        try:
            page = max(0, int(call.data.split(":", 1)[1]))
        except (ValueError, IndexError):
            page = 0
    safe_edit(call, manage_videos_text(uid, page), manage_videos_kb(uid, page))


@bot.callback_query_handler(func=lambda c: c.data.startswith("manage_filter:"))
def cb_manage_filter(call):
    """Filter videos by status."""
    uid = call.from_user.id
    status_filter = call.data.split(":", 1)[1]
    safe_answer(call)
    safe_edit(call, manage_videos_text(uid, 0, status_filter),
              manage_videos_kb(uid, 0, status_filter))


@bot.callback_query_handler(func=lambda c: c.data.startswith("vid_view:"))
def cb_vid_view(call):
    """View a single video in the manage view."""
    uid = call.from_user.id
    job_id = int(call.data.split(":", 1)[1])
    job = get_job(job_id, uid)
    if not job:
        safe_answer(call, "Job not found.", alert=True)
        return
    safe_answer(call)
    safe_edit(call, job_card_text(job), job_actions_kb(job_id, job_field(job, "status")))


@bot.callback_query_handler(func=lambda c: c.data.startswith("vid_delete:"))
def cb_vid_delete(call):
    """Delete a video from manage view."""
    uid = call.from_user.id
    job_id = int(call.data.split(":", 1)[1])
    job = get_job(job_id, uid)
    if not job:
        safe_answer(call, "Job not found.", alert=True)
        return
    path = job_field(job, "file_path") or ""
    if path and str(path).startswith(str(STORAGE_DIR)) and Path(path).exists():
        try:
            Path(path).unlink()
        except OSError:
            pass
    db_query('DELETE FROM upload_jobs WHERE job_id = ?', (job_id,))
    audit(uid, "job_deleted", str(job_id))
    safe_answer(call, "Deleted")
    safe_edit(call, manage_videos_text(uid, 0), manage_videos_kb(uid, 0))


@bot.callback_query_handler(func=lambda c: c.data == "ch_set_priv")
def cb_ch_set_privacy(call):
    """Set default privacy from the channel dashboard or upload hub."""
    uid = call.from_user.id
    safe_answer(call)
    safe_edit(call, "🔒 <b>Default Privacy for uploads</b>\n\n"
              "This applies to all future uploads unless you change it per video.",
              privacy_kb("ch_def_priv"))


@bot.callback_query_handler(func=lambda c: c.data.startswith("ch_def_priv:"))
def cb_ch_def_priv_set(call):
    """Save default privacy choice."""
    uid = call.from_user.id
    value = call.data.split(":", 1)[1]
    if value not in PRIVACY_LABELS:
        safe_answer(call, "Invalid choice.", alert=True)
        return
    db_query('UPDATE users SET default_privacy = ? WHERE user_id = ?', (value, uid))
    safe_answer(call, f"Default privacy: {PRIVACY_LABELS[value]}")
    # Route back to the most relevant screen
    ch = default_channel_id(uid)
    if ch:
        safe_edit(call, channel_dashboard_text(uid, ch), channel_dashboard_kb(uid, ch))
    else:
        safe_edit(call, upload_hub_text(uid), upload_hub_kb(uid))


@bot.callback_query_handler(func=lambda c: c.data == "ch_set_tags")
def cb_ch_set_tags(call):
    """Set default tags from the channel dashboard."""
    uid = call.from_user.id
    safe_answer(call)
    set_state(uid, "ch_edit_tags")
    defaults = get_user_defaults(uid)
    current = defaults.get("tags", "")
    safe_edit(call,
              f"📝 <b>Default Tags</b>\n\n"
              f"Current: <code>{escape(current[:200]) or '(none)'}</code>\n\n"
              "Send new tags separated by commas. They will be applied to all future uploads.\n\n"
              "<i>Send /cancel to abort.</i>",
              back_kb("upload_hub"))


@bot.callback_query_handler(func=lambda c: c.data == "ch_dash")
def cb_ch_dash(call):
    """Channel dashboard — show active channel dashboard."""
    uid = call.from_user.id
    safe_answer(call)
    ch = default_channel_id(uid)
    if not ch:
        safe_answer(call, "No channel selected.", alert=True)
        return
    safe_edit(call, channel_dashboard_text(uid, ch), channel_dashboard_kb(uid, ch))


@bot.callback_query_handler(func=lambda c: c.data in ("analytics", "analytics_refresh"))
def cb_analytics(call):
    """Show video analytics dashboard."""
    uid = call.from_user.id
    safe_answer(call, "Loading analytics…")
    if not user_channels(uid):
        safe_answer(call, "Connect a YouTube channel first.", alert=True)
        return
    if not plan_has_feature(uid, "analytics"):
        safe_answer(call, "Upgrade your plan to access Analytics.", alert=True)
        return
    show_analytics_dashboard(call.message.chat.id, uid, edit_msg_id=call.message.message_id)


# ═════════════════════════════════════════════════════════════════
#  FALLBACK HANDLER (must be last)
# ═════════════════════════════════════════════════════════════════

@bot.callback_query_handler(func=lambda c: True)
def cb_unknown(call):
    """Last-resort handler so no button ever spins forever."""
    uid = call.from_user.id
    print(f"[ui] unhandled callback: {call.data!r} from {uid}")
    safe_answer(call, "This button is out of date — reopening the menu.")
    safe_send(call.message.chat.id, f"🎬 <b>{BRAND}</b>\n\nChoose an option:", main_menu_kb(uid))


# ═════════════════════════════════════════════════════════════════
#  MAIN
# ═════════════════════════════════════════════════════════════════

# Update types this bot handles, passed explicitly on EVERY getUpdates call.
#
# `allowed_updates` is sticky server-side: if the parameter is omitted, Telegram
# reuses the previous setting instead of resetting to "everything". Any earlier
# tool that polled this same token with a narrow list (the unused aiogram app in
# `src/` reads the same BOT_TOKEN) therefore keeps filtering updates for us - a
# list without `callback_query` makes every inline button do nothing while /start
# and plain text keep working. Never let this default to None.
POLLED_UPDATES = [
    "message",
    "edited_message",
    "callback_query",
    "channel_post",
    "edited_channel_post",
]


def startup_banner() -> None:
    print(f"🎬 Starting {BRAND}...")
    # First line of every deploy log: if this fingerprint is not the one you just
    # built, you are reading a previous deploy's log.
    print(f"🏷 Build: {build_marker()}")
    print(f"🔑 Token: {mask_secret(TOKEN)} (from {value_source(BOT_TOKEN_ENV_KEY)})")
    if os.environ.get("BOT_TOKEN") and DOTENV_VALUES.get("BOT_TOKEN") \
            and os.environ["BOT_TOKEN"] != DOTENV_VALUES["BOT_TOKEN"]:
        print("[!] The BOT_TOKEN environment variable differs from the one in .env."
              "\n    The environment wins by default - set PREFER_ENV_FILE=1 to use .env instead.")
    print(f"👑 Owner: {OWNER_ID or 'unset'}")
    if not OWNER_ID:
        print("[!] OWNER_ID is not set - nobody will have admin access."
              "\n    Add OWNER_ID=<your numeric id> to .env (get it from @userinfobot).")
    print(f"📺 YouTube API: {'✅ Enabled' if _YT_OK else '❌ Disabled'}")
    print(f"🎵 yt-dlp: {'✅' if _YTDLP_OK else '❌ missing (link downloads disabled)'}")
    print(f"🎞 ffmpeg: {'found' if shutil.which('ffmpeg') else 'missing'}")
    print(f"🍪 Social cookies: {'found' if os.path.isfile(SOCIAL_COOKIES_FILE) else 'missing'}")
    for line in oauth_config_lines():
        print(line)
    # Printed so it is obvious in host logs whether storage survives a redeploy.
    print(f"🗄  Storage dir: {STORAGE_DIR}")
    print(f"    Database:    {DB_FILE} "
          f"({'exists' if DB_FILE.exists() else 'created now'})")
    for line in storage_report_lines():
        print(line)
    try:
        me = bot.get_me()
        print(f"🤖 Signed in as @{me.username} (id {me.id})")
    except Exception as exc:
        print(f"[!] getMe failed: {exc}")
    try:
        hook = bot.get_webhook_info()
        if hook.url:
            print(f"[!] A webhook is set to {hook.url}")
            print("    Polling and a webhook cannot coexist - updates go to the")
            print("    webhook URL and getUpdates fails, so buttons look dead.")
    except Exception:
        pass


if __name__ == "__main__":
    startup_banner()

    # Must happen before polling: a webhook left behind by an earlier deploy or
    # by the unused src/ app blocks getUpdates entirely (409 Conflict). Clearing
    # it is a no-op for a bot that only ever polls.
    try:
        bot.remove_webhook()
        print("[polling] webhook cleared - polling mode")
    except Exception as exc:
        print(f"[!] could not clear webhook: {exc}")

    start_keepalive()
    print(f"🌐 Web server started on port {HTTP_PORT}")
    Thread(target=upload_worker_loop, daemon=True).start()
    Thread(target=tiktok_loop, daemon=True).start()
    Thread(target=channel_health_loop, daemon=True).start()
    Thread(target=nightly_summary_loop, daemon=True).start()
    Thread(target=announce_startup, daemon=True).start()

    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=60,
                                 allowed_updates=POLLED_UPDATES)
        except Exception as exc:
            print(f"[polling] crashed: {exc}")
            traceback.print_exc()
            time.sleep(5)
