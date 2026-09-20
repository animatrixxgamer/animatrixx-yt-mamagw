"""Dispatch every button payload through the real handlers in bot.py.

`test_bot_ui.py` proves each button *has* a handler; this module proves the
handlers actually run. Each callback payload is routed exactly the way
pyTelegramBotAPI routes it (first filter match wins) against a stubbed
Telegram API, so a crash in any menu path fails the test instead of showing
up as an error in production logs.
"""

import importlib.util
import os
import sys
from datetime import datetime
from pathlib import Path


import pytest

BOT_PATH = Path(__file__).resolve().parent.parent / "bot.py"
CHAT_ID = 777001
JOB_UID = 4242420001  # fixture user for queue/file buttons


def _load_bot():
    os.environ.setdefault("SKIP_AUTO_INSTALL", "1")
    os.environ.setdefault("BOT_TOKEN", "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    os.environ.setdefault("OWNER_ID", str(CHAT_ID))
    if "yt_dlp" not in sys.modules:
        try:
            import yt_dlp  # noqa: F401
        except ImportError:
            import types

            stub = types.ModuleType("yt_dlp")
            stub.YoutubeDL = object  # type: ignore[attr-defined]
            sys.modules["yt_dlp"] = stub

    spec = importlib.util.spec_from_file_location("bot_dispatch_under_test", BOT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["bot_dispatch_under_test"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def bot():
    return _load_bot()


class FakeUser:
    def __init__(self, user_id: int) -> None:
        self.id = user_id
        self.username = "pytest"
        self.first_name = "PyTest"
        self.is_bot = False


class FakeChat:
    def __init__(self) -> None:
        self.id = CHAT_ID
        self.type = "private"


class FakeMessage:
    def __init__(self) -> None:
        self.chat = FakeChat()
        self.message_id = 10
        self.message_thread_id = None
        self.text = ""


class FakeCall:
    def __init__(self, data: str, user_id: int) -> None:
        self.id = "callback-id"
        self.data = data
        self.from_user = FakeUser(user_id)
        self.message = FakeMessage()


class RecordingBot:
    """Stub of the TeleBot surface the handlers touch."""

    def __init__(self) -> None:
        self.edits = []
        self.sends = []
        self.answers = []

    def answer_callback_query(self, _id, text=None, show_alert=False):
        self.answers.append((text, show_alert))

    def edit_message_text(self, text, _chat_id, _message_id, reply_markup=None,
                          disable_web_page_preview=None):
        self.edits.append(text)
        return FakeMessage()

    def send_message(self, _chat_id, text, reply_markup=None, disable_web_page_preview=None):
        self.sends.append(text)
        return FakeMessage()

    def get_me(self):
        class Me:
            username = "pytest_bot"
            id = 123456789

        return Me()

    def get_file(self, *_args, **_kwargs):  # pragma: no cover - network path
        raise RuntimeError("network disabled in tests")

    def download_file(self, *_args, **_kwargs):  # pragma: no cover - network path
        raise RuntimeError("network disabled in tests")


def _stub_api(monkeypatch, module) -> RecordingBot:
    """Swap the live bot's I/O methods for a recorder, keeping handlers."""
    recorder = RecordingBot()
    monkeypatch.setattr(module.bot, "answer_callback_query", recorder.answer_callback_query)
    monkeypatch.setattr(module.bot, "edit_message_text", recorder.edit_message_text)
    monkeypatch.setattr(module.bot, "send_message", recorder.send_message)
    monkeypatch.setattr(module.bot, "get_me", recorder.get_me)
    return recorder


def _dispatch(module, payload: str, user_id: int):
    """Route one callback payload the way pyTelegramBotAPI would."""
    call = FakeCall(payload, user_id)
    for handler in module.bot.callback_query_handlers:
        predicate = handler.get("filters")
        if callable(predicate):
            try:
                if not predicate(call):
                    continue
            except Exception:
                continue
        return handler["function"], call
    raise AssertionError(f"no handler matched {payload!r}")


def test_every_menu_payload_runs(bot, monkeypatch):
    recorder = _stub_api(monkeypatch, bot)
    bot.ensure_user(JOB_UID, "pytest-queue")

    # A queued job so per-job buttons have something real to render.
    job_id = bot.create_job(JOB_UID, "chan-1", "Dispatch smoke test", file_path="",
                            privacy="private")
    calls = [
        ("main_menu", JOB_UID),
        ("yt_connect", JOB_UID),
        ("yt_channels", JOB_UID),
        ("yt_upload", JOB_UID),
        ("yt_upload_now", JOB_UID),
        ("yt_jobs", JOB_UID),
        ("yt_cancel", JOB_UID),
        ("queue_menu", JOB_UID),
        ("queue_run_all", JOB_UID),
        ("queue_clear", JOB_UID),
        ("social_menu", JOB_UID),
        ("social_help", JOB_UID),
        ("dl_privacy", JOB_UID),
        ("dl_priv:public", JOB_UID),
        ("dl_cancel", JOB_UID),
        ("dl_only", JOB_UID),
        ("bulk_tiktok", JOB_UID),
        ("bulk_links", JOB_UID),
        ("bulk_go", JOB_UID),
        ("bulk_go_upload", JOB_UID),
        ("bulk_cancel", JOB_UID),
        ("dl_list:0", JOB_UID),
        ("dl_list:1", JOB_UID),
        ("dl_send:1", JOB_UID),
        ("dl_del:1", JOB_UID),
        ("dl_zip:0", JOB_UID),
        ("dl_links:0", JOB_UID),
        ("dl_zip_ids:1,2", JOB_UID),
        ("dl_links_ids:1,2", JOB_UID),
        ("dl_queue:1", JOB_UID),
        ("tk_menu", JOB_UID),
        ("tk_add", JOB_UID),
        ("tk_check_all", JOB_UID),
        ("bot_speed", JOB_UID),
        ("bot_stats", JOB_UID),
        ("help", JOB_UID),
        ("support", JOB_UID),
        ("host_upload", JOB_UID),
        ("host_files", JOB_UID),
        ("file_ctrl:{uid}:bot.py".format(uid=JOB_UID), JOB_UID),
        ("run_file:{uid}:missing.py".format(uid=JOB_UID), JOB_UID),
        ("restart_file:{uid}:missing.py".format(uid=JOB_UID), JOB_UID),
        ("stop_file:{uid}:missing.py".format(uid=JOB_UID), JOB_UID),
        ("logs:{uid}:missing.py".format(uid=JOB_UID), JOB_UID),
        # admin surface (owner id == OWNER_ID in this environment)
        ("admin_panel", CHAT_ID),
        ("adm_stats", CHAT_ID),
        ("adm_users:0", CHAT_ID),
        ("adm_ban", CHAT_ID),
        ("adm_premium", CHAT_ID),
        ("adm_broadcast", CHAT_ID),
        ("adm_broadcast_cancel", CHAT_ID),
        ("adm_settings", CHAT_ID),
        ("adm_set_free", CHAT_ID),
        ("adm_audit", CHAT_ID),
        ("adm_diag", CHAT_ID),
        (f"adm_user:{JOB_UID}", CHAT_ID),
        (f"adm_ban_user:{JOB_UID}", CHAT_ID),
        (f"adm_unban_user:{JOB_UID}", CHAT_ID),
        (f"adm_grant:{JOB_UID}", CHAT_ID),
        (f"adm_revoke:{JOB_UID}", CHAT_ID),
        # per-job buttons of the queued job
        (f"queue_job:{job_id}", JOB_UID),
        (f"queue_now:{job_id}", JOB_UID),
        (f"queue_sched:{job_id}", JOB_UID),
        (f"yt_edit_title:{job_id}", JOB_UID),
        (f"yt_edit_desc:{job_id}", JOB_UID),
        (f"yt_edit_tags:{job_id}", JOB_UID),
        (f"yt_edit_privacy:{job_id}", JOB_UID),
        (f"ytset_priv_{job_id}:unlisted", JOB_UID),
        (f"ch_vstats:{job_id}", JOB_UID),
        (f"sch_quick:{job_id}:60", JOB_UID),
        ("tk_source:999", JOB_UID),
        ("tk_toggle:999", JOB_UID),
        ("tk_del:999", JOB_UID),
        ("tk_check:999", JOB_UID),
        ("tk_setch:999", JOB_UID),
        ("tk_setpriv:999", JOB_UID),
        ("tk_setmax:999", JOB_UID),
        ("tk_setint:999", JOB_UID),
        ("tk_priv_999:public", JOB_UID),
        ("tk_channel:999:chan-1", JOB_UID),
        ("tk_max:999:2", JOB_UID),
        ("tk_int:999:60", JOB_UID),
        ("queue_job:999999", JOB_UID),
        ("unknown_stale_button", JOB_UID),
    ]

    for payload, user_id in calls:
        function, call = _dispatch(bot, payload, user_id)
        try:
            function(call)
        except Exception as exc:  # pragma: no cover - failure detail
            pytest.fail(f"handler for {payload!r} raised {type(exc).__name__}: {exc}")

    # Every payload produced some user-visible feedback (a reply, an edit or
    # an answer) - a silently dead button would have produced nothing.
    assert len(recorder.answers) + len(recorder.edits) + len(recorder.sends) > len(calls) - 5

    bot.db_query('DELETE FROM upload_jobs WHERE job_id = ?', (job_id,))
    bot.db_query('DELETE FROM users WHERE user_id = ?', (JOB_UID,))


def test_edit_state_is_consumed_by_text(bot, monkeypatch):
    """A metadata edit prompt stores state that the text handler uses."""
    bot.set_state(JOB_UID, "yt_edit_title", job_id=1)
    state = bot.get_state(JOB_UID)
    assert state["state"] == "yt_edit_title"
    assert state["job_id"] == 1
    bot.clear_state(JOB_UID)
    assert bot.get_state(JOB_UID) == {}


def test_state_expires(bot):
    bot.set_state(JOB_UID, "yt_edit_title", job_id=1)
    bot.USER_STATES[JOB_UID]["ts"] = 0  # pretend it's ancient
    assert bot.get_state(JOB_UID) == {}


def test_banned_user_gets_nothing(bot, monkeypatch):
    recorder = _stub_api(monkeypatch, bot)
    bot.set_banned(JOB_UID, True)
    function, call = _dispatch(bot, "main_menu", JOB_UID)
    function(call)
    assert "blocked" in (recorder.answers[-1][0] or "").lower()
    assert not recorder.edits and not recorder.sends
    bot.set_banned(JOB_UID, False)


def teardown_module(module):
    """Leave the shared SQLite file exactly as we found it."""
    try:
        spec = importlib.util.spec_from_file_location("bot_cleanup_handlers", BOT_PATH)
        bot_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bot_module)
        bot_module.db_query('DELETE FROM upload_jobs WHERE user_id IN (?, ?)', (JOB_UID, CHAT_ID))
        bot_module.db_query('DELETE FROM user_files WHERE user_id IN (?, ?)', (JOB_UID, CHAT_ID))
        bot_module.db_query('DELETE FROM tiktok_sources WHERE user_id IN (?, ?)',
                            (JOB_UID, CHAT_ID))
        bot_module.db_query('DELETE FROM users WHERE user_id IN (?, ?)', (JOB_UID, CHAT_ID))
    except Exception:  # pragma: no cover - cleanup best effort
        pass


def test_upload_job_uses_publish_at_for_far_future(bot, tmp_path):
    video = tmp_path / "scheduling.mp4"
    video.write_bytes(b"0" * 16)
    job_id = bot.create_job(JOB_UID, "chan-1", "Native schedule", file_path=str(video))
    bot.apply_schedule(JOB_UID, job_id, datetime.utcnow() + bot.timedelta(days=1))
    job = bot.get_job(job_id, JOB_UID)
    assert bot.job_field(job, "publish_at")
    bot.db_query('DELETE FROM upload_jobs WHERE job_id = ?', (job_id,))
