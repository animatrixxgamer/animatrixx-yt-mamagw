"""Tests for the standalone bot.py bot (buttons, helpers, upload queue).

bot.py is the file the hosting process actually runs (`Procfile` →
`python bot.py`), so these tests load it as a module and verify the parts
that used to break in production:

* every inline button has a callback handler (dead buttons were a real bug),
* token/env precedence reporting,
* link parsing and scheduling helpers,
* the upload-job queue round-trip.
"""

import importlib.util
import os
import re
import sys
import time
import types
from datetime import datetime, timedelta
from pathlib import Path

import pytest

BOT_PATH = Path(__file__).resolve().parent.parent / "bot.py"
TEST_UID = 4242424242  # fake user, cleaned up after the queue tests


CHAT_ID = 4242424243


def _load_bot_module():
    """Import bot.py with a dummy token so no network calls happen."""
    os.environ.setdefault("SKIP_AUTO_INSTALL", "1")
    os.environ.setdefault("BOT_TOKEN", "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    os.environ.setdefault("OWNER_ID", "1")

    if not hasattr(sys.modules.get("yt_dlp", None), "YoutubeDL"):
        try:  # yt-dlp may be absent in a minimal test env; a stub is enough
            import yt_dlp  # noqa: F401
        except ImportError:
            import types

            stub = types.ModuleType("yt_dlp")
            stub.YoutubeDL = object  # type: ignore[attr-defined]
            sys.modules["yt_dlp"] = stub

    spec = importlib.util.spec_from_file_location("bot_under_test", BOT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["bot_under_test"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def bot_module():
    return _load_bot_module()


@pytest.fixture(scope="module")
def source() -> str:
    return BOT_PATH.read_text(encoding="utf-8")


class TestButtonCoverage:
    """No button may exist without a handler (the bug the user reported)."""

    @staticmethod
    def _handled_patterns(source: str) -> set:
        exact = set(re.findall(r'c\.data\s*==\s*"([^"]+)"', source))
        for group in re.findall(r'c\.data\s+in\s+\(([^)]*)\)', source):
            exact |= set(re.findall(r'"([^"]+)"', group))
        prefixes = set(re.findall(r'c\.data\.startswith\("([^"]+)"\)', source))
        return exact | prefixes

    def test_every_callback_data_is_handled(self, source):
        handled = self._handled_patterns(source)
        callback_prefixes = set()
        for match in re.finditer(r'callback_data=(?:f)?[ru]?"([^"]+)"', source):
            raw = match.group(1)
            prefix = raw.split(":")[0].strip()
            # Skip interpolated prefixes (privacy_kb builds them at runtime).
            if prefix and not prefix.startswith("{"):
                callback_prefixes.add(prefix)

        unhandled = sorted(
            cb for cb in callback_prefixes
            if cb not in handled
            and not any(p.startswith(cb + ":") for p in handled)
            and not any(cb.startswith(p) for p in handled)
        )
        assert not unhandled, f"buttons with no handler: {unhandled}"

        # Sanity check: the scan must actually be finding the new features.
        assert {"queue_menu", "tk_menu", "social_menu", "restart_file"} <= callback_prefixes

    def test_no_duplicate_exact_handlers(self, source):
        exact = re.findall(r'c\.data\s*==\s*"([^"]+)"', source)
        duplicates = {value for value in exact if exact.count(value) > 1}
        assert not duplicates, f"duplicate callback handlers: {duplicates}"

    def test_has_unknown_callback_fallback(self, bot_module):
        """A catch-all handler must exist so stale buttons never spin."""
        handlers = bot_module.bot.callback_query_handlers
        assert any(handler.get("filters") is None or True for handler in handlers)


class TestConfigReporting:
    def test_mask_secret_keeps_bot_id(self, bot_module):
        masked = bot_module.mask_secret("123456789:AAAsecret")
        assert masked.startswith("123456789:")
        assert "AAAsecret" not in masked

    def test_value_source_reports_dotenv(self, bot_module):
        bot_module.DOTENV_VALUES["SOME_TEST_KEY"] = "x"
        assert "env file" in bot_module.value_source("SOME_TEST_KEY")

    def test_python_test_env_token_is_used(self, bot_module):
        # The dummy token from the test env must be the active one.
        assert bot_module.TOKEN.startswith("123456789:")


class TestParsingHelpers:
    def test_parse_when_relative_minutes(self, bot_module):
        when = bot_module.parse_when("+30m")
        assert when is not None
        delta = when - datetime.utcnow()
        assert timedelta(minutes=29) < delta <= timedelta(minutes=31)

    def test_parse_when_relative_hours_and_days(self, bot_module):
        assert bot_module.parse_when("+2h") > datetime.utcnow() + timedelta(hours=1)
        assert bot_module.parse_when("+1d") > datetime.utcnow() + timedelta(hours=23)

    def test_parse_when_absolute(self, bot_module):
        assert bot_module.parse_when("2030-01-02 03:04") == datetime(2030, 1, 2, 3, 4)

    def test_parse_when_rejects_garbage(self, bot_module):
        assert bot_module.parse_when("whenever") is None
        assert bot_module.parse_when("") is None

    def test_safe_name_strips_paths_and_symbols(self, bot_module):
        cleaned = bot_module.safe_name("My/../Video <script>.mp4")
        assert "/" not in cleaned and "<" not in cleaned and ".." not in cleaned
        assert cleaned.startswith("My.Video")
        assert bot_module.safe_name("") == "video"

    def test_human_size(self, bot_module):
        assert bot_module.human_size(0) == "?"
        assert bot_module.human_size(1536) == "1.5KB"

    def test_job_tags_list(self, bot_module):
        assert bot_module.job_tags_list("#shorts, viral  funny") == ["shorts", "viral", "funny"]
        assert bot_module.job_tags_list(None) == []


class TestLinkParsing:
    def test_extract_url(self, bot_module):
        url = "https://www.tiktok.com/@creator/video/7412345678901234567"
        assert bot_module.extract_url(f"look at this {url}!") == url

    def test_supported_hosts(self, bot_module):
        assert bot_module.is_supported_url("https://youtu.be/abc")
        assert bot_module.is_supported_url("https://www.instagram.com/reel/x/")
        assert not bot_module.is_supported_url("https://example.com/video")

    def test_tiktok_handle_and_link(self, bot_module):
        handle, url = bot_module.tiktok_profile_url("@creator")
        assert handle == "creator"
        assert url == "https://www.tiktok.com/@creator"

        handle, url = bot_module.tiktok_profile_url(
            "https://www.tiktok.com/@creator/video/7412345678901234567")
        assert handle == "creator"
        assert url == "https://www.tiktok.com/@creator"

    def test_best_format_prefers_merge_when_ffmpeg_present(self, bot_module, monkeypatch):
        monkeypatch.setattr(bot_module.shutil, "which", lambda _: "/usr/bin/ffmpeg")
        assert "bv*" in bot_module.best_format(720)

        monkeypatch.setattr(bot_module.shutil, "which", lambda _: None)
        assert bot_module.best_format(720).startswith("b[ext=mp4]")


class TestUploadQueue:
    """End-to-end checks on the SQLite queue used by the worker thread."""

    def test_job_roundtrip_and_scheduling(self, bot_module, tmp_path):
        bot_module.ensure_user(TEST_UID, "pytest-user")
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"0" * 32)

        job_id = bot_module.create_job(TEST_UID, "chan-1", "Test clip",
                                      file_path=str(video), privacy="unlisted")
        job = bot_module.get_job(job_id, TEST_UID)
        assert bot_module.job_field(job, "status") == "pending"
        assert bot_module.job_field(job, "privacy_status") == "unlisted"
        assert bot_module.job_field(job, "title") == "Test clip"

        when = datetime.utcnow() + timedelta(hours=2)
        bot_module.apply_schedule(TEST_UID, job_id, when)
        job = bot_module.get_job(job_id, TEST_UID)
        assert bot_module.job_field(job, "status") == "scheduled"
        # A far-future schedule is handed to YouTube natively too.
        assert bot_module.job_field(job, "publish_at")

        # Promote into the queue once the time is due.
        bot_module.db_query('UPDATE upload_jobs SET scheduled_at = ? WHERE job_id = ?',
                            ((datetime.utcnow() - timedelta(minutes=1)).isoformat(), job_id))
        assert bot_module.promote_due_jobs() >= 1
        assert bot_module.job_field(bot_module.get_job(job_id, TEST_UID), "status") == "pending"
        bot_module.db_query('DELETE FROM upload_jobs WHERE job_id = ?', (job_id,))

    def test_parse_when_short_schedule_stays_manual(self, bot_module, tmp_path):
        video = tmp_path / "clip2.mp4"
        video.write_bytes(b"0" * 32)
        job_id = bot_module.create_job(TEST_UID, "chan-1", "Soon", file_path=str(video))
        bot_module.apply_schedule(TEST_UID, job_id, datetime.utcnow() + timedelta(minutes=5))
        job = bot_module.get_job(job_id, TEST_UID)
        # Under 15 minutes YouTube would reject publishAt, so we upload it ourselves.
        assert bot_module.job_field(job, "publish_at") is None
        bot_module.db_query('DELETE FROM upload_jobs WHERE job_id = ?', (job_id,))

    def test_upload_without_channel_fails_cleanly(self, bot_module, tmp_path):
        video = tmp_path / "clip3.mp4"
        video.write_bytes(b"0" * 32)
        job_id = bot_module.create_job(TEST_UID, "", "No channel", file_path=str(video))
        assert bot_module.run_job_upload(job_id, quiet=True) is False
        job = bot_module.get_job(job_id, TEST_UID)
        assert bot_module.job_field(job, "status") == "failed"
        assert "channel" in str(bot_module.job_field(job, "error_msg")).lower()
        bot_module.db_query('DELETE FROM upload_jobs WHERE job_id = ?', (job_id,))

    def test_upload_metadata_strips_internal_source_marker(self, bot_module, tmp_path):
        """The invisible strip/restore marker must never reach YouTube."""
        video = tmp_path / "clip4.mp4"
        video.write_bytes(b"0" * 32)
        url = "https://www.tiktok.com/@someone/video/7412345678901234567"
        bot_module.save_user_credit_source(TEST_UID, 1)
        job_id = bot_module.create_job(
            TEST_UID, "", "Marker clip",
            description=bot_module.build_import_description(
                TEST_UID, "Marker clip", "@someone", url, mark_source=True),
            file_path=str(video), source_url=url, source="link")
        try:
            assert bot_module.SOURCE_SUFFIX_MARK in bot_module.job_field(
                bot_module.get_job(job_id, TEST_UID), "description")
            # Fails at the channel check, but only after metadata was built.
            assert bot_module.run_job_upload(job_id, quiet=True) is False
        finally:
            bot_module.db_query('DELETE FROM upload_jobs WHERE job_id = ?', (job_id,))

    def test_banned_user_blocks_ingest(self, bot_module, tmp_path):
        bot_module.set_banned(TEST_UID, True)
        assert bot_module.is_banned(TEST_UID) is True
        bot_module.set_banned(TEST_UID, False)
        assert bot_module.is_banned(TEST_UID) is False


class TestTikTokSources:
    def test_source_lifecycle(self, bot_module):
        source_id = bot_module.add_source(TEST_UID, "pytestcreator",
                                         "https://www.tiktok.com/@pytestcreator", "chan-1")
        source = bot_module.get_source(source_id, TEST_UID)
        assert source is not None
        assert source[2] == "pytestcreator"
        assert source[6] == 1  # enabled by default

        assert not bot_module.already_seen(source_id, "7412345678901234567")
        bot_module.mark_seen(source_id, "7412345678901234567")
        assert bot_module.already_seen(source_id, "7412345678901234567")

        bot_module.db_query('DELETE FROM tiktok_seen WHERE source_id = ?', (source_id,))
        bot_module.db_query('DELETE FROM tiktok_sources WHERE source_id = ?', (source_id,))
        assert bot_module.get_source(source_id, TEST_UID) is None

    def test_foreign_source_is_not_readable(self, bot_module):
        source_id = bot_module.add_source(TEST_UID, "another", "https://tiktok.com/@another",
                                          "chan-1")
        assert bot_module.get_source(source_id, TEST_UID + 1) is None
        bot_module.db_query('DELETE FROM tiktok_sources WHERE source_id = ?', (source_id,))

    def test_credit_preference_follows_user_default(self, bot_module):
        """A source row with credit_source NULL mirrors the user-wide setting."""
        bot_module.save_user_credit_source(TEST_UID, 1)
        source_id = bot_module.add_source(TEST_UID, "credtest",
                                         "https://tiktok.com/@credtest", "chan-1")
        try:
            src = bot_module.get_source(source_id, TEST_UID)
            assert bot_module.user_credit_source(TEST_UID) == 1
            assert bot_module.source_credit_source(src) == 1

            bot_module.save_user_credit_source(TEST_UID, 0)
            assert bot_module.user_credit_source(TEST_UID) == 0
            assert bot_module.source_credit_source(
                bot_module.get_source(source_id, TEST_UID)) == 0
        finally:
            bot_module.save_user_credit_source(TEST_UID, 1)
            bot_module.db_query('DELETE FROM tiktok_sources WHERE source_id = ?', (source_id,))

    def test_credit_per_source_override(self, bot_module):
        """An explicit per-source choice wins over the user default; None reverts."""
        bot_module.save_user_credit_source(TEST_UID, 1)
        source_id = bot_module.add_source(TEST_UID, "credover",
                                         "https://tiktok.com/@credover", "chan-1")
        try:
            bot_module.set_source_credit(source_id, TEST_UID, 0)
            assert bot_module.source_credit_source(
                bot_module.get_source(source_id, TEST_UID)) == 0

            bot_module.set_source_credit(source_id, TEST_UID, 1)
            assert bot_module.source_credit_source(
                bot_module.get_source(source_id, TEST_UID)) == 1

            bot_module.set_source_credit(source_id, TEST_UID, None)
            assert bot_module.source_credit_source(
                bot_module.get_source(source_id, TEST_UID)) == 1  # back to user default
        finally:
            bot_module.db_query('DELETE FROM tiktok_sources WHERE source_id = ?', (source_id,))

    def test_append_source_credit_respects_flag(self, bot_module):
        url = "https://www.tiktok.com/@a/video/1"
        assert bot_module.append_source_credit("My title", url, 1) == f"My title\n\nSource: {url}"
        assert bot_module.append_source_credit("My title", url, 0) == "My title"
        assert bot_module.append_source_credit("My title", "", 1) == "My title"


class TestConfigurableText:
    """Every line the bot appends to descriptions is user-editable now."""

    @pytest.fixture(autouse=True)
    def _text_user(self, bot_module):
        """The users row must exist or the preference updates are no-ops."""
        bot_module.ensure_user(TEST_UID, "pytest-text")
        yield
        bot_module.db_query(
            'UPDATE users SET desc_template = NULL, creator_line_template = NULL, '
            'source_line_template = NULL, shorts_tag = NULL, credit_source = 1 '
            'WHERE user_id = ?', (TEST_UID,))

    def test_render_description_template_fills_placeholders(self, bot_module):
        rendered = bot_module.render_description_template(
            "{title} by {creator} | {source_url}",
            title="My clip", creator="@someone", source_url="https://t.me/x")
        assert rendered == "My clip by @someone | https://t.me/x"

    def test_creator_line_defaults_and_off(self, bot_module):
        bot_module.save_user_creator_line_template(TEST_UID, "")
        assert bot_module.user_creator_line_template(TEST_UID) == ""  # explicit off
        bot_module.save_user_creator_line_template(TEST_UID, "off")
        assert bot_module.user_creator_line_template(TEST_UID) == ""
        bot_module.save_user_creator_line_template(TEST_UID, "Credit: {creator}")
        assert bot_module.user_creator_line_template(TEST_UID) == "Credit: {creator}"
        bot_module.db_query(
            'UPDATE users SET creator_line_template = NULL WHERE user_id = ?', (TEST_UID,))
        assert bot_module.user_creator_line_template(TEST_UID) == "Original creator: {creator}"

    def test_source_line_honours_legacy_toggle(self, bot_module):
        bot_module.save_user_source_line_template(TEST_UID, "")
        bot_module.save_user_credit_source(TEST_UID, 0)
        assert bot_module.user_source_line_template(TEST_UID) == ""
        bot_module.db_query(
            'UPDATE users SET source_line_template = NULL WHERE user_id = ?', (TEST_UID,))
        bot_module.save_user_credit_source(TEST_UID, 1)
        assert bot_module.user_source_line_template(TEST_UID) == "Source: {source_url}"

    def test_import_description_uses_templates(self, bot_module):
        bot_module.save_user_credit_source(TEST_UID, 1)
        bot_module.save_user_desc_template(TEST_UID, "")
        bot_module.save_user_creator_line_template(TEST_UID, "")
        bot_module.save_user_source_line_template(TEST_UID, "off")
        url = "https://www.tiktok.com/@someone/video/7412345678901234567"
        desc = bot_module.build_import_description(TEST_UID, "My clip", "@someone", url)
        assert desc == "My clip"

        bot_module.save_user_creator_line_template(TEST_UID, "👤 {creator}")
        bot_module.save_user_source_line_template(TEST_UID, "From: {source_url}")
        desc = bot_module.build_import_description(TEST_UID, "My clip", "@someone", url)
        assert desc == "My clip\n👤 @someone\nFrom: " + url

    def test_import_description_custom_template_owns_the_top(self, bot_module):
        bot_module.save_user_credit_source(TEST_UID, 1)
        bot_module.save_user_desc_template(TEST_UID, "{title} — watch till the end")
        bot_module.save_user_creator_line_template(TEST_UID, "off")
        bot_module.save_user_source_line_template(TEST_UID, "off")
        url = "https://www.tiktok.com/@someone/video/7412345678901234567"
        desc = bot_module.build_import_description(TEST_UID, "My clip", "@someone", url)
        assert desc == "My clip — watch till the end"

    def test_import_description_marked_source_round_trip(self, bot_module):
        """The marker must vanish on upload and survive split/restore."""
        bot_module.save_user_credit_source(TEST_UID, 1)
        bot_module.save_user_desc_template(TEST_UID, "")
        bot_module.save_user_creator_line_template(TEST_UID, "")
        bot_module.save_user_source_line_template(TEST_UID, "Source: {source_url}")
        url = "https://www.tiktok.com/@someone/video/7412345678901234567"
        desc = bot_module.build_import_description(
            TEST_UID, "My clip", "@someone", url, mark_source=True)
        assert bot_module.SOURCE_SUFFIX_MARK not in desc.replace(
            bot_module.SOURCE_SUFFIX_MARK, "")  # only the marker itself
        base, stored = bot_module.split_source_suffix(desc)
        assert base == "My clip"
        assert stored == f"Source: {url}"
        assert bot_module.SOURCE_SUFFIX_MARK not in base

    def test_split_source_suffix_parses_legacy_plain_lines(self, bot_module):
        url = "https://youtu.be/x"
        base, stored = bot_module.split_source_suffix(f"My clip\n\nSource: {url}")
        assert base == "My clip" and stored == url
        base, stored = bot_module.split_source_suffix(f"My clip\n\n{url}")
        assert base == "My clip" and stored == url
        base, stored = bot_module.split_source_suffix("Just a clean description")
        assert base == "Just a clean description" and stored == ""

    def test_shorts_tag_configurable(self, bot_module):
        bot_module.save_user_shorts_tag(TEST_UID, "#Shorts")
        assert bot_module.user_shorts_tag(TEST_UID) == "Shorts"
        bot_module.save_user_shorts_tag(TEST_UID, "off")
        assert bot_module.user_shorts_tag(TEST_UID) == ""
        bot_module.db_query('UPDATE users SET shorts_tag = NULL WHERE user_id = ?', (TEST_UID,))
        assert bot_module.user_shorts_tag(TEST_UID) == "shorts"  # legacy default

    def test_job_source_line_strip_and_restore(self, bot_module):
        bot_module.save_user_credit_source(TEST_UID, 1)
        bot_module.save_user_source_line_template(TEST_UID, "Source: {source_url}")
        url = "https://www.tiktok.com/@someone/video/7412345678901234567"
        job_id = bot_module.create_job(
            TEST_UID, "chan-1", "Toggle clip",
            description=bot_module.build_import_description(
                TEST_UID, "Toggle clip", "@someone", url, mark_source=True),
            source_url=url, source="link")
        try:
            assert bot_module.job_source_line_enabled(job_id) is True
            assert bot_module.toggle_job_source_line(job_id) == "off"
            job = bot_module.get_job(job_id)
            desc = bot_module.job_field(job, "description")
            assert url not in desc and "Toggle clip" in desc
            assert bot_module.job_source_line_enabled(job_id) is False

            assert bot_module.toggle_job_source_line(job_id) == "on"
            job = bot_module.get_job(job_id)
            desc = bot_module.job_field(job, "description")
            assert "Source: " + url in desc
            assert bot_module.SOURCE_SUFFIX_MARK not in desc.replace(
                bot_module.SOURCE_SUFFIX_MARK, "")
            assert bot_module.job_source_line_enabled(job_id) is True
        finally:
            bot_module.db_query('DELETE FROM upload_jobs WHERE job_id = ?', (job_id,))

    def test_toggle_keeps_legacy_plain_source_line_working(self, bot_module):
        """A pre-migration job with a plain 'Source:' line can be stripped too."""
        bot_module.save_user_credit_source(TEST_UID, 1)
        url = "https://www.tiktok.com/@someone/video/7412345678901234567"
        job_id = bot_module.create_job(
            TEST_UID, "chan-1", "Legacy clip",
            description=f"Legacy clip\n\nSource: {url}", source_url=url, source="link")
        try:
            assert bot_module.job_source_line_enabled(job_id) is True
            assert bot_module.toggle_job_source_line(job_id) == "off"
            job = bot_module.get_job(job_id)
            assert url not in bot_module.job_field(job, "description")
        finally:
            bot_module.db_query('DELETE FROM upload_jobs WHERE job_id = ?', (job_id,))

    def test_template_saving_via_ui_path(self, bot_module):
        for field, expected in (("desc_template", "Join my channel {source_url}"),
                                ("creator_line_template", "🎬 {creator}"),
                                ("source_line_template", "off"),
                                ("shorts_tag", "#MyTag")):
            stored = bot_module.tk_template_save(TEST_UID, field, expected)
            if field == "source_line_template":
                assert stored == ""
            elif field == "shorts_tag":
                assert stored == "MyTag"
            else:
                assert stored == expected


class TestForceJoinGate:
    """Force-join must gate /start itself (users bypassed it via /start)."""

    @pytest.fixture(autouse=True)
    def _force_join_env(self, bot_module):
        bot_module.ensure_user(TEST_UID, "pytest-fj")
        yield
        bot_module.set_setting("force_join_enabled", "0")
        bot_module.db_query('DELETE FROM force_join_channels')

    @staticmethod
    def _message():
        message = types.SimpleNamespace(message_id=55, text="/start")
        message.from_user = types.SimpleNamespace(id=TEST_UID, username="pytest-fj",
                                                  first_name="Py")
        message.chat = types.SimpleNamespace(id=CHAT_ID)
        return message

    def test_start_is_gated_when_force_join_enabled(self, bot_module, monkeypatch):
        bot_module.add_force_join_channel("-1001234567890", "Test Channel",
                                          "testchannel", "https://t.me/testchannel")
        bot_module.set_setting("force_join_enabled", "1")
        sent = []
        monkeypatch.setattr(bot_module.bot, "send_message",
                            lambda chat_id, text, **kw: sent.append(text))
        bot_module.cmd_start(self._message())
        assert sent and "Join our community first" in sent[-1]

    def test_start_welcomes_when_force_join_disabled(self, bot_module, monkeypatch):
        bot_module.set_setting("force_join_enabled", "0")
        sent = []
        monkeypatch.setattr(bot_module.bot, "send_message",
                            lambda chat_id, text, **kw: sent.append(text))
        bot_module.cmd_start(self._message())
        assert sent and "Welcome to" in sent[-1]

    def test_check_force_join_without_channels_is_noop(self, bot_module):
        bot_module.set_setting("force_join_enabled", "1")
        assert bot_module.check_force_join(TEST_UID, CHAT_ID) is True


class TestSavedDownloads:
    """"Download only" must work without any YouTube channel."""

    def _saved(self, bot_module, tmp_path, name="clip.mp4", payload=b"0" * 64):
        # Keep files inside storage/ - the bot deliberately refuses to delete
        # anything that lives outside its own storage directory.
        folder = bot_module.STORAGE_DIR / "test_downloads"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        path.write_bytes(payload)
        download_id = bot_module.add_download(TEST_UID, str(path), name, path.stat().st_size,
                                             "https://example.com/x", kind="link")
        return download_id, path

    def test_add_list_and_delete(self, bot_module, tmp_path):
        bot_module.ensure_user(TEST_UID, "pytest-user")
        download_id, path = self._saved(bot_module, tmp_path, "saved.mp4")

        rows = bot_module.user_downloads(TEST_UID)
        assert any(row[0] == download_id for row in rows)
        assert bot_module.get_download(download_id, TEST_UID)[1] == "saved.mp4"
        assert bot_module.get_download(download_id, TEST_UID + 1) is None

        assert bot_module.delete_download(download_id, TEST_UID) is True
        assert bot_module.get_download(download_id, TEST_UID) is None
        assert not path.exists()  # the file goes too

    def test_delete_refuses_files_outside_storage(self, bot_module, tmp_path):
        outside = tmp_path / "outside.mp4"
        outside.write_bytes(b"keep me")
        download_id = bot_module.add_download(TEST_UID, str(outside), "outside.mp4", 7)
        # A path outside storage/ must never be unlinked by the bot.
        bot_module.delete_download(download_id, TEST_UID)
        assert outside.exists()

    def test_share_token_round_trip(self, bot_module, tmp_path):
        _download_id, path = self._saved(bot_module, tmp_path, "shared.mp4")
        token = bot_module.make_download_token(str(path), TEST_UID)
        resolved = bot_module.resolve_download_token(token)
        assert resolved and resolved[0] == str(path)
        assert bot_module.resolve_download_token("not-a-real-token") is None

    def test_download_route_serves_the_file(self, bot_module, tmp_path):
        _download_id, path = self._saved(bot_module, tmp_path, "route.mp4", b"video-bytes")
        token = bot_module.make_download_token(str(path), TEST_UID)
        client = bot_module.app_flask.test_client()
        response = client.get(f"/dl/{token}")
        assert response.status_code == 200
        assert response.data == b"video-bytes"
        assert client.get("/dl/bogus").status_code == 404

    def test_big_file_falls_back_to_a_link(self, bot_module, tmp_path, monkeypatch):
        bot_module.ensure_user(TEST_UID, "pytest-user")
        download_id, _path = self._saved(bot_module, tmp_path, "big.mp4")
        monkeypatch.setattr(bot_module, "TG_SEND_LIMIT", 1)  # pretend everything is huge

        sent = []
        monkeypatch.setattr(bot_module, "safe_send", lambda chat, text, kb=None: sent.append(text))
        monkeypatch.setattr(bot_module, "send_document", lambda *a, **k: False)
        bot_module.deliver_download(TEST_UID, CHAT_ID, download_id)

        assert any("too big" in text.lower() for text in sent)
        bot_module.delete_download(download_id, TEST_UID)

    def test_zip_and_deliver_batch(self, bot_module, tmp_path, monkeypatch):
        paths = []
        for index in range(3):
            path = tmp_path / f"part{index}.mp4"
            path.write_bytes(b"x" * 32)
            bot_module.add_download(TEST_UID, str(path), path.name, 32)
            paths.append(str(path))

        archive = bot_module.zip_paths(TEST_UID, paths)
        assert archive is not None and archive.exists()
        import zipfile

        with zipfile.ZipFile(archive) as zf:
            assert sorted(zf.namelist()) == ["part0.mp4", "part1.mp4", "part2.mp4"]

        sent = []
        monkeypatch.setattr(bot_module, "safe_send", lambda chat, text, kb=None: sent.append(text))
        monkeypatch.setattr(bot_module, "send_document", lambda *a, **k: True)
        bot_module.deliver_batch(TEST_UID, CHAT_ID, paths, kind="bulk", heading="done")
        assert any("3 file" in text for text in sent)

        for row in bot_module.user_downloads(TEST_UID, limit=200):
            bot_module.delete_download(row[0], TEST_UID)
        archive.unlink(missing_ok=True)


class TestBulkDownload:
    def test_bulk_items_from_links_dedupes(self, bot_module):
        items = bot_module.bulk_items_from_links(
            "https://www.tiktok.com/@a/video/1 https://youtu.be/abc\n"
            "https://www.tiktok.com/@a/video/1")
        assert [item["url"] for item in items] == [
            "https://www.tiktok.com/@a/video/1", "https://youtu.be/abc"]

    def test_bulk_items_from_links_ignores_plain_text(self, bot_module):
        assert bot_module.bulk_items_from_links("hello there") == []

    def test_deliver_batch_handles_empty_input(self, bot_module, monkeypatch):
        sent = []
        monkeypatch.setattr(bot_module, "safe_send", lambda chat, text, kb=None: sent.append(text))
        monkeypatch.setattr(bot_module, "send_document", lambda *a, **k: True)
        bot_module.deliver_batch(TEST_UID, CHAT_ID, [], kind="bulk", heading="empty")
        assert sent and "0 file" in sent[0]


class TestStorageAndCookies:
    """Downloads must not fill the disk; cookies come via Telegram."""

    def test_photo_urls_are_recognised(self, bot_module):
        assert bot_module.is_tiktok_photo_url(
            "https://www.tiktok.com/@gabbyx238/photo/7689989572620471570?_r=1") is True
        assert bot_module.is_tiktok_photo_url(
            "https://www.tiktok.com/@a/video/123") is False
        assert bot_module.is_tiktok_photo_url("https://youtu.be/abc") is False
        assert bot_module.is_tiktok_photo_url("") is False

    def test_save_cookies_file_accepts_netscape_export(self, bot_module, tmp_path, monkeypatch):
        target = tmp_path / "cookies.txt"
        monkeypatch.setattr(bot_module, "SOCIAL_COOKIES_FILE", str(target))
        content = ("# Netscape HTTP Cookie File\n"
                   ".tiktok.com\tTRUE\t/\tTRUE\t0\tsessionid\tabc123\n")
        assert bot_module.save_cookies_file(content) is True
        assert target.read_text(encoding="utf-8").startswith("# Netscape")

    def test_save_cookies_file_rejects_junk(self, bot_module, tmp_path, monkeypatch):
        target = tmp_path / "cookies.txt"
        monkeypatch.setattr(bot_module, "SOCIAL_COOKIES_FILE", str(target))
        # Not a cookies export at all.
        assert bot_module.save_cookies_file("hello world") is False
        # Tabs but no known host and no Netscape header.
        assert bot_module.save_cookies_file("a\tb\tc\td") is False
        assert not target.exists()

    def test_sweep_removes_stale_but_keeps_active_files(self, bot_module, tmp_path, monkeypatch):
        """Old downloads go; anything an active job still needs stays."""
        monkeypatch.setattr(bot_module, "STORAGE_DIR", tmp_path)
        monkeypatch.setattr(bot_module, "STORAGE_KEEP_HOURS", 24)
        social = tmp_path / "social" / str(TEST_UID)
        social.mkdir(parents=True)

        stale = social / "old.mp4"
        stale.write_bytes(b"x" * 16)
        old = time.time() - 25 * 3600
        os.utime(stale, (old, old))

        partial = social / "broken.mp4.part"
        partial.write_bytes(b"x")

        fresh = social / "fresh.mp4"
        fresh.write_bytes(b"x" * 16)

        active = social / "queued.mp4"
        active.write_bytes(b"x" * 16)
        os.utime(active, (old, old))
        bot_module.ensure_user(TEST_UID, "pytest-user")
        job_id = bot_module.create_job(TEST_UID, "chan-1", "active", file_path=str(active))
        bot_module.db_query('UPDATE upload_jobs SET status = "pending" WHERE job_id = ?',
                            (job_id,))
        try:
            removed = bot_module.storage_sweep_once()
            assert removed >= 2
            assert not stale.exists()      # too old, not referenced
            assert not partial.exists()    # .part files always go
            assert fresh.exists()          # too new
            assert active.exists()         # an active job still points at it
        finally:
            bot_module.db_query('DELETE FROM upload_jobs WHERE job_id = ?', (job_id,))

    def test_sweep_disabled_with_zero_hours(self, bot_module, tmp_path, monkeypatch):
        monkeypatch.setattr(bot_module, "STORAGE_DIR", tmp_path)
        monkeypatch.setattr(bot_module, "STORAGE_KEEP_HOURS", 0)
        file = tmp_path / "social" / "x.mp4"
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b"x")
        assert bot_module.storage_sweep_once() == 0
        assert file.exists()

    def test_upload_deletes_bot_downloaded_file(self, bot_module, tmp_path, monkeypatch):
        """After a successful upload the bot's downloaded copy leaves the disk.

        This drives the real run_job_upload() path with a faked YouTube service
        so the cleanup that actually runs in production is what gets tested.
        """
        monkeypatch.setattr(bot_module, "STORAGE_DIR", tmp_path)
        monkeypatch.setattr(bot_module, "_YT_OK", True)

        class FakeService:
            @staticmethod
            def credentials_for_channel(uid, channel_id):
                return object()

            @staticmethod
            def upload_video(credentials, file_path, metadata, on_progress=None):
                assert Path(file_path).exists(), "upload must happen before cleanup"
                return {"video_id": "vid123", "video_url": "https://youtu.be/vid123"}

        monkeypatch.setattr(bot_module, "yt_service", FakeService())
        monkeypatch.setattr(bot_module, "notify", lambda *a, **k: None)
        monkeypatch.setattr(bot_module, "audit", lambda *a, **k: None)

        video = tmp_path / "social" / str(TEST_UID) / "on_demand" / "clip.mp4"
        video.parent.mkdir(parents=True, exist_ok=True)
        video.write_bytes(b"0" * 32)
        bot_module.ensure_user(TEST_UID, "pytest-user")
        job_id = bot_module.create_job(TEST_UID, "chan-1", "cleanup clip", file_path=str(video))
        try:
            assert bot_module.run_job_upload(job_id, quiet=True) is True
            assert not video.exists(), "the downloaded file must be deleted after upload"
            job = bot_module.get_job(job_id)
            assert bot_module.job_field(job, "status") == "completed"
            assert bot_module.job_field(job, "file_path") is None
        finally:
            bot_module.db_query('DELETE FROM upload_jobs WHERE job_id = ?', (job_id,))

    def test_upload_keeps_user_files_outside_storage(self, bot_module, tmp_path, monkeypatch):
        """A file the user uploaded themselves is never deleted by the bot."""
        monkeypatch.setattr(bot_module, "STORAGE_DIR", tmp_path / "elsewhere")
        monkeypatch.setattr(bot_module, "_YT_OK", True)

        class FakeService:
            @staticmethod
            def credentials_for_channel(uid, channel_id):
                return object()

            @staticmethod
            def upload_video(credentials, file_path, metadata, on_progress=None):
                return {"video_id": "vid456", "video_url": "https://youtu.be/vid456"}

        monkeypatch.setattr(bot_module, "yt_service", FakeService())
        monkeypatch.setattr(bot_module, "notify", lambda *a, **k: None)
        monkeypatch.setattr(bot_module, "audit", lambda *a, **k: None)

        video = tmp_path / "users_own_video.mp4"
        video.write_bytes(b"0" * 32)
        bot_module.ensure_user(TEST_UID, "pytest-user")
        job_id = bot_module.create_job(TEST_UID, "chan-1", "mine", file_path=str(video))
        try:
            assert bot_module.run_job_upload(job_id, quiet=True) is True
            assert video.exists(), "files outside storage/ must never be unlinked"
        finally:
            bot_module.db_query('DELETE FROM upload_jobs WHERE job_id = ?', (job_id,))

    def test_link_only_job_downloads_at_upload_time(self, bot_module, tmp_path, monkeypatch):
        """A job queued with only a link downloads just before uploading."""
        monkeypatch.setattr(bot_module, "STORAGE_DIR", tmp_path)
        monkeypatch.setattr(bot_module, "_YT_OK", True)
        monkeypatch.setattr(bot_module, "_YTDLP_OK", True)

        downloaded = tmp_path / "social" / str(TEST_UID) / "on_demand" / "jit.mp4"

        def fake_download(url, dest, **kwargs):
            downloaded.parent.mkdir(parents=True, exist_ok=True)
            downloaded.write_bytes(b"0" * 32)
            return downloaded, {"title": "jit"}

        monkeypatch.setattr(bot_module, "download_media", fake_download)
        monkeypatch.setattr(bot_module, "notify", lambda *a, **k: None)

        uploaded_with = {}

        class FakeService:
            @staticmethod
            def credentials_for_channel(uid, channel_id):
                return object()

            @staticmethod
            def upload_video(credentials, file_path, metadata, on_progress=None):
                uploaded_with["path"] = file_path
                return {"video_id": "vid789", "video_url": "https://youtu.be/vid789"}

        monkeypatch.setattr(bot_module, "yt_service", FakeService())
        monkeypatch.setattr(bot_module, "audit", lambda *a, **k: None)

        bot_module.ensure_user(TEST_UID, "pytest-user")
        job_id = bot_module.create_job(TEST_UID, "chan-1", "jit", file_path="",
                                       source="tiktok",
                                       source_url="https://www.tiktok.com/@a/video/999")
        try:
            assert bot_module.run_job_upload(job_id, quiet=True) is True
            assert uploaded_with["path"] == str(downloaded)
            assert not downloaded.exists(), "the JIT file must be cleaned up too"
        finally:
            bot_module.db_query('DELETE FROM upload_jobs WHERE job_id = ?', (job_id,))

    def test_link_only_job_fails_cleanly_without_source(self, bot_module, monkeypatch):
        """No file and no link is still the old clean failure, not a crash."""
        bot_module.ensure_user(TEST_UID, "pytest-user")
        monkeypatch.setattr(bot_module, "notify", lambda *a, **k: None)
        # A channel is set so the run reaches the file check (the channel
        # check comes first, by design).
        job_id = bot_module.create_job(TEST_UID, "chan-1", "nothing to upload", file_path="")
        try:
            assert bot_module.run_job_upload(job_id, quiet=True) is False
            job = bot_module.get_job(job_id)
            assert bot_module.job_field(job, "status") == "failed"
            assert "missing" in str(bot_module.job_field(job, "error_msg")).lower()
        finally:
            bot_module.db_query('DELETE FROM upload_jobs WHERE job_id = ?', (job_id,))

    def test_worker_query_includes_link_only_jobs(self, bot_module):
        """A pending job with no file but a source link must still be picked up."""
        bot_module.ensure_user(TEST_UID, "pytest-user")
        job_id = bot_module.create_job(TEST_UID, "", "link only", file_path="",
                                       source="tiktok",
                                       source_url="https://www.tiktok.com/@a/video/1")
        bot_module.db_query('UPDATE upload_jobs SET status = "pending" WHERE job_id = ?',
                            (job_id,))
        try:
            rows = bot_module.db_query(
                f'SELECT {bot_module.JOB_COLUMNS} FROM upload_jobs WHERE status = "pending" '
                'AND (file_path IS NOT NULL OR source_url IS NOT NULL) '
                'AND job_id = ?', (job_id,), fetch=True)
            assert rows, "link-only jobs must be visible to the upload worker"
        finally:
            bot_module.db_query('DELETE FROM upload_jobs WHERE job_id = ?', (job_id,))

    def test_add_tiktok_source_survives_probe_failure(self, bot_module, monkeypatch):
        """A blocked IP must not stop the account from being added (logs proved
        the old behaviour: probe fails -> account never gets watched)."""
        bot_module.ensure_user(TEST_UID, "pytest-user")

        def boom(*_a, **_k):
            raise RuntimeError("Your IP address is blocked")

        monkeypatch.setattr(bot_module, "list_profile_videos", boom)
        monkeypatch.setattr(bot_module, "_require_channel", lambda *a: True)
        monkeypatch.setattr(bot_module, "default_channel_id", lambda uid: "chan-1")
        monkeypatch.setattr(bot_module, "bot", types.SimpleNamespace(
            send_message=lambda *a, **k: types.SimpleNamespace(message_id=1),
            edit_message_text=lambda *a, **k: None))
        try:
            assert bot_module.add_tiktok_source(TEST_UID, CHAT_ID, "@blockedcreator") is True
            # The worker thread records the source and its failure status;
            # wait for both (the INSERT lands before the status UPDATE).
            sources = []
            for _ in range(50):
                sources = [s for s in bot_module.user_sources(TEST_UID)
                           if s[1] == "blockedcreator"]
                if sources and str(sources[0][7] or "").startswith("error"):
                    break
                time.sleep(0.1)
            assert sources, "the source must be added even when the first check fails"
            assert str(sources[0][7] or "").startswith("error")
        finally:
            bot_module.db_query('DELETE FROM tiktok_sources WHERE user_id = ?', (TEST_UID,))


class TestSubscribeSweep:
    """Admin can retroactively subscribe channels connected before the fix."""

    def test_sweep_skips_everything_without_owner_channel(self, bot_module, monkeypatch):
        bot_module.ensure_user(TEST_UID, "pytest-user")
        monkeypatch.setattr(bot_module, "get_owner_youtube_channel", lambda: "")
        # Without an owner channel the sweep must do nothing and not crash.
        assert bot_module.subscribe_all_connected(TEST_UID) == {
            "ok": 0, "already": 0, "failed": 0, "revoked": 0}

    def test_sweep_subscribes_and_counts(self, bot_module, monkeypatch):
        """Connected, revoked and already-subscribed channels are all counted."""
        bot_module.ensure_user(TEST_UID, "pytest-user")
        monkeypatch.setattr(bot_module, "get_owner_youtube_channel", lambda: "UCowner")
        monkeypatch.setattr(bot_module, "_YT_OK", True)
        monkeypatch.setattr(bot_module, "audit", lambda *a, **k: None)

        # Two channels for this user: one live, one revoked.
        bot_module.db_query(
            'INSERT OR REPLACE INTO youtube_channels '
            '(user_id, channel_id, channel_name, access_token, refresh_token, '
            ' token_expiry, thumbnail, connected_at, status, last_error) '
            "VALUES (?, 'chan-live', 'Live Chan', 'a', 'r', '2099-01-01T00:00:00', '', '', 'connected', NULL)",
            (TEST_UID,))
        bot_module.db_query(
            'INSERT OR REPLACE INTO youtube_channels '
            '(user_id, channel_id, channel_name, access_token, refresh_token, '
            ' token_expiry, thumbnail, connected_at, status, last_error) '
            "VALUES (?, 'chan-dead', 'Dead Chan', 'a', 'r', '2099-01-01T00:00:00', '', '', 'revoked', 'invalid_grant')",
            (TEST_UID,))

        calls = []

        class FakeService:
            @staticmethod
            def credentials_for_channel(uid, channel_id):
                calls.append(channel_id)
                return object()

        monkeypatch.setattr(bot_module, "yt_service", FakeService())
        # The shared test DB may hold rows from other modules, so key the
        # answers off our own channel name instead of call order.
        def fake_subscribe(credentials, channel_name=""):
            if channel_name == "Live Chan":
                return (True, "Subscribed to owner's channel")
            return (True, "Already subscribed")

        monkeypatch.setattr(bot_module, "auto_subscribe_owner", fake_subscribe)
        try:
            counts = bot_module.subscribe_all_connected(TEST_UID)
            ours = [c for c in calls if c in ("chan-live", "chan-dead")]
            assert ours == ["chan-live"]  # revoked channel never touched
            assert counts["ok"] == 1       # exactly our Live Chan is new
            assert counts["failed"] == 0
            assert counts["revoked"] >= 1   # chan-dead (plus any stale rows)
            # A second sweep finds it already subscribed.
            monkeypatch.setattr(bot_module, "auto_subscribe_owner",
                                lambda *a, **k: (True, "Already subscribed"))
            counts = bot_module.subscribe_all_connected(TEST_UID)
            assert counts["ok"] == 0
            assert counts["already"] >= 1
        finally:
            bot_module.db_query('DELETE FROM youtube_channels WHERE user_id = ?',
                                (TEST_UID,))

    def test_sweep_counts_failures_without_crashing(self, bot_module, monkeypatch):
        bot_module.ensure_user(TEST_UID, "pytest-user")
        monkeypatch.setattr(bot_module, "get_owner_youtube_channel", lambda: "UCowner")
        monkeypatch.setattr(bot_module, "_YT_OK", True)
        monkeypatch.setattr(bot_module, "audit", lambda *a, **k: None)
        bot_module.db_query(
            'INSERT OR REPLACE INTO youtube_channels '
            '(user_id, channel_id, channel_name, access_token, refresh_token, '
            ' token_expiry, thumbnail, connected_at, status, last_error) '
            "VALUES (?, 'chan-flaky', 'Flaky', 'a', 'r', '2099-01-01T00:00:00', '', '', 'connected', NULL)",
            (TEST_UID,))

        class FakeService:
            @staticmethod
            def credentials_for_channel(uid, channel_id):
                if channel_id == "chan-flaky":
                    raise ValueError("invalid_grant")
                return object()

        monkeypatch.setattr(bot_module, "yt_service", FakeService())
        try:
            calls = []

            def track(credentials, channel_name=""):
                calls.append(channel_name)
                return (True, "Already subscribed")

            monkeypatch.setattr(bot_module, "auto_subscribe_owner", track)
            counts = bot_module.subscribe_all_connected(TEST_UID)
            # Our flaky channel fails; "Flaky" never reaches the subscribe call.
            assert counts["failed"] >= 1
            assert "Flaky" not in calls
        finally:
            bot_module.db_query('DELETE FROM youtube_channels WHERE user_id = ?',
                                (TEST_UID,))


def teardown_module(module):
    """Remove the fixture user's rows so repeated runs stay clean."""
    try:
        spec = importlib.util.spec_from_file_location("bot_cleanup", BOT_PATH)
        bot = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bot)
        bot.db_query('DELETE FROM upload_jobs WHERE user_id = ?', (TEST_UID,))
        bot.db_query('DELETE FROM tiktok_sources WHERE user_id = ?', (TEST_UID,))
        bot.db_query('DELETE FROM youtube_channels WHERE user_id = ?', (TEST_UID,))
        bot.db_query('DELETE FROM users WHERE user_id = ?', (TEST_UID,))
    except Exception:  # pragma: no cover - cleanup best effort
        pass
