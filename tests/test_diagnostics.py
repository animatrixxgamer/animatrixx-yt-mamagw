"""Diagnostics tests for bot.py: the build marker and the OAuth self-check.

Both exist to end one specific kind of wasted hour — staring at a symptom that
has not changed, with no way to tell whether the running code is even current,
or whether Google is genuinely rejecting the configured client.
"""

import hashlib
import importlib.util
import os
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

USER_A = 111222333
USER_B = 444555666

BOT_PATH = Path(__file__).resolve().parent.parent / "bot.py"


def _load_bot(db_path):
    """Import bot.py the way the other suites do, pointed at a throwaway db."""
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

    spec = importlib.util.spec_from_file_location("bot_diag_test", BOT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["bot_diag_test"] = module
    spec.loader.exec_module(module)

    module.DB_FILE = db_path
    module.init_db()
    return module


@pytest.fixture(scope="module")
def bot(tmp_path_factory):
    return _load_bot(tmp_path_factory.mktemp("diagdb") / "bot_data.db")


class TestBuildMarker:
    def test_fingerprint_is_the_digest_of_the_running_file(self, bot):
        expected = hashlib.sha256(BOT_PATH.read_bytes()).hexdigest()[:12]
        assert bot._code_fingerprint() == expected

    def test_fingerprint_tracks_the_file_contents(self, bot, tmp_path):
        first = tmp_path / "first.py"
        second = tmp_path / "second.py"
        first.write_text("print(1)", encoding="utf-8")
        second.write_text("print(2)", encoding="utf-8")
        assert bot._code_fingerprint(first) != bot._code_fingerprint(second)

    def test_unreadable_file_is_reported_not_raised(self, bot, tmp_path):
        assert bot._code_fingerprint(tmp_path / "missing.py") == "unknown"

    def test_marker_identifies_the_artifact_and_its_age(self, bot):
        marker = bot.build_marker()
        assert marker.startswith(bot._code_fingerprint())
        assert f"{BOT_PATH.stat().st_size}b" in marker
        assert "written" in marker
        assert "up " in marker

    def test_diag_page_shows_the_marker(self, bot):
        html = bot.app_flask.test_client().get("/diag").get_data(as_text=True)
        assert bot._code_fingerprint() in html

    def test_status_page_shows_the_marker(self, bot):
        text = bot.app_flask.test_client().get("/").get_data(as_text=True)
        assert f"build={bot._code_fingerprint()}" in text

    def test_banner_page_and_command_share_one_marker(self, bot):
        """Three places report the build; they must not drift apart."""
        source = BOT_PATH.read_text(encoding="utf-8")
        assert source.count("build_marker()") >= 3
        assert "🏷 Build:" in source


class TestOAuthSelfCheck:
    @pytest.fixture(autouse=True)
    def _configured(self, bot, monkeypatch):
        """A plausible working setup; each test then breaks exactly one thing."""
        if bot.yt_service is None:
            pytest.skip("Google client libraries are not installed")
        monkeypatch.setattr(bot, "_YT_OK", True)
        monkeypatch.setattr(bot, "PUBLIC_BASE_URL", "")
        monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
        monkeypatch.setattr(bot, "YT_CLIENT_ID", "1234.apps.googleusercontent.com")
        monkeypatch.setattr(bot, "YT_CLIENT_SECRET", "GOCSPX-secret")
        monkeypatch.setattr(
            bot, "YT_REDIRECT_URI",
            "https://animatrixx-yt-manager.up.railway.app/oauth/youtube/callback")

    def test_healthy_client_reports_nothing_to_fix(self, bot, monkeypatch):
        # Google validates client auth and the redirect URI before the code, so
        # invalid_grant means everything except the fake code was accepted.
        monkeypatch.setattr(bot.yt_service, "probe_client_config",
                            lambda: bot.OAuthError("invalid_grant", "Malformed auth code"))
        report = bot.oauth_self_check_html(check_reachability=False)
        assert "Google accepted" in report
        assert "Nothing to fix" in report
        assert "❌" not in report

    def test_redirect_uri_mismatch_names_the_exact_screen(self, bot, monkeypatch):
        monkeypatch.setattr(bot.yt_service, "probe_client_config",
                            lambda: bot.OAuthError("redirect_uri_mismatch", "Bad Request"))
        report = bot.oauth_self_check_html(check_reachability=False)
        assert "Authorized redirect URIs" in report
        assert "1 problem(s)" in report

    def test_invalid_client_is_not_blamed_on_the_redirect_uri(self, bot, monkeypatch):
        monkeypatch.setattr(bot.yt_service, "probe_client_config",
                            lambda: bot.OAuthError("invalid_client", "Unauthorized"))
        report = bot.oauth_self_check_html(check_reachability=False)
        assert "Web application" in report
        assert "Authorized redirect URIs" not in report

    def test_unauthorized_client_points_at_the_client_type(self, bot, monkeypatch):
        monkeypatch.setattr(bot.yt_service, "probe_client_config",
                            lambda: bot.OAuthError("unauthorized_client", "nope"))
        assert "Web application" in bot.oauth_self_check_html(check_reachability=False)

    def test_localhost_redirect_on_a_host_is_an_error(self, bot, monkeypatch):
        monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
        monkeypatch.setattr(bot, "YT_REDIRECT_URI", "http://localhost:8080/oauth/callback")
        report = bot.oauth_self_check_html(check_reachability=False)
        assert "back into its own container" in report

    def test_missing_credentials_skip_the_live_probe(self, bot, monkeypatch):
        monkeypatch.setattr(bot, "YT_CLIENT_SECRET", "")
        called = []
        monkeypatch.setattr(bot.yt_service, "probe_client_config",
                            lambda: called.append(True))
        report = bot.oauth_self_check_html(check_reachability=False)
        assert "YOUTUBE_CLIENT_SECRET" in report
        assert "not set on this host" in report
        assert not called

    def test_the_web_page_runs_the_same_check(self, bot, monkeypatch):
        monkeypatch.setattr(bot.yt_service, "probe_client_config",
                            lambda: bot.OAuthError("redirect_uri_mismatch", "Bad Request"))
        html = bot.app_flask.test_client().get("/oauth-check").get_data(as_text=True)
        assert "self-check" in html.lower()
        assert "Authorized redirect URIs" in html

    def test_diag_page_links_to_the_self_check(self, bot):
        html = bot.app_flask.test_client().get("/diag").get_data(as_text=True)
        assert "/oauth-check" in html

    def test_the_button_is_wired_to_a_handler(self, bot):
        """The suite's button-coverage test scans for this; keep it honest."""
        source = BOT_PATH.read_text(encoding="utf-8")
        assert 'callback_data="oauth_selfcheck"' in source
        assert 'c.data == "oauth_selfcheck"' in source


class TestCallbackReachability:
    """A browser redirect that cannot land must not be offered as the main path."""

    @pytest.fixture(autouse=True)
    def _fresh_cache(self, bot):
        bot._CALLBACK_PROBE.clear()

    def _alive(self, bot, monkeypatch, status=200, text="alive"):
        resp = MagicMock(status_code=status, text=text)
        monkeypatch.setattr(bot.req_lib, "get", lambda *a, **k: resp)

    def test_localhost_redirect_on_a_host_is_unreachable(self, bot, monkeypatch):
        monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
        monkeypatch.setattr(bot, "YT_REDIRECT_URI", "http://localhost:8080/oauth/callback")
        ok, why = bot._probe_callback()
        assert ok is False
        assert "container" in why

    def test_public_url_that_answers_is_reachable(self, bot, monkeypatch):
        monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
        monkeypatch.setattr(bot, "YT_REDIRECT_URI", "https://bot.example/oauth/youtube/callback")
        monkeypatch.setattr(bot, "PUBLIC_BASE_URL", "https://bot.example")
        self._alive(bot, monkeypatch)
        assert bot._probe_callback() == (True, "")

    def test_public_url_that_does_not_answer_is_unreachable(self, bot, monkeypatch):
        monkeypatch.setattr(bot, "YT_REDIRECT_URI", "https://bot.example/oauth/youtube/callback")
        monkeypatch.setattr(bot, "PUBLIC_BASE_URL", "https://bot.example")

        def boom(*_args, **_kwargs):
            raise bot.req_lib.ConnectionError("no route to host")

        monkeypatch.setattr(bot.req_lib, "get", boom)
        ok, why = bot._probe_callback()
        assert ok is False
        assert "did not answer" in why

    def test_something_else_answering_is_not_mistaken_for_the_bot(self, bot, monkeypatch):
        monkeypatch.setattr(bot, "YT_REDIRECT_URI", "https://bot.example/oauth/youtube/callback")
        monkeypatch.setattr(bot, "PUBLIC_BASE_URL", "https://bot.example")
        self._alive(bot, monkeypatch, status=404, text="Not Found")
        ok, why = bot._probe_callback()
        assert ok is False
        assert "404" in why

    def test_a_missing_redirect_uri_is_reported(self, bot, monkeypatch):
        monkeypatch.setattr(bot, "YT_REDIRECT_URI", "")
        monkeypatch.setattr(bot, "PUBLIC_BASE_URL", "")
        ok, why = bot._probe_callback()
        assert ok is False
        assert "nowhere" in why

    def test_the_probe_is_cached_so_button_presses_stay_fast(self, bot, monkeypatch):
        monkeypatch.setattr(bot, "YT_REDIRECT_URI", "https://bot.example/oauth/youtube/callback")
        monkeypatch.setattr(bot, "PUBLIC_BASE_URL", "https://bot.example")
        calls = []
        resp = MagicMock(status_code=200, text="alive")
        monkeypatch.setattr(bot.req_lib, "get",
                            lambda *a, **k: (calls.append(1), resp)[1])
        bot.oauth_callback_reachable()
        bot.oauth_callback_reachable()
        assert len(calls) == 1
        bot.oauth_callback_reachable(force=True)
        assert len(calls) == 2

    def test_connect_flow_leads_with_paste_when_the_redirect_is_dead(self, bot, monkeypatch):
        monkeypatch.setattr(bot, "_YT_OK", True)
        monkeypatch.setattr(bot, "YT_CLIENT_ID", "id.apps.googleusercontent.com")
        monkeypatch.setattr(bot, "YT_CLIENT_SECRET", "secret")
        monkeypatch.setattr(bot, "oauth_callback_reachable", lambda *a, **k: (False, "no route"))
        monkeypatch.setattr(bot.yt_service, "get_auth_url",
                            lambda state: "https://accounts.google.com/o/oauth2/auth")
        sent = []
        monkeypatch.setattr(bot, "safe_send",
                            lambda chat_id, text, kb=None: sent.append((text, kb)))

        bot.start_connect_flow(1, 4242)

        assert len(sent) == 1
        text, kb = sent[0]
        assert "Paste Code Manually" in text
        assert "no route" in text
        labels = [btn.get("text", "") if isinstance(btn, dict) else getattr(btn, "text", "")
                  for row in kb.keyboard for btn in row]
        # The working flow comes first; the browser one stays but is demoted.
        assert "Paste Code Manually" in labels[0]
        assert any("Try the browser flow anyway" in label for label in labels)


class TestStartupReport:
    @pytest.fixture(autouse=True)
    def _libs(self, bot, monkeypatch):
        if bot.yt_service is None:
            pytest.skip("Google client libraries are not installed")
        monkeypatch.setattr(bot, "_YT_OK", True)
        monkeypatch.setattr(bot, "YT_CLIENT_ID", "id.apps.googleusercontent.com")
        monkeypatch.setattr(bot, "YT_CLIENT_SECRET", "secret")

    def test_report_states_the_build_and_every_verdict(self, bot, monkeypatch):
        monkeypatch.setattr(bot, "oauth_callback_reachable", lambda *a, **k: (True, ""))
        monkeypatch.setattr(bot, "storage_volume_name", lambda *a, **k: "bot-data")
        monkeypatch.setattr(bot.yt_service, "probe_client_config", lambda: None)
        report = bot.startup_report_html()
        assert bot._code_fingerprint() in report
        assert "persistent" in report
        assert "reaches this bot" in report
        assert "Google accepted" in report

    def test_report_calls_out_ephemeral_storage(self, bot, monkeypatch):
        monkeypatch.setattr(bot, "storage_volume_name", lambda *a, **k: "")
        monkeypatch.setattr(bot, "oauth_callback_reachable", lambda *a, **k: (True, ""))
        monkeypatch.setattr(bot.yt_service, "probe_client_config", lambda: None)
        assert "not persistent" in bot.startup_report_html()

    def test_report_calls_out_a_rejected_oauth_client(self, bot, monkeypatch):
        monkeypatch.setattr(bot, "oauth_callback_reachable", lambda *a, **k: (False, "no route"))
        monkeypatch.setattr(bot.yt_service, "probe_client_config",
                            lambda: bot.OAuthError("redirect_uri_mismatch", "Bad Request"))
        report = bot.startup_report_html()
        assert "cannot work" in report
        assert "Paste Code Manually" in report
        assert "redirect_uri_mismatch" in report
        assert "Authorized redirect URIs" in report

    def test_report_mentions_missing_credentials(self, bot, monkeypatch):
        monkeypatch.setattr(bot, "YT_CLIENT_SECRET", "")
        monkeypatch.setattr(bot, "oauth_callback_reachable", lambda *a, **k: (True, ""))
        assert "YOUTUBE_CLIENT_SECRET" in bot.startup_report_html()

    def test_announce_startup_is_silent_without_an_owner(self, bot, monkeypatch):
        monkeypatch.setattr(bot, "OWNER_ID", 0)
        sent = []
        monkeypatch.setattr(bot, "notify", lambda *a, **k: sent.append(1))
        bot.announce_startup()
        assert not sent

    def test_announce_startup_sends_the_report_to_the_owner(self, bot, monkeypatch):
        monkeypatch.setattr(bot, "OWNER_ID", 999)
        monkeypatch.setattr(bot, "startup_report_html", lambda *a, **k: "verdict")
        monkeypatch.setattr(bot.time, "sleep", lambda *a: None)
        sent = []
        monkeypatch.setattr(bot, "notify",
                            lambda uid, text, kb=None: sent.append((uid, text)))
        bot.announce_startup()
        assert sent == [(999, "verdict")]


class TestHealthSummary:
    """The nightly message has to be scoped, accurate, and not fire every boot."""

    @pytest.fixture(autouse=True)
    def _clean(self, bot):
        bot.db_query("DELETE FROM youtube_channels")
        bot.db_query("DELETE FROM upload_jobs")

    def _channel(self, bot, uid, cid, status="connected"):
        bot.db_query("INSERT INTO youtube_channels "
                     "(user_id, channel_id, channel_name, status) VALUES (?, ?, ?, ?)",
                     (uid, cid, cid, status))

    def _job(self, bot, uid, status, error=None, count_as_today=True):
        bot.db_query(
            'INSERT INTO upload_jobs (user_id, channel_id, title, status, error_msg, '
            'completed_at) VALUES (?, ?, ?, ?, ?, ?)',
            (uid, "UC_ok", "t", status, error,
             datetime.utcnow().isoformat() if count_as_today else None))

    def test_counts_channels_queue_and_failures(self, bot):
        self._channel(bot, USER_A, "UC_ok")
        self._channel(bot, USER_A, "UC_dead", status="revoked")
        self._job(bot, USER_A, "completed")
        self._job(bot, USER_A, "pending", count_as_today=False)
        self._job(bot, USER_A, "scheduled", count_as_today=False)
        self._job(bot, USER_A, "failed", error="Quota exceeded", count_as_today=False)

        text = bot.health_summary_html(USER_A)
        assert "1 connected" in text
        assert "1 need reconnecting" in text
        assert "Uploads (24h): 1" in text
        assert "1,600 of 10,000 API units" in text
        assert "1 ready" in text
        assert "1 scheduled" in text
        assert "Failed: 1" in text

    def test_the_owner_sees_every_user(self, bot):
        self._job(bot, USER_A, "pending", count_as_today=False)
        self._job(bot, USER_B, "pending", count_as_today=False)
        assert "2 ready" in bot.health_summary_html(0)

    def test_a_regular_user_sees_only_their_own_work(self, bot):
        self._job(bot, USER_A, "pending", count_as_today=False)
        self._job(bot, USER_B, "pending", count_as_today=False)
        assert "1 ready" in bot.health_summary_html(USER_A)

    def test_an_idle_bot_reads_as_healthy(self, bot):
        text = bot.health_summary_html(USER_A)
        assert "none connected" in text
        assert "Failed: 0 ✅" in text

    def test_the_summary_carries_the_build_fingerprint(self, bot):
        assert bot._code_fingerprint() in bot.health_summary_html(USER_A)

    def test_the_command_reports_the_whole_bot_to_the_owner_only(self, bot):
        source = BOT_PATH.read_text(encoding="utf-8")
        assert "commands=['summary', 'health']" in source
        assert "health_summary_html(0 if is_admin(uid) else uid)" in source

    def test_the_loop_can_be_turned_off(self, bot, monkeypatch):
        monkeypatch.setenv("HEALTH_SUMMARY_SECONDS", "0")
        slept = []
        monkeypatch.setattr(bot.time, "sleep", lambda seconds: slept.append(seconds))
        bot.nightly_summary_loop()
        assert not slept  # returns immediately instead of sleeping forever

    def test_the_nightly_send_is_due_once_and_recorded(self, bot, monkeypatch):
        monkeypatch.delenv("HEALTH_SUMMARY_SECONDS", raising=False)
        monkeypatch.setattr(bot, "OWNER_ID", 999)
        monkeypatch.setattr(bot, "get_setting", lambda key, default="": "")
        sent, saved = [], []
        monkeypatch.setattr(bot, "notify",
                            lambda uid, text, kb=None: sent.append((uid, text)))
        monkeypatch.setattr(bot, "set_setting",
                            lambda key, value: saved.append((key, value)))

        class StopLoop(Exception):
            pass

        sleeps = []

        def sleeper(seconds):
            sleeps.append(seconds)
            if len(sleeps) >= 2:
                raise StopLoop()

        monkeypatch.setattr(bot.time, "sleep", sleeper)
        with pytest.raises(StopLoop):
            bot.nightly_summary_loop()

        assert sent and sent[0][0] == 999
        # Recorded in the database so a redeploy does not reset the timer.
        assert saved and saved[0][0] == "last_summary_at"

    def test_a_recent_summary_is_not_resent(self, bot, monkeypatch):
        monkeypatch.delenv("HEALTH_SUMMARY_SECONDS", raising=False)
        monkeypatch.setattr(bot, "OWNER_ID", 999)
        monkeypatch.setattr(bot, "get_setting",
                            lambda key, default="": datetime.utcnow().isoformat())
        sent = []
        monkeypatch.setattr(bot, "notify",
                            lambda uid, text, kb=None: sent.append(uid))

        class StopLoop(Exception):
            pass

        monkeypatch.setattr(bot.time, "sleep",
                            lambda seconds: (_ for _ in ()).throw(StopLoop()))
        with pytest.raises(StopLoop):
            bot.nightly_summary_loop()
        assert not sent
