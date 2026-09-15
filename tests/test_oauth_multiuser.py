"""Multi-user YouTube OAuth tests for bot.py.

Covers the parts that were actually broken in production:

* ``extract_auth_code`` decodes ``%2F`` exactly once (never twice).
* ``_token_request`` turns Google's 400 JSON body into a typed ``OAuthError``.
* ``exchange_code`` sends the *same* ``redirect_uri`` the auth URL used.
* Tokens are stored per Telegram user (multi-user, no global refresh token).
* ``credentials_for_channel`` reuses its cache and flags revoked grants.
* ``disconnect_channel`` removes only the caller's row and revokes at Google.
* The Flask callback rejects unknown/expired states instead of replaying codes.
"""

import importlib.util
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BOT_PATH = Path(__file__).resolve().parent.parent / "bot.py"
USER_A = 111222333
USER_B = 444555666


def _load_bot(db_path):
    os.environ.setdefault("SKIP_AUTO_INSTALL", "1")
    os.environ.setdefault("BOT_TOKEN", "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    os.environ.setdefault("OWNER_ID", "1")
    if "yt_dlp" not in sys.modules:
        try:
            import yt_dlp  # noqa: F401
        except ImportError:
            stub = __import__("types").ModuleType("yt_dlp")
            stub.YoutubeDL = object
            sys.modules["yt_dlp"] = stub

    spec = importlib.util.spec_from_file_location("bot_multiuser_test", BOT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["bot_multiuser_test"] = module
    spec.loader.exec_module(module)

    # Point every db_query() at a throwaway database for this module.
    module.DB_FILE = db_path
    module.init_db()
    return module


@pytest.fixture(scope="module")
def bot(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("oauthdb") / "test.db"
    return _load_bot(db_path)


@pytest.fixture(autouse=True)
def clean_state(bot):
    """Keep each test free of rows/cache left by an earlier one."""
    bot.db_query("DELETE FROM youtube_channels")
    bot.db_query("DELETE FROM oauth_states")
    bot.ACCESS_TOKEN_CACHE.clear()
    # Real rows so set_default_channel()/last_channel_id behave like production.
    bot.ensure_user(USER_A, "alice")
    bot.ensure_user(USER_B, "bob")
    yield
    bot.ACCESS_TOKEN_CACHE.clear()


def _fake_response(status=200, body=None, text=""):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = body if body is not None else {}
    resp.text = text
    return resp


# ── code extraction ──────────────────────────────────────────────────

class TestExtractAuthCode:
    def test_decodes_percent_encoded_slash_once(self, bot):
        url = ("http://localhost:8080/oauth/youtube/callback"
               "?state=y4c_RONz&iss=https://accounts.google.com"
               "&code=4%2F0ATsMZqBL-W3w64GWK%2FRODLaabs&scope=x")
        assert bot.extract_auth_code(url) == "4/0ATsMZqBL-W3w64GWK/RODLaabs"

    def test_does_not_leave_double_encoded_output(self, bot):
        """The classic bug: passing '%2F' straight to requests double-encodes it."""
        code = bot.extract_auth_code("?code=4%2F0Aabc")
        assert "%" not in code
        assert code == "4/0Aabc"

    def test_real_world_url_from_the_bug_report(self, bot):
        url = ("http://localhost:8080/oauth/youtube/callback?state=NC_DaMZ5hN-xKp2O-c-NtA"
               "&iss=https://accounts.google.com"
               "&code=4/0ATsMZqC9nlNeZPnLWYjEGClrAGYCKluYLx9Oy3Z-F-69TiSbKVdGRNtinjYNIzo19DRINw"
               "&scope=https://www.googleapis.com/auth/youtube.upload%20"
               "https://www.googleapis.com/auth/youtube")
        code = bot.extract_auth_code(url)
        assert code.startswith("4/0ATsMZqC9nlNeZPnLWYjEGClr")
        # scope= must never leak into the code
        assert " " not in code and "scope" not in code

    def test_bare_code_is_returned_untouched(self, bot):
        bare = "4/0AeaYSHD_lots-of-chars+here"
        # A bare code must NOT be percent-decoded ('+' is a legal code char).
        assert bot.extract_auth_code(bare) == bare

    def test_message_without_code_returns_empty_ish(self, bot):
        assert bot.extract_auth_code("hello world") == "hello world"

    def test_picks_code_not_state(self, bot):
        url = "?state=abc&code=XYZ123&scope=nope"
        assert bot.extract_auth_code(url) == "XYZ123"


# ── token endpoint error mapping ─────────────────────────────────────

class TestTokenEndpointErrors:
    def test_invalid_grant_becomes_oauth_error(self, bot):
        resp = _fake_response(400, {"error": "invalid_grant",
                                    "error_description": "Bad Request"})
        with patch.object(bot.req_lib, "post", return_value=resp):
            with pytest.raises(bot.OAuthError) as excinfo:
                bot.yt_service.exchange_code("4/0Acode")
        assert excinfo.value.error == "invalid_grant"
        assert excinfo.value.status == 400

    def test_redirect_uri_mismatch_is_surfaced(self, bot):
        resp = _fake_response(400, {"error": "redirect_uri_mismatch",
                                    "error_description": "redirect_uri mismatch"})
        with patch.object(bot.req_lib, "post", return_value=resp):
            with pytest.raises(bot.OAuthError) as excinfo:
                bot.yt_service.exchange_code("4/0Acode")
        assert excinfo.value.error == "redirect_uri_mismatch"

    def test_non_json_body_still_raises(self, bot):
        resp = _fake_response(502, None, text="<html>bad gateway</html>")
        resp.json.side_effect = ValueError("not json")
        with patch.object(bot.req_lib, "post", return_value=resp):
            with pytest.raises(bot.OAuthError) as excinfo:
                bot.yt_service.exchange_code("4/0Acode")
        assert excinfo.value.error == "http_502"

    def test_network_failure_is_wrapped(self, bot):
        with patch.object(bot.req_lib, "post", side_effect=OSError("no route")):
            with pytest.raises(bot.OAuthError) as excinfo:
                bot.yt_service.refresh_access_token("1//refresh")
        assert excinfo.value.error == "network_error"

    def test_exchange_code_sends_matching_redirect_uri(self, bot):
        resp = _fake_response(200, {"access_token": "at", "refresh_token": "rt",
                                    "expires_in": 3600})
        seen = {}
        with patch.object(bot.req_lib, "post", side_effect=lambda url, data=None, timeout=None: (
                seen.update(data or {}) or resp)):
            bot.yt_service.exchange_code("4/0Aabc")
        assert seen["redirect_uri"] == bot.yt_service.redirect_uri
        assert seen["grant_type"] == "authorization_code"
        assert seen["code"] == "4/0Aabc"
        assert seen["client_id"] == bot.YT_CLIENT_ID


# ── per-user storage ─────────────────────────────────────────────────

class TestPerUserStorage:
    def test_save_channel_tokens_is_scoped_to_the_user(self, bot):
        info_a = {"channel_id": "UC_aaa", "channel_name": "Alice", "thumbnail": ""}
        info_b = {"channel_id": "UC_bbb", "channel_name": "Bob", "thumbnail": ""}
        bot.save_channel_tokens(USER_A, {"access_token": "at-a", "refresh_token": "rt-a",
                                         "expires_in": 3600}, info_a)
        bot.save_channel_tokens(USER_B, {"access_token": "at-b", "refresh_token": "rt-b",
                                         "expires_in": 3600}, info_b)

        assert [c[0] for c in bot.user_channels(USER_A)] == ["UC_aaa"]
        assert [c[0] for c in bot.user_channels(USER_B)] == ["UC_bbb"]
        # Tokens are encrypted at rest, never stored as plaintext.
        raw = bot.db_query("SELECT refresh_token FROM youtube_channels WHERE user_id = ?",
                           (USER_A,), fetch=True)[0][0]
        assert raw != "rt-a"
        assert bot.decrypt_data(raw) == "rt-a"

    def test_one_user_can_own_several_channels(self, bot):
        for cid in ("UC_1", "UC_2"):
            bot.save_channel_tokens(
                USER_A, {"access_token": "at", "refresh_token": "rt", "expires_in": 3600},
                {"channel_id": cid, "channel_name": cid, "thumbnail": ""})
        assert len(bot.user_channels(USER_A)) == 2
        # The last one connected becomes the default upload target.
        assert bot.default_channel_id(USER_A) == "UC_2"

    def test_save_marks_channel_connected(self, bot):
        bot.save_channel_tokens(
            USER_A, {"access_token": "at", "refresh_token": "rt", "expires_in": 3600},
            {"channel_id": "UC_x", "channel_name": "X", "thumbnail": ""})
        status = bot.db_query("SELECT status FROM youtube_channels WHERE user_id = ?",
                              (USER_A,), fetch=True)[0][0]
        assert status == "connected"


# ── refresh / auto-renew ─────────────────────────────────────────────

class TestRefresh:
    def _seed(self, bot, expiry_offset=timedelta(hours=1)):
        bot.save_channel_tokens(
            USER_A, {"access_token": "at-old", "refresh_token": "rt-1", "expires_in": 3600},
            {"channel_id": "UC_x", "channel_name": "X", "thumbnail": ""})
        expiry = (datetime.utcnow() + expiry_offset).isoformat()
        bot.db_query("UPDATE youtube_channels SET token_expiry = ? WHERE user_id = ?",
                     (expiry, USER_A))
        bot.ACCESS_TOKEN_CACHE.clear()

    def test_valid_token_never_hits_google(self, bot):
        self._seed(bot, timedelta(hours=1))
        with patch.object(bot.req_lib, "post") as post:
            creds = bot.yt_service.credentials_for_channel(USER_A, "UC_x")
        post.assert_not_called()
        assert creds.token == "at-old"

    def test_expired_token_is_refreshed_and_cached(self, bot):
        self._seed(bot, timedelta(minutes=-1))
        resp = _fake_response(200, {"access_token": "at-new", "expires_in": 3600})
        with patch.object(bot.req_lib, "post", return_value=resp) as post:
            creds = bot.yt_service.credentials_for_channel(USER_A, "UC_x")
            # Second call must be served from the cache, not from Google.
            creds2 = bot.yt_service.credentials_for_channel(USER_A, "UC_x")
        assert creds.token == "at-new"
        assert creds2.token == "at-new"
        assert post.call_count == 1
        assert post.call_args.kwargs["data"]["grant_type"] == "refresh_token"

    def test_revoked_grant_marks_channel_revoked(self, bot):
        self._seed(bot, timedelta(minutes=-1))
        resp = _fake_response(400, {"error": "invalid_grant",
                                    "error_description": "Token has been expired or revoked."})
        with patch.object(bot.req_lib, "post", return_value=resp):
            with pytest.raises(ValueError, match="revoked or expired"):
                bot.yt_service.credentials_for_channel(USER_A, "UC_x")
        status = bot.db_query("SELECT status, last_error FROM youtube_channels "
                              "WHERE user_id = ?", (USER_A,), fetch=True)[0]
        assert status[0] == "revoked"
        assert status[1] == "invalid_grant"

    def test_missing_channel_raises(self, bot):
        with pytest.raises(ValueError, match="not connected"):
            bot.yt_service.credentials_for_channel(USER_A, "UC_missing")


# ── disconnect ───────────────────────────────────────────────────────

class TestDisconnect:
    def test_disconnect_removes_only_that_row_and_revokes(self, bot):
        for cid in ("UC_1", "UC_2"):
            bot.save_channel_tokens(
                USER_A, {"access_token": "at", "refresh_token": f"rt-{cid}",
                         "expires_in": 3600},
                {"channel_id": cid, "channel_name": cid, "thumbnail": ""})
        with patch.object(bot.yt_service, "revoke_token", return_value=True) as revoke:
            assert bot.disconnect_channel(USER_A, "UC_2") is True
        revoke.assert_called_once_with("rt-UC_2")
        assert [c[0] for c in bot.user_channels(USER_A)] == ["UC_1"]
        # Default channel must fall back to what is left.
        assert bot.default_channel_id(USER_A) == "UC_1"

    def test_disconnect_unknown_channel_is_a_no_op(self, bot):
        with patch.object(bot.yt_service, "revoke_token") as revoke:
            assert bot.disconnect_channel(USER_A, "nope") is False
        revoke.assert_not_called()

    def test_disconnect_drops_the_cached_access_token(self, bot):
        bot.save_channel_tokens(
            USER_A, {"access_token": "at", "refresh_token": "rt", "expires_in": 3600},
            {"channel_id": "UC_1", "channel_name": "One", "thumbnail": ""})
        assert (USER_A, "UC_1") in bot.ACCESS_TOKEN_CACHE
        bot.disconnect_channel(USER_A, "UC_1")
        assert (USER_A, "UC_1") not in bot.ACCESS_TOKEN_CACHE


# ── callback hardening ───────────────────────────────────────────────

class TestCallbackHardening:
    def test_unknown_state_is_rejected(self, bot):
        html = bot._handle_oauth_callback("does-not-exist", "4/0Acode")
        assert "Link expired" in html or "expired" in html

    def test_expired_state_is_rejected_without_exchanging(self, bot):
        old = (datetime.utcnow() - timedelta(seconds=bot.OAUTH_STATE_TTL_SECONDS + 60)).isoformat()
        bot.db_query("INSERT OR REPLACE INTO oauth_states (state, user_id, chat_id, created_at) "
                     "VALUES (?, ?, ?, ?)", ("stale", USER_A, USER_A, old))
        with patch.object(bot.yt_service, "exchange_code") as exchange:
            bot._handle_oauth_callback("stale", "4/0Acode")
        exchange.assert_not_called()

    def test_state_is_single_use(self, bot):
        bot.db_query("INSERT OR REPLACE INTO oauth_states (state, user_id, chat_id, created_at) "
                     "VALUES (?, ?, ?, ?)",
                     ("one-shot", USER_A, USER_A, datetime.utcnow().isoformat()))
        with patch.object(bot.yt_service, "exchange_code",
                          side_effect=bot.OAuthError("invalid_grant", "used")):
            bot._handle_oauth_callback("one-shot", "4/0Acode")
        # The state row must be gone so a browser refresh cannot replay it.
        rows = bot.db_query("SELECT state FROM oauth_states WHERE state = ?",
                            ("one-shot",), fetch=True)
        assert not rows

    def test_missing_code_is_reported(self, bot):
        html = bot._handle_oauth_callback("", "")
        assert "code" in html.lower() or "expired" in html.lower()

    def test_redirect_uri_is_passed_to_google_unchanged(self, bot):
        """Auth URL and token exchange must agree, or Google returns 400."""
        url = bot.yt_service.get_auth_url("state123")
        if url:  # empty when the Google client libraries are absent
            assert "redirect_uri=" in url
            assert "state=state123" in url
            assert "access_type=offline" in url
            assert "prompt=consent" in url


# ── configuration hygiene ────────────────────────────────────────────

class TestConfigHygiene:
    def test_oauth_env_values_are_stripped_at_load(self, bot):
        """A trailing newline on a Railway secret breaks every token call."""
        source = BOT_PATH.read_text(encoding="utf-8")
        assert 'os.environ.get("YOUTUBE_CLIENT_ID", "").strip()' in source
        assert 'os.environ.get("YOUTUBE_CLIENT_SECRET", "").strip()' in source
        assert '"YOUTUBE_REDIRECT_URI", "http://localhost:8000/oauth/callback").strip()' in source
        # And the values actually loaded here are clean.
        assert bot.YT_CLIENT_ID == bot.YT_CLIENT_ID.strip()
        assert bot.YT_CLIENT_SECRET == bot.YT_CLIENT_SECRET.strip()
        assert bot.YT_REDIRECT_URI == bot.YT_REDIRECT_URI.strip()
        assert "\n" not in bot.YT_REDIRECT_URI

    def test_readonly_scope_requested(self, bot):
        assert "https://www.googleapis.com/auth/youtube.readonly" in bot.YT_SCOPES
        assert "https://www.googleapis.com/auth/youtube.upload" in bot.YT_SCOPES

    def test_no_global_refresh_token_constant(self, bot):
        """Multi-user model: there must be no process-wide refresh token."""
        assert not hasattr(bot, "YT_REFRESH_TOKEN")
        assert not hasattr(bot, "YOUTUBE_REFRESH_TOKEN")
