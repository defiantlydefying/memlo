"""Offline Stage 0 checks: no Discord connection and no Anthropic calls required."""

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

os.environ["ANTHROPIC_API_KEY"] = os.environ.get("ANTHROPIC_API_KEY") or "offline-test"

import bot


class StageZeroTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.original_db_path = bot.DB_PATH
        self.original_backup_dir = bot.BACKUP_DIR
        bot.DB_PATH = str(Path(self.tempdir.name) / "memlo.db")
        bot.BACKUP_DIR = Path(self.tempdir.name) / "backups"
        bot.init_db()

    def tearDown(self):
        bot.DB_PATH = self.original_db_path
        bot.BACKUP_DIR = self.original_backup_dir
        self.tempdir.cleanup()

    def test_new_server_tracks_nothing_until_setup(self):
        settings = bot.ensure_guild_settings(1001)
        self.assertEqual(settings["setup_status"], "pending")
        self.assertFalse(bot.is_tracked(1001, 2001, 3001))

        bot.configure_guild(1001, "America/Chicago", 9001)
        bot.activate_category(2001, 1001, "AP Biology", "Admin", 9001)
        self.assertTrue(bot.is_tracked(1001, 2001, 3001))
        self.assertFalse(bot.is_tracked(1001, 2002, 3001))
        configured = bot.get_guild_settings(1001)
        self.assertEqual(configured["timezone"], "America/Chicago")
        self.assertEqual(configured["installer_id"], 9001)

    def test_explicit_channel_scope(self):
        bot.configure_guild(1001, "Europe/London", 9001)
        bot.activate_channel(3001, 1001, "chemistry", 9001)
        self.assertTrue(bot.is_tracked(1001, None, 3001))
        self.assertFalse(bot.is_tracked(1001, None, 3002))

    def test_deletion_uses_discord_user_id_not_display_name(self):
        bot.configure_guild(1001, "America/Chicago", 9001)
        for user_id in (7001, 7002):
            bot.save_deadline(
                "Shared-name test", "2026-08-20", "Alex", 3001, "link",
                guild_id=1001, author_id=user_id)
            bot.save_note(
                "Note", "original", "Alex", 3001, "link",
                guild_id=1001, author_id=user_id)
        counts = bot.count_user_data_by_id(7001, 1001)
        self.assertEqual(counts["deadlines"], 1)
        self.assertEqual(counts["notes"], 1)
        bot.forget_user_by_id(7001, 1001)
        self.assertEqual(sum(bot.count_user_data_by_id(7001, 1001).values()), 0)
        self.assertEqual(bot.count_user_data_by_id(7002, 1001)["deadlines"], 1)

    def test_telemetry_rejects_content_and_supports_first_event(self):
        self.assertTrue(bot.record_event(
            "first_answer", 1001,
            {"source": "mention", "message_text": "private class message"},
            once=True))
        self.assertFalse(bot.record_event(
            "first_answer", 1001, {"source": "second"}, once=True))
        conn = bot.db()
        row = conn.execute(
            "SELECT properties_json FROM analytics_events WHERE guild_id = 1001"
        ).fetchone()
        conn.close()
        self.assertEqual(row["properties_json"], '{"source": "mention"}')

    def test_prefilter(self):
        self.assertTrue(bot.should_analyze_message("science test tmr"))
        self.assertTrue(bot.should_analyze_message("bring a calculator on Friday"))
        self.assertTrue(bot.should_analyze_message("Important update", ["Teacher"]))
        self.assertFalse(bot.should_analyze_message("lol that was wild"))
        self.assertFalse(bot.should_analyze_message("anyone gaming tonight?"))

    def test_rate_limiter(self):
        limiter = bot.RateLimiter()
        old = bot.RATE_LIMITS["answer"]
        bot.RATE_LIMITS["answer"] = (2, 60)
        try:
            self.assertTrue(limiter.allow("answer", 1, 2, now=100))
            self.assertTrue(limiter.allow("answer", 1, 2, now=101))
            self.assertFalse(limiter.allow("answer", 1, 2, now=102))
            self.assertTrue(limiter.allow("answer", 1, 2, now=161))
        finally:
            bot.RATE_LIMITS["answer"] = old

    def test_backup_is_valid_sqlite(self):
        bot.ensure_guild_settings(1001)
        path = bot.backup_database()
        self.assertTrue(path.exists())
        conn = sqlite3.connect(path)
        check = conn.execute("PRAGMA integrity_check").fetchone()[0]
        conn.close()
        self.assertEqual(check, "ok")

    def test_server_deletion_resets_to_pending(self):
        bot.configure_guild(1001, "America/Chicago", 9001)
        bot.activate_channel(3001, 1001, "history", 9001)
        bot.save_note(
            "Note", "original", "Student", 3001, "link",
            guild_id=1001, author_id=7001)
        counts = bot.delete_server_data(1001)
        self.assertEqual(counts["notes"], 1)
        self.assertEqual(bot.get_guild_settings(1001)["setup_status"], "pending")
        self.assertFalse(bot.is_tracked(1001, None, 3001))


if __name__ == "__main__":
    unittest.main()
