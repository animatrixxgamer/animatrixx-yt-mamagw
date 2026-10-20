"""Spam throttling and YouTube quota handling.

The bot used to message its owner on EVERY TikTok poll failure (every 15
minutes, forever) and to notify on every retry attempt of every failed upload.
These tests pin the fixed behaviour:

* a TikTok check failure is announced once, then stays silent, with a
  reminder only after NOTIFY_COOLDOWN_SECONDS, and recovery is announced,
* a quota error parks the job until the reset and notifies once per window,
* a revoked token fails a job immediately instead of retrying with a
  notification per attempt,
* the daily upload budget stops work before Google starts refusing,
* the hosting panel renders for admins,
* a zip upload keeps every extracted file, pip-installs requirements.txt
  and offers a Run button (the old flow deleted everything but the main
  script and installed nothing).
"""

import importlib.util
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

BOT_PATH = Path(__file__).resolve().parent.parent / "bot.py"
UID = 4242420777  # fixture user, deleted after each test module run


def _load_bot():
    os.environ.setdefault("SKIP_AUTO_INSTALL", "1")
    os.environ.setdefault("BOT_TOKEN", "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    os.environ.setdefault("OWNER_ID", "777001")
    if "yt_dlp" not in sys.modules:
        try:
            import yt_dlp  # noqa: F401
        except ImportError:
            import types

            stub = types.ModuleType("yt_dlp")
            stub.YoutubeDL = object  # type: ignore[attr-defined]
            sys.modules["yt_dlp"] = stub

    spec = importlib.util.spec_from_file_location("bot_limits_under_test", BOT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["bot_limits_under_test"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def bot():
    return _load_bot()


@pytest.fixture()
def sent(monkeypatch, bot):
    """Record every notify() the module sends."""
    box = []
    monkeypatch.setattr(bot, "notify", lambda uid, text, kb=None: box.append(text))
    return box


class StubYT:
    """Minimal yt_service stand-in: creds work, upload raises `error`."""

    def __init__(self, error: Exception):
        self.error = error
        self.upload_calls = 0

    def credentials_for_channel(self, uid, channel_id):
        return object()

    def upload_video(self, credentials, path, metadata, on_progress=None):
        self.upload_calls += 1
        raise self.error


# ── quota detection & reset timing ───────────────────────────────────

def test_is_quota_error_detects_google_reason_codes(bot):
    assert bot.is_quota_error("HttpError 403 <error><reason>quotaExceeded</reason>")
    assert bot.is_quota_error("dailyLimitExceeded: The daily quota was exceeded")
    assert bot.is_quota_error("Upload Limit Exceeded")
    assert bot.is_quota_error("429 rateLimitExceeded — slow down")
    assert not bot.is_quota_error("Video file missing on disk")
    assert not bot.is_quota_error("Connection reset by peer")
    assert not bot.is_quota_error("")


def test_quota_reset_is_soon_and_within_24h(bot):
    now = datetime(2026, 6, 1, 12, 0, 0)
    reset = bot.quota_reset_at(now)
    assert now < reset <= now + timedelta(hours=24)


def test_quota_budget_counts_todays_completed_uploads(bot):
    before = bot.quota_budget_left()
    bot.db_query(
        'INSERT INTO upload_jobs (user_id, channel_id, title, status, completed_at) '
        "VALUES (?, 'chan-budget-test', 'Budget filler', 'completed', ?)",
        (UID, datetime.utcnow().isoformat()))
    try:
        assert bot.quota_budget_left() == max(0, before - 1)
    finally:
        bot.db_query("DELETE FROM upload_jobs WHERE channel_id = 'chan-budget-test'")


# ── TikTok poll error throttle ───────────────────────────────────────

def test_source_error_announced_once_then_reminded(bot):
    now = datetime.utcnow()
    fresh = {"last_error_key": "blocked",
             "last_error_at": (now - timedelta(hours=1)).isoformat()}
    # same error, announced recently -> silent
    assert not bot.source_error_is_new(fresh, "blocked", now)
    # different error -> announce (it is a new problem)
    assert bot.source_error_is_new(fresh, "something else", now)
    # same error but the reminder window passed -> announce again
    stale = {"last_error_key": "blocked",
             "last_error_at": (now - timedelta(days=2)).isoformat()}
    assert bot.source_error_is_new(stale, "blocked", now)
    # first ever failure -> announce
    assert bot.source_error_is_new({}, "blocked", now)
    assert bot.source_error_is_new({"last_error_key": "blocked"}, "blocked", now)


def test_poll_source_announces_error_once_then_recovery(bot, monkeypatch, sent):
    bot.ensure_user(UID, "pytest-limits")
    src_id = bot.add_source(UID, "testhandle", "https://www.tiktok.com/@testhandle",
                            "chan-1")
    try:
        def boom(*_a, **_k):
            raise RuntimeError("TikTok blocked this IP")

        monkeypatch.setattr(bot, "list_profile_videos", boom)

        assert bot.poll_source(src_id) == 0
        assert len(sent) == 1 and "check failed" in sent[0]

        # Same error on the next poll: NO new message (this was the spam).
        assert bot.poll_source(src_id) == 0
        assert len(sent) == 1

        # After the reminder window the same error is announced exactly once.
        bot.db_query('UPDATE tiktok_sources SET last_error_at = ? WHERE source_id = ?',
                     ((datetime.utcnow() - timedelta(days=2)).isoformat(), src_id))
        assert bot.poll_source(src_id) == 0
        assert len(sent) == 2

        # The check works again: one recovery message.
        monkeypatch.setattr(bot, "list_profile_videos", lambda *_a, **_k: [])
        assert bot.poll_source(src_id) == 0
        assert len(sent) == 3 and "working again" in sent[2]

        # A fresh incident after recovery announces again.
        monkeypatch.setattr(bot, "list_profile_videos", boom)
        assert bot.poll_source(src_id) == 0
        assert len(sent) == 4
    finally:
        bot.db_query('DELETE FROM tiktok_seen WHERE source_id = ?', (src_id,))
        bot.db_query('DELETE FROM tiktok_sources WHERE source_id = ?', (src_id,))


def test_check_now_button_reports_a_failed_check(bot, monkeypatch):
    """The manual Check now button must surface a failed check, not 'nothing new'."""
    bot.ensure_user(UID, "pytest-limits")
    src_id = bot.add_source(UID, "checkhandle", "https://www.tiktok.com/@checkhandle",
                            "chan-1")
    sends = []
    monkeypatch.setattr(bot.bot, "answer_callback_query", lambda *a, **k: None)
    monkeypatch.setattr(bot.bot, "send_message",
                        lambda chat_id, text, **k: sends.append(text))

    class ImmediateThread:
        """Run the handler's worker inline so the assertion sees its message."""

        def __init__(self, target=None, daemon=None):
            self._target = target

        def start(self):
            if self._target:
                self._target()

    monkeypatch.setattr(bot.threading, "Thread", ImmediateThread)

    def boom(*_a, **_k):
        raise RuntimeError("TikTok blocked this IP")

    monkeypatch.setattr(bot, "list_profile_videos", boom)
    call = SimpleNamespace(
        id="cb", data=f"tk_check:{src_id}",
        from_user=SimpleNamespace(id=UID),
        message=SimpleNamespace(chat=SimpleNamespace(id=UID), message_id=1),
    )
    try:
        bot.cb_tk_check(call)
        assert sends, "the check button must answer even when the check failed"
        assert "the check failed" in sends[-1]
        assert "checkhandle" in sends[-1]

        # A working check with nothing new says so, without the error banner.
        monkeypatch.setattr(bot, "list_profile_videos", lambda *_a, **_k: [])
        sends.clear()
        bot.cb_tk_check(call)
        assert "nothing new" in sends[-1]
        assert "the check failed" not in sends[-1]
    finally:
        bot.db_query('DELETE FROM tiktok_seen WHERE source_id = ?', (src_id,))
        bot.db_query('DELETE FROM tiktok_sources WHERE source_id = ?', (src_id,))


# ── quota parking & token fail-fast ──────────────────────────────────

def test_quota_error_parks_job_and_notifies_once_per_window(bot, monkeypatch, sent,
                                                            tmp_path):
    bot.ensure_user(UID, "pytest-limits")
    service = StubYT(RuntimeError("HttpError 403 <reason>quotaExceeded</reason>"))
    monkeypatch.setattr(bot, "yt_service", service)
    monkeypatch.setattr(bot, "_YT_OK", True)
    monkeypatch.setattr(bot, "quota_budget_left", lambda: 3)  # budget fine; Google refused
    bot.set_setting("quota_paused_until", "")

    jobs = []
    for i in range(2):
        video = tmp_path / f"quota{i}.mp4"
        video.write_bytes(b"0" * 8)
        jobs.append(bot.create_job(UID, "chan-quota", f"Quota test {i}",
                                   file_path=str(video)))
    try:
        assert bot.run_job_upload(jobs[0]) is False
        job = bot.get_job(jobs[0])
        assert bot.job_field(job, "status") == "scheduled"
        scheduled = bot.job_field(job, "scheduled_at")
        assert scheduled and datetime.fromisoformat(scheduled) > datetime.utcnow()
        assert len(sent) == 1 and "quota" in sent[0].lower()
        paused = bot.get_setting("quota_paused_until")
        assert paused and datetime.fromisoformat(paused) > datetime.utcnow()

        # Second job, same window: parked WITHOUT a second message.
        assert bot.run_job_upload(jobs[1]) is False
        assert bot.job_field(bot.get_job(jobs[1]), "status") == "scheduled"
        assert len(sent) == 1
        assert service.upload_calls == 2  # both were actually attempted once
    finally:
        for job_id in jobs:
            bot.db_query('DELETE FROM upload_jobs WHERE job_id = ?', (job_id,))
        bot.db_query("DELETE FROM settings WHERE key LIKE 'quota_notice:%'")
        bot.set_setting("quota_paused_until", "")


def test_revoked_token_fails_fast_without_retry_spam(bot, monkeypatch, sent, tmp_path):
    bot.ensure_user(UID, "pytest-limits")
    service = StubYT(RuntimeError(
        "401 Unauthorized: invalid_grant Token has been expired, or revoked"))
    monkeypatch.setattr(bot, "yt_service", service)
    monkeypatch.setattr(bot, "_YT_OK", True)

    video = tmp_path / "revoked.mp4"
    video.write_bytes(b"0" * 8)
    job_id = bot.create_job(UID, "chan-revoked", "Revoked token test",
                            file_path=str(video))
    try:
        assert bot.run_job_upload(job_id) is False
        job = bot.get_job(job_id)
        # Immediate failure: no scheduled retry, no attempt 1/3 -> 2/3 -> 3/3 spam.
        assert bot.job_field(job, "status") == "failed"
        assert not bot.job_field(job, "scheduled_at")
        assert not any("attempt" in text.lower() for text in sent)
        assert service.upload_calls == 1
    finally:
        bot.db_query('DELETE FROM upload_jobs WHERE job_id = ?', (job_id,))


def test_transient_error_still_retries_but_announces_only_once(bot, monkeypatch, sent,
                                                               tmp_path):
    bot.ensure_user(UID, "pytest-limits")
    service = StubYT(RuntimeError("Connection reset by peer"))
    monkeypatch.setattr(bot, "yt_service", service)
    monkeypatch.setattr(bot, "_YT_OK", True)
    monkeypatch.setattr(bot, "quota_budget_left", lambda: 3)

    video = tmp_path / "flaky.mp4"
    video.write_bytes(b"0" * 8)
    job_id = bot.create_job(UID, "chan-flaky", "Flaky upload test",
                            file_path=str(video))
    try:
        assert bot.run_job_upload(job_id) is False
        job = bot.get_job(job_id)
        assert bot.job_field(job, "status") == "scheduled"
        assert int(bot.job_field(job, "retries")) == 1
        notices = [t for t in sent if f"#{job_id}" in t]
        assert len(notices) == 1 and "attempt 1/" in notices[0]

        # Clear the backoff so the next attempt runs immediately.
        bot.db_query('UPDATE upload_jobs SET scheduled_at = ? WHERE job_id = ?',
                     (datetime.utcnow().isoformat(), job_id))
        sent.clear()
        assert bot.run_job_upload(job_id) is False
        assert int(bot.job_field(bot.get_job(job_id), "retries")) == 2
        assert not any(f"#{job_id}" in t for t in sent)  # attempt 2 stays quiet
    finally:
        bot.db_query('DELETE FROM upload_jobs WHERE job_id = ?', (job_id,))


# ── hosting panel ────────────────────────────────────────────────────

def test_hosting_panel_renders(bot):
    text = bot.hosting_text()
    assert "Hosting panel" in text
    assert "Disk:" in text
    assert "Queue:" in text


def test_hosting_backup_creates_and_prunes(bot, tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "STORAGE_DIR", tmp_path)
    # One pre-existing "old" backup that exceeds the keep-7 window.
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for i in range(8):
        (backup_dir / f"bot-2020010{i}-000000.db").write_bytes(b"x")
    text = bot.backup_database()
    assert "backed up" in text.lower()
    assert len(list(backup_dir.glob("bot-*.db"))) == 7  # newest kept, oldest gone


# ── zip upload runner: keep files, install requirements, offer Run ──

def _make_zip(entries) -> bytes:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in entries.items():
            z.writestr(name, content)
    return buf.getvalue()


class _ImmediateThread:
    """Run the handler's worker inline so assertions see its messages."""

    def __init__(self, target=None, daemon=None):
        self._target = target

    def start(self):
        if self._target:
            self._target()


def _upload_zip(bot, monkeypatch, tmp_path, entries, fname="proj.zip"):
    """Drive handle_file_upload end-to-end against a fake Telegram API.

    Isolated in tmp_path, so the real storage folder is never touched, and
    pip is stubbed, so no network access happens.
    """
    import subprocess as real_subprocess

    owner = bot.OWNER_ID
    zip_bytes = _make_zip(entries)
    doc = SimpleNamespace(file_name=fname, file_id="fid-zip",
                          file_size=len(zip_bytes))
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=owner, username="pytest"),
        document=doc, chat=SimpleNamespace(id=UID))

    monkeypatch.setattr(bot, "STORAGE_DIR", tmp_path)
    sends = []   # (text, reply_markup)
    pip_calls = []
    monkeypatch.setattr(bot.bot, "reply_to",
                        lambda m, text, **k: SimpleNamespace(message_id=1))
    monkeypatch.setattr(bot.bot, "edit_message_text", lambda *a, **k: None)
    monkeypatch.setattr(bot.bot, "get_file",
                        lambda fid: SimpleNamespace(file_path="tg/fake.zip"))
    monkeypatch.setattr(bot.bot, "download_file", lambda path: zip_bytes)
    monkeypatch.setattr(
        bot.bot, "send_message",
        lambda chat_id, text, **k: sends.append((text, k.get("reply_markup"))))
    monkeypatch.setattr(bot.threading, "Thread", _ImmediateThread)

    def fake_run(cmd, **kw):
        pip_calls.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(
        bot, "subprocess",
        SimpleNamespace(run=fake_run, TimeoutExpired=real_subprocess.TimeoutExpired))

    bot.db_query(
        "DELETE FROM user_files WHERE user_id = ? AND file_name = ?", (owner, fname))
    try:
        bot.handle_file_upload(message)
        return SimpleNamespace(sends=sends, pip_calls=pip_calls,
                               folder=tmp_path / "uploads" / str(owner))
    finally:
        bot.db_query(
            "DELETE FROM user_files WHERE user_id = ? AND file_name = ?", (owner, fname))


def test_zip_keeps_all_files_installs_requirements_and_offers_run(
        bot, monkeypatch, tmp_path):
    result = _upload_zip(bot, monkeypatch, tmp_path, {
        "main.py": "import requests\nprint('hi')\n",
        "helper.py": "def assist():\n    return 1\n",
        "requirements.txt": "requests==2.32.0\n",
    })
    folder = result.folder

    # Everything the zip shipped survives extraction. The old flow kept only
    # the main script and deleted the rest (helper.py, requirements.txt, ...).
    assert (folder / "main.py").is_file()
    assert (folder / "helper.py").is_file()
    assert (folder / "requirements.txt").is_file()

    # requirements.txt was actually handed to pip.
    req_path = str(folder / "requirements.txt")
    assert any("-r" in cmd and req_path in cmd for cmd in result.pip_calls), \
        result.pip_calls

    # The reply states the result and offers working Run + Logs buttons.
    finals = [t for t, kb in result.sends if "Extracted!" in t]
    assert finals, result.sends
    assert "requirements.txt installed" in finals[-1]
    kb = [kb for t, kb in result.sends if "Extracted!" in t][-1]
    callbacks = [b.callback_data for row in kb.keyboard for b in row]
    assert f"run_file:{bot.OWNER_ID}:main.py" in callbacks
    assert any(c.startswith("logs:") for c in callbacks)


def test_zip_unwraps_github_style_folder(bot, monkeypatch, tmp_path):
    result = _upload_zip(bot, monkeypatch, tmp_path, {
        "myrepo-main/main.py": "print('nested')\n",
        "myrepo-main/lib/util.py": "X = 1\n",
        "myrepo-main/requirements.txt": "requests\n",
    }, fname="gh.zip")
    folder = result.folder
    assert (folder / "main.py").is_file()
    assert (folder / "lib" / "util.py").is_file()
    assert (folder / "requirements.txt").is_file()
    assert any("-r" in cmd for cmd in result.pip_calls)


def test_zip_rejects_paths_outside_the_extract_folder(bot, monkeypatch, tmp_path):
    result = _upload_zip(bot, monkeypatch, tmp_path, {
        "../evil.py": "os.system('boom')\n",
        "main.py": "print('ok')\n",
    }, fname="evil.zip")
    assert any("Extract error" in t for t, _kb in result.sends), result.sends
    # Nothing was extracted at all - not even the legit main.py.
    assert not (result.folder / "evil.py").exists()
    assert not (result.folder / "main.py").exists()


def test_install_project_deps_notes_success_and_failure(bot, monkeypatch, tmp_path):
    import subprocess as real_subprocess

    # Nothing declared -> honest note, no subprocess at all.
    assert "nothing to install" in bot.install_project_deps(tmp_path)

    (tmp_path / "requirements.txt").write_text("requests==2.32.0\n")
    calls = []

    def failing(cmd, **kw):
        calls.append(list(cmd))
        return SimpleNamespace(
            returncode=1, stdout="",
            stderr="ERROR: Could not find a version that satisfies the requirement nope")

    monkeypatch.setattr(
        bot, "subprocess",
        SimpleNamespace(run=failing, TimeoutExpired=real_subprocess.TimeoutExpired))
    note = bot.install_project_deps(tmp_path)
    assert "could not install requirements.txt" in note
    assert "Could not find a version" in note  # the real pip error is surfaced
    assert any("-r" in cmd for cmd in calls)

    def ok(cmd, **kw):
        calls.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(
        bot, "subprocess",
        SimpleNamespace(run=ok, TimeoutExpired=real_subprocess.TimeoutExpired))
    assert "requirements.txt installed" in bot.install_project_deps(tmp_path)


def teardown_module(module):
    try:
        spec = importlib.util.spec_from_file_location("bot_limits_cleanup", BOT_PATH)
        bot_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bot_module)
        bot_module.db_query('DELETE FROM upload_jobs WHERE user_id = ?', (UID,))
        bot_module.db_query('DELETE FROM tiktok_sources WHERE user_id = ?', (UID,))
        bot_module.db_query('DELETE FROM users WHERE user_id = ?', (UID,))
        bot_module.db_query("DELETE FROM settings WHERE key LIKE 'quota_%'")
    except Exception:  # pragma: no cover - cleanup best effort
        pass
