"""Storage-persistence tests for bot.py.

A redeploy replaces the container, not its volumes. When ``STORAGE_DIR`` is not
on an attached volume, ``bot_data.db`` and ``secret.key`` die *together*, which
is why "the bot lost my YouTube channel" looks identical to "the login never
worked":

* the rows are gone -> the health page reports ``channels=0``;
* even if the database survived, a regenerated ``secret.key`` cannot decrypt
  the stored OAuth tokens, so every channel reads as unusable.

These tests pin the detection logic and prove a connected channel survives a
restart when both files share a persistent directory.
"""

import base64
import contextlib
import importlib.util
import os
import sqlite3
import sys
from pathlib import Path

import pytest

BOT_PATH = Path(__file__).resolve().parent.parent / "bot.py"
USER = 424242


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

    spec = importlib.util.spec_from_file_location("bot_storage_test", BOT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["bot_storage_test"] = module
    spec.loader.exec_module(module)

    module.DB_FILE = db_path
    module.init_db()
    return module


@pytest.fixture(scope="module")
def bot(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("storagedb") / "bot_data.db"
    return _load_bot(db_path)


def _mounts(tmp_path, *mounts) -> str:
    """A fake /proc/mounts: the usual container root, plus `mounts`."""
    rows = ["overlay / overlay rw,relatime 0 0"]
    rows += [f"/dev/vdb {m} ext4 rw,relatime 0 0" for m in mounts]
    path = tmp_path / "mounts"
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return str(path)


# ── detecting whether storage is persistent ──────────────────────────

class TestVolumeDetection:
    def test_directory_on_a_mounted_volume_is_persistent(self, bot, tmp_path):
        mount = tmp_path / "data"
        mount.mkdir()
        assert bot.storage_volume_name(mount, _mounts(tmp_path, mount)) == str(mount)

    def test_subdirectory_of_a_volume_is_persistent(self, bot, tmp_path):
        mount = tmp_path / "data"
        (mount / "bot").mkdir(parents=True)
        # STORAGE_DIR=/data/bot is a valid choice - it is still on the volume.
        assert bot.storage_volume_name(mount / "bot", _mounts(tmp_path, mount)) == str(mount)

    def test_root_filesystem_is_never_counted_as_a_volume(self, bot, tmp_path):
        """Every container has / mounted, so treating / as persistence would
        make the ephemeral warning unreachable."""
        store = tmp_path / "storage"
        store.mkdir()
        assert bot.storage_volume_name(store, _mounts(tmp_path)) == ""

    def test_storage_beside_the_volume_is_not_persistent(self, bot, tmp_path):
        """The classic misconfiguration: a volume exists, but STORAGE_DIR points
        somewhere else, so the database still lands on the wiped filesystem."""
        mount = tmp_path / "data"
        mount.mkdir()
        other = tmp_path / "app" / "storage"
        other.mkdir(parents=True)
        assert bot.storage_volume_name(other, _mounts(tmp_path, mount)) == ""

    def test_unset_volume_env_is_not_treated_as_the_current_directory(self, bot, tmp_path,
                                                                    monkeypatch):
        """Path("") silently becomes ".", which made every local run look like it
        had a volume mounted at the working directory."""
        monkeypatch.delenv("RAILWAY_VOLUME_MOUNT_PATH", raising=False)
        store = tmp_path / "storage"
        store.mkdir()
        assert bot.storage_volume_name(store, _mounts(tmp_path)) == ""

    def test_unreadable_mounts_file_is_treated_as_ephemeral(self, bot, tmp_path):
        store = tmp_path / "storage"
        store.mkdir()
        assert bot.storage_volume_name(store, str(tmp_path / "missing")) == ""

    def test_railway_volume_env_is_honoured_without_proc_mounts(self, bot, tmp_path,
                                                               monkeypatch):
        monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", str(tmp_path))
        monkeypatch.setenv("RAILWAY_VOLUME_NAME", "bot-data")
        assert bot.storage_volume_name(tmp_path) == "bot-data"

    def test_mount_path_below_storage_dir_does_not_count(self, bot, tmp_path, monkeypatch):
        """A volume mounted *inside* storage (e.g. /app/storage/zips) does not
        make the database itself persistent."""
        store = tmp_path / "storage"
        store.mkdir()
        monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", str(store / "zips"))
        assert bot.storage_volume_name(store, _mounts(tmp_path)) == ""


# ── what the startup log says ────────────────────────────────────────

class TestStorageReport:
    def test_persistent_storage_is_reported(self, bot, tmp_path):
        mount = tmp_path / "data"
        mount.mkdir()
        report = " ".join(bot.storage_report_lines(mount, _mounts(tmp_path, mount)))
        assert "Persistent volume" in report
        assert "survives redeploys" in report
        assert "NOT persistent" not in report

    def test_ephemeral_storage_names_the_damage(self, bot, tmp_path, monkeypatch):
        store = tmp_path / "storage"
        store.mkdir()
        monkeypatch.delenv("RAILWAY_VOLUME_MOUNT_PATH", raising=False)
        monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
        report = " ".join(bot.storage_report_lines(store, _mounts(tmp_path)))
        assert "NOT persistent" in report
        assert "Railway" in report
        assert "channels=0" in report          # the symptom seen in production
        assert "secret.key" in report          # ...and the token half of it

    def test_local_run_does_not_pretend_a_redeploy_will_happen(self, bot, tmp_path,
                                                              monkeypatch):
        store = tmp_path / "storage"
        store.mkdir()
        monkeypatch.delenv("RAILWAY_VOLUME_MOUNT_PATH", raising=False)
        monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
        monkeypatch.delenv("RAILWAY_PROJECT_ID", raising=False)
        monkeypatch.delenv("RENDER", raising=False)
        monkeypatch.delenv("DYNO", raising=False)
        report = " ".join(bot.storage_report_lines(store, _mounts(tmp_path)))
        assert "NOT persistent" in report
        assert "Local run" in report
        assert "Railway wipes" not in report

    def test_attached_but_unused_volume_recommends_the_exact_value(self, bot, tmp_path,
                                                                  monkeypatch):
        store = tmp_path / "storage"
        store.mkdir()
        monkeypatch.setenv("RAILWAY_VOLUME_MOUNT_PATH", "/data")
        report = " ".join(bot.storage_report_lines(store, _mounts(tmp_path)))
        assert "A volume IS mounted at /data" in report
        assert "STORAGE_DIR=/data" in report


# ── surviving an actual restart ──────────────────────────────────────

class TestPersistenceAcrossRestart:
    def test_database_and_encryption_key_hang_off_one_storage_dir(self, bot):
        """They have to move together: a key on ephemeral storage alongside a
        database that somehow survived leaves rows that can never be read, and
        that is only guaranteed while both derive from STORAGE_DIR."""
        source = BOT_PATH.read_text(encoding="utf-8")
        assert 'DB_FILE = STORAGE_DIR / "bot_data.db"' in source
        assert '_SECRET_FILE = STORAGE_DIR / "secret.key"' in source
        # ...and STORAGE_DIR follows the volume Railway attaches for us.
        assert 'os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")' in source

    def test_connected_channel_and_its_token_survive_a_restart(self, bot, tmp_path,
                                                               monkeypatch):
        store = tmp_path / "volume"
        store.mkdir()
        monkeypatch.setattr(bot, "DB_FILE", store / "bot_data.db")
        monkeypatch.setattr(bot, "_SECRET_FILE", store / "secret.key")
        monkeypatch.delenv("TOKEN_ENCRYPTION_KEY", raising=False)

        # Process 1: a fresh volume, so the bot generates secret.key there - that
        # is exactly what makes stored tokens readable again after a redeploy.
        key = bot._master_key()
        monkeypatch.setattr(bot, "_FERNET", bot.Fernet(base64.urlsafe_b64encode(key)))
        bot.init_db()
        bot.ensure_user(USER, "keeper")
        bot.save_channel_tokens(
            USER,
            {"access_token": "ya29.after-restart", "refresh_token": "1//refresh-me",
             "expires_in": 3600},
            {"channel_id": "UC_keep", "channel_name": "Kept Channel", "thumbnail": ""})

        assert (store / "bot_data.db").is_file()
        assert (store / "secret.key").is_file()

        # Process 2: a brand new container that only has the volume's contents.
        # Nothing is shared with process 1 except these two files.
        rebooted_key = bot._master_key()
        rebooted = bot.Fernet(base64.urlsafe_b64encode(rebooted_key))
        with contextlib.closing(sqlite3.connect(store / "bot_data.db")) as conn:
            rows = conn.execute(
                "SELECT user_id, channel_id, refresh_token FROM youtube_channels"
            ).fetchall()

        assert [(row[0], row[1]) for row in rows] == [(USER, "UC_keep")]
        assert rebooted.decrypt(rows[0][2].encode()).decode() == "1//refresh-me"
