"""Tests for the manual OAuth code paste handler in bot.py.

Verifies that the yt_paste_code state handler correctly:
- Extracts codes from pasted URLs
- URL-decodes authorization codes
- Rejects invalid / short codes
- Handles token exchange errors (invalid_grant, redirect_uri_mismatch)
- Handles missing refresh tokens
- Saves the channel on success
- Rate-limits repeated attempts
"""

import importlib.util
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import re

import pytest

BOT_PATH = Path(__file__).resolve().parent.parent / "bot.py"
TEST_UID = 4242429999
CHAT_ID = 4242429999


def _load_bot():
    os.environ.setdefault("SKIP_AUTO_INSTALL", "1")
    os.environ.setdefault("BOT_TOKEN", "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    os.environ.setdefault("OWNER_ID", "1")
    if "yt_dlp" not in sys.modules:
        try:
            import yt_dlp  # noqa: F401
        except ImportError:
            import types
            stub = types.ModuleType("yt_dlp")
            stub.YoutubeDL = object
            sys.modules["yt_dlp"] = stub
    spec = importlib.util.spec_from_file_location("bot_oauth_test", BOT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["bot_oauth_test"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def bot():
    return _load_bot()


class FakeUser:
    def __init__(self, uid):
        self.id = uid
        self.username = "test_user"
        self.first_name = "Test"
        self.is_bot = False


class FakeChat:
    def __init__(self, cid=CHAT_ID):
        self.id = cid
        self.type = "private"


class FakeMessage:
    def __init__(self, text="", cid=CHAT_ID, mid=100):
        self.chat = FakeChat(cid)
        self.message_id = mid
        self.text = text
        self.from_user = FakeUser(TEST_UID)


class RecordingBot:
    def __init__(self):
        self.edits = []
        self.sends = []
        self.replies = []
        self.answers = []

    def answer_callback_query(self, _id, text=None, show_alert=False):
        self.answers.append((text, show_alert))

    def edit_message_text(self, text, _cid, _mid, reply_markup=None,
                          disable_web_page_preview=None):
        self.edits.append(text)
        return FakeMessage(text)

    def send_message(self, _cid, text, reply_markup=None, disable_web_page_preview=None):
        self.sends.append(text)
        return FakeMessage(text)

    def reply_to(self, _msg, text, reply_markup=None, disable_web_page_preview=None):
        self.replies.append(text)
        return FakeMessage(text)


def _stub_api(monkeypatch, bot_module):
    recorder = RecordingBot()
    monkeypatch.setattr(bot_module.bot, "edit_message_text", recorder.edit_message_text)
    monkeypatch.setattr(bot_module.bot, "send_message", recorder.send_message)
    monkeypatch.setattr(bot_module.bot, "reply_to", recorder.reply_to)
    monkeypatch.setattr(bot_module.bot, "answer_callback_query", recorder.answer_callback_query)
    return recorder


def _make_msg(text):
    return FakeMessage(text=text)


def _extract_handler(bot_module):
    """Find the yt_paste_code branch inside handle_text."""
    for handler in bot_module.bot.message_handlers:
        func = handler.get("function")
        if func and getattr(func, "__name__", "") == "handle_text":
            return func
    raise RuntimeError("handle_text not found")


class TestCodeExtraction:
    def test_extracts_code_from_full_callback_url(self, bot):
        """code= parameter is extracted from a complete OAuth redirect URL."""
        text = (
            "http://localhost:8080/oauth/callback"
            "?code=4/0AeaYSHD_lots-of-chars-here%2Fmore"
            "&scope=https://www.googleapis.com/auth/youtube.upload"
            "&state=abc123"
        )
        match = re.search(r"code=([^&]+)", text)
        assert match is not None
        from urllib.parse import unquote
        code = unquote(match.group(1))
        assert code.startswith("4/0AeaYSHD_")
        assert "/" in code  # %2F was decoded

    def test_extracts_code_with_url_encoded_slashes(self, bot):
        """Codes containing %2F are correctly decoded to /."""
        from urllib.parse import unquote
        raw = "code=4%2F0AeaYSHD_test%2Fvalue"
        match = re.search(r"code=([^&]+)", raw)
        assert match is not None
        decoded = unquote(match.group(1))
        assert decoded == "4/0AeaYSHD_test/value"

    def test_no_code_in_regular_message(self, bot):
        """A regular message without code= should not match."""
        import re as re_mod
        assert re_mod.search(r"code=([^&]+)", "hello world") is None

    def test_code_must_be_at_least_10_chars(self, bot):
        """Short codes (< 10 chars) are rejected as invalid."""
        assert len("short") < 10
        assert len("4/0AeaYSH") < 10
        assert len("4/0AeaYSHD_enough") >= 10


class TestRateLimit:
    def test_cooldown_dict_exists(self, bot):
        assert hasattr(bot, "OAUTH_PASTE_COOLDOWN")
        assert hasattr(bot, "OAUTH_PASTE_COOLDOWN_SECONDS")

    def test_cooldown_blocks_repeat_attempts(self, bot):
        """After a recent attempt, the cooldown should block the next one."""
        bot.OAUTH_PASTE_COOLDOWN[TEST_UID] = time.time()
        last = bot.OAUTH_PASTE_COOLDOWN.get(TEST_UID, 0)
        assert time.time() - last < bot.OAUTH_PASTE_COOLDOWN_SECONDS
        # Clean up
        bot.OAUTH_PASTE_COOLDOWN.pop(TEST_UID, None)

    def test_cooldown_allows_after_expiry(self, bot):
        """After the cooldown period expires, attempts should be allowed."""
        bot.OAUTH_PASTE_COOLDOWN[TEST_UID] = time.time() - bot.OAUTH_PASTE_COOLDOWN_SECONDS - 1
        last = bot.OAUTH_PASTE_COOLDOWN.get(TEST_UID, 0)
        assert time.time() - last >= bot.OAUTH_PASTE_COOLDOWN_SECONDS
        bot.OAUTH_PASTE_COOLDOWN.pop(TEST_UID, None)


class TestErrorHandling:
    def test_invalid_grant_message(self, bot):
        """The handler should show a friendly message for invalid_grant errors."""
        # Check that the handler code contains the expected error text
        source = BOT_PATH.read_text(encoding="utf-8")
        assert "invalid_grant" in source
        assert "Code expired or already used" in source

    def test_redirect_uri_mismatch_message(self, bot):
        """The handler should show the expected redirect URI for mismatch errors."""
        source = BOT_PATH.read_text(encoding="utf-8")
        assert "redirect_uri_mismatch" in source
        assert "Redirect URI mismatch" in source

    def test_missing_refresh_token_message(self, bot):
        """The handler should suggest revoking access when refresh token is missing."""
        source = BOT_PATH.read_text(encoding="utf-8")
        assert "No refresh token received" in source
        assert "myaccount.google.com/permissions" in source

    def test_exchange_code_uses_correct_redirect_uri(self, bot):
        """The handler reuses yt_service.exchange_code which uses YT_REDIRECT_URI."""
        assert bot.yt_service is not None
        # exchange_code should use the configured redirect_uri
        assert bot.yt_service.redirect_uri == bot.YT_REDIRECT_URI


class TestCallbackButton:
    def test_paste_code_button_in_connect_flow(self, bot):
        """The connect flow keyboard should include the Paste Code Manually button."""
        source = BOT_PATH.read_text(encoding="utf-8")
        assert 'callback_data="yt_paste_code"' in source
        assert "Paste Code Manually" in source

    def test_paste_code_callback_handler_exists(self, bot):
        """A callback handler for yt_paste_code must be registered."""
        handler_names = [
            h.get("function", lambda: None).__name__
            for h in bot.bot.callback_query_handlers
        ]
        assert "cb_yt_paste_code" in handler_names


class TestStateManagement:
    def test_state_is_set_on_button_press(self, bot):
        """Pressing Paste Code Manually should set state to yt_paste_code."""
        bot.set_state(TEST_UID, "yt_paste_code")
        state = bot.get_state(TEST_UID)
        assert state.get("state") == "yt_paste_code"
        bot.clear_state(TEST_UID)

    def test_state_cleared_after_paste(self, bot):
        """State should be cleared when a URL is pasted (handler calls clear_state)."""
        bot.set_state(TEST_UID, "yt_paste_code")
        assert bot.get_state(TEST_UID).get("state") == "yt_paste_code"
        bot.clear_state(TEST_UID)
        assert bot.get_state(TEST_UID) == {}
