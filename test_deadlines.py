"""Deadline lifecycle regressions; temporary SQLite, mocked classifier, no API calls."""

import os
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

os.environ["ANTHROPIC_API_KEY"] = os.environ.get("ANTHROPIC_API_KEY") or "offline-test"

import bot
import deadline_store as storage
from deadline_controls import CancelDeadlineView
from deadline_events import DeadlineEvents
from test_functionality import fake_message, model_response


class DeadlineCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        for name, value in (
            ("DB_PATH", str(Path(self.directory.name) / "deadlines.db")),
            ("class_now", lambda guild_id=None: datetime(2026, 10, 24, 10)),
            ("class_today", lambda guild_id=None: date(2026, 10, 24)),
        ):
            patcher = patch.object(bot, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        original_version_time = storage.version_time
        patcher = patch.object(storage, "version_time",
            side_effect=lambda value=None: original_version_time(value or "2026-10-24T14:00:00+00:00"))
        patcher.start()
        self.addCleanup(patcher.stop)
        bot.init_db()
        bot.configure_guild(1001, "America/Chicago", 1)
        bot.activate_channel(3001, 1001, "chemistry", 1)
        self.store = bot.deadline_store()

    def save(self, message_id=101, author_id=7001, *, summary="Chemistry quiz",
             due="2026-10-25", audience="", version="2026-10-24T12:00:00+00:00",
             channel=3001, guild=1001, **options):
        return self.store.save(summary, due, "Alex", channel,
                               f"https://discord.com/channels/{guild}/{channel}/{message_id}",
                               audience, guild, author_id, source_message_id=message_id,
                               source_version=version, **options)

    def rows(self):
        return self.store.upcoming(3001, 1001)

    def total(self):
        conn = bot.db()
        try:
            return conn.execute("SELECT COUNT(*) FROM deadlines").fetchone()[0]
        finally:
            conn.close()


class DeadlineStorageTests(DeadlineCase):
    def test_replayed_message_is_idempotent(self):
        self.save()
        self.assertEqual(self.save().action, "stale")
        self.assertEqual(self.total(), 1)

    def test_same_source_edit_updates_existing_row(self):
        original = self.save()
        changed = self.save(due="2026-10-27", version="2026-10-24T13:00:00+00:00")
        self.assertEqual(changed.action, "updated")
        self.assertEqual(changed.deadline_id, original.deadline_id)
        self.assertEqual(self.total(), 1)
        self.assertEqual(self.rows()[0]["due_date"], "2026-10-27")

    def test_exact_reposts_collapse_but_keep_authors(self):
        self.save()
        result = self.save(102, 7002, summary="CHEMISTRY QUIZ!", version="2026-10-24T12:30:00+00:00")
        self.assertEqual(result.action, "duplicate")
        self.assertEqual(self.total(), 2)
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(bot.count_user_data_by_id(7001, 1001)["deadlines"], 1)
        self.assertEqual(bot.count_user_data_by_id(7002, 1001)["deadlines"], 1)

    def test_different_audiences_dates_and_channels_stay_separate(self):
        self.save(audience="7th Grade")
        self.save(102, audience="8th Grade")
        self.save(103, due="2026-10-26", audience="7th Grade")
        self.save(104, channel=3002, audience="7th Grade")
        self.assertEqual(len(self.rows()), 3)

    def test_deleting_one_contributor_keeps_others_copy(self):
        self.save()
        self.save(102, 7002)
        bot.forget_user_by_id(7001, 1001)
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.rows()[0]["author_id"], 7002)

    def test_edited_date_moves_exact_copies_and_resets_reminders(self):
        first = self.save()
        self.save(102, 7002)
        self.store.mark_reminded(first.deadline_id)
        self.save(due="2026-10-27", version="2026-10-24T13:00:00+00:00")
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["due_date"], "2026-10-27")
        self.assertEqual(rows[0]["group_reminded"], 0)

    def test_original_source_can_correct_title_without_leaving_old_copies(self):
        self.save()
        self.save(102, 7002)
        self.save(summary="Chemistry exam", due="2026-10-27", version="2026-10-24T13:00:00+00:00")
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.rows()[0]["summary"], "Chemistry exam")

    def test_repost_replaced_with_unrelated_task_does_not_rename_original(self):
        self.save()
        self.save(102, 7002)
        self.save(102, 7002, summary="History essay", due="2026-10-27",
                  version="2026-10-24T13:00:00+00:00")
        self.assertEqual({r["summary"] for r in self.rows()}, {"Chemistry quiz", "History essay"})

    def test_restoring_removed_source_resets_reminder(self):
        original = self.save()
        self.store.mark_reminded(original.deadline_id)
        self.store.remove_source(1001, 3001, 101, source_version="2026-10-24T12:30:00+00:00")
        self.save(version="2026-10-24T13:00:00+00:00")
        self.assertEqual(self.rows()[0]["group_reminded"], 0)

    def test_newer_group_correction_cannot_be_undone_by_older_source_edit(self):
        self.save()
        self.save(102, 7002, version="2026-10-24T13:00:00+00:00")
        result = self.save(due="2026-10-27", version="2026-10-24T12:30:00+00:00")
        self.assertEqual(result.action, "stale")
        self.assertEqual(self.rows()[0]["due_date"], "2026-10-25")

    def test_separate_clear_correction_updates_one_assignment(self):
        self.save()
        changed = self.save(102, due="2026-10-28", correction=True,
                            version="2026-10-24T13:00:00+00:00")
        self.assertEqual(changed.action, "rescheduled")
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.rows()[0]["due_date"], "2026-10-28")

    def test_ambiguous_correction_makes_no_changes(self):
        self.save()
        self.save(102, due="2026-10-26")
        changed = self.save(103, due="2026-10-28", correction=True,
                            version="2026-10-24T13:00:00+00:00")
        self.assertEqual(changed.action, "needs_choice")
        self.assertEqual(self.total(), 2)
        self.assertEqual({row["due_date"] for row in self.rows()}, {"2026-10-25", "2026-10-26"})

    def test_reply_disambiguates_correction_and_preserves_title(self):
        self.save(summary="History essay")
        self.save(102, summary="Science essay")
        result = self.save(103, summary="Essay", due="2026-10-28", correction=True,
                           target_message_id=101, version="2026-10-24T13:00:00+00:00")
        self.assertEqual(result.action, "rescheduled")
        self.assertEqual({(r["summary"], r["due_date"]) for r in self.rows()},
                         {("History essay", "2026-10-28"), ("Science essay", "2026-10-25")})

    def test_noncontributor_cannot_rewrite_or_cancel(self):
        first = self.save()
        result = self.save(102, 7002, due="2026-10-28", correction=True,
                           version="2026-10-24T13:00:00+00:00")
        self.assertEqual(result.action, "forbidden")
        with self.assertRaises(storage.DeadlinePermissionError):
            self.store.change(first.deadline_id, 1001, 3001, 7002)
        self.assertEqual(self.rows()[0]["due_date"], "2026-10-25")

    def test_reposting_cannot_take_ownership_of_shared_assignment(self):
        self.save()
        repost = self.save(102, 7002)
        with self.assertRaises(storage.DeadlinePermissionError):
            self.store.change(repost.deadline_id, 1001, 3001, 7002, new_date="2026-10-27")
        edited = self.save(102, 7002, due="2026-10-27", version="2026-10-24T13:00:00+00:00")
        self.assertEqual(edited.action, "replaced")
        self.assertEqual(self.store.source(1001, 3001, 101)["due_date"], "2026-10-25")

    def test_same_date_correction_does_not_reset_reminder(self):
        original = self.save()
        self.store.mark_reminded(original.deadline_id)
        self.save(102, correction=True, version="2026-10-24T13:00:00+00:00")
        self.assertEqual(self.rows()[0]["group_reminded"], 1)

    def test_admin_can_reschedule_and_unchanged_date_is_noop(self):
        first = self.save()
        self.store.change(first.deadline_id, 1001, 3001, 9, admin=True, new_date="2026-10-27")
        revision = self.rows()[0]["revision"]
        result = self.store.change(first.deadline_id, 1001, 3001, 9, admin=True, new_date="2026-10-27")
        self.assertEqual(result.action, "unchanged")
        self.assertEqual(self.rows()[0]["revision"], revision)

    def test_stale_replay_after_manual_reschedule_is_ignored(self):
        first = self.save()
        self.store.change(first.deadline_id, 1001, 3001, 7001, new_date="2026-10-27")
        result = self.save(version="2026-10-24T13:00:00+00:00")
        self.assertEqual(result.action, "stale")
        self.assertEqual(self.rows()[0]["due_date"], "2026-10-27")

    def test_cancel_hides_all_exact_copies_and_retains_attribution(self):
        first = self.save()
        self.save(102, 7002)
        self.store.change(first.deadline_id, 1001, 3001, 7001)
        self.assertFalse(self.rows())
        self.assertFalse(self.store.due_soon(1001, quiet_hours=0))
        self.assertEqual(self.total(), 2)

    def test_cross_server_or_channel_id_does_not_authorize_update(self):
        first = self.save()
        for guild, channel in ((1002, 3001), (1001, 3002)):
            with self.assertRaises(storage.DeadlineNotFound):
                self.store.change(first.deadline_id, guild, channel, 7001, admin=True)

    def test_bad_dates_never_enter_storage(self):
        for due in ("2026-02-30", "tomorrow", "2026-1-2", "", None):
            with self.assertRaises(ValueError):
                self.save(due=due)
        self.assertEqual(self.total(), 0)

    def test_old_reminder_revision_cannot_mark_new_date_reminded(self):
        first = self.save()
        old = self.rows()[0]
        self.store.change(first.deadline_id, 1001, 3001, 7001, new_date="2026-10-27")
        self.assertFalse(self.store.mark_reminded(old["id"], old["revision"]))
        self.assertEqual(self.rows()[0]["group_reminded"], 0)

    def test_duplicate_after_reminder_does_not_trigger_another(self):
        first = self.save()
        self.store.mark_reminded(first.deadline_id)
        self.save(102, 7002, version="2026-10-24T13:00:00+00:00")
        self.assertEqual(self.rows()[0]["group_reminded"], 1)
        self.assertFalse(self.store.due_soon(1001, quiet_hours=0))

    def test_removing_one_source_does_not_hide_remaining_copy(self):
        self.save()
        self.save(102, 7002)
        self.store.remove_source(1001, 3001, 101)
        self.assertEqual(len(self.rows()), 1)
        self.store.remove_source(1001, 3001, 102)
        self.assertFalse(self.rows())

    def test_migration_is_idempotent_and_preserves_rows(self):
        self.save()
        self.save(102, 7002)
        bot.init_db()
        bot.init_db()
        self.assertEqual(self.total(), 2)
        self.assertEqual(len(self.rows()), 1)

    def test_migration_repairs_only_matching_webhook_source_links(self):
        self.save()
        self.save(102, 7002, summary="Other assignment")
        conn = bot.db()
        conn.execute("UPDATE deadlines SET link = ? WHERE source_message_id = 101",
                     ("https://discord.com/channels/@me/3001/101",))
        conn.execute("UPDATE deadlines SET link = ? WHERE source_message_id = 102",
                     ("https://discord.com/channels/@me/9999/102",))
        conn.commit()
        conn.close()
        bot.init_db()
        bot.init_db()
        links = {row["source_message_id"]: row["link"] for row in self.rows()}
        self.assertEqual(links[101], "https://discord.com/channels/1001/3001/101")
        self.assertEqual(links[102], "https://discord.com/channels/@me/9999/102")
        self.assertEqual(self.total(), 2)


class DeadlineEventTests(DeadlineCase, unittest.IsolatedAsyncioTestCase):
    def message(self, text="Chemistry quiz due tomorrow", message_id=101, edited_hour=None):
        message = fake_message(text, addressed=False)
        message.id = message_id
        message.jump_url = f"https://discord.com/channels/1001/3001/{message_id}"
        message.created_at = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
        message.edited_at = datetime(2026, 10, 24, edited_hour, tzinfo=timezone.utc) if edited_hour else None
        return message

    def result(self, due="2026-10-25", **extra):
        return dict(is_important=True, type="deadline", summary="Chemistry quiz",
                    due_date=due, audience="", poster_grade="", **extra)

    async def test_uncached_edit_fetches_latest_message_and_updates_date(self):
        self.save()
        message = self.message(edited_hour=13)
        message.channel.fetch_message = AsyncMock(return_value=message)
        payload = NS(guild_id=1001, channel_id=3001, message_id=101, data={"content": "new text"})
        events = DeadlineEvents(bot)
        with patch.object(bot.bot, "get_channel", return_value=message.channel), \
                patch.object(bot, "analyze_message", return_value=self.result("2026-10-27")):
            await events.edited(payload)
        message.channel.fetch_message.assert_awaited_once_with(101)
        self.assertEqual(self.rows()[0]["due_date"], "2026-10-27")

    async def test_edit_to_unimportant_text_removes_saved_deadline(self):
        self.save()
        with patch.object(bot, "analyze_message", return_value={"is_important": False}):
            await DeadlineEvents(bot).remember(self.message("never mind", edited_hour=13), edited=True)
        self.assertFalse(self.rows())

    async def test_failed_classifier_cannot_erase_deadline(self):
        self.save()
        with patch.object(bot, "analyze_message", return_value={"classification_failed": True}):
            await DeadlineEvents(bot).remember(self.message(edited_hour=13), edited=True)
        self.assertEqual(self.rows()[0]["due_date"], "2026-10-25")

    async def test_transition_between_note_and_deadline_leaves_no_stale_copy(self):
        events = DeadlineEvents(bot)
        message = self.message()
        note = dict(is_important=True, type="announcement", summary="Bring calculator", due_date="")
        with patch.object(bot, "analyze_message", return_value=note):
            await events.remember(message)
        self.assertEqual(len(bot.get_notes(3001)), 1)
        message.edited_at = datetime(2026, 10, 24, 13, tzinfo=timezone.utc)
        with patch.object(bot, "analyze_message", return_value=self.result()):
            await events.remember(message, edited=True)
        self.assertFalse(bot.get_notes(3001))
        self.assertEqual(len(self.rows()), 1)
        message.edited_at = datetime(2026, 10, 24, 14, tzinfo=timezone.utc)
        with patch.object(bot, "analyze_message", return_value=note):
            await events.remember(message, edited=True)
        self.assertFalse(self.rows())
        self.assertEqual(len(bot.get_notes(3001)), 1)

    async def test_old_edit_is_ignored_before_calling_classifier(self):
        self.save(version="2026-10-24T14:00:00+00:00")
        with patch.object(bot, "analyze_message") as classify:
            await DeadlineEvents(bot).remember(self.message(edited_hour=13), edited=True)
        classify.assert_not_called()

    async def test_preview_only_edits_are_ignored(self):
        with patch.object(bot.bot, "get_channel") as channel:
            await DeadlineEvents(bot).edited(NS(guild_id=1001, data={"embeds": []}))
        channel.assert_not_called()

    async def test_untracked_edits_do_not_call_classifier(self):
        self.save()
        bot.deactivate_channel(3001)
        with patch.object(bot, "analyze_message") as classify:
            await DeadlineEvents(bot).remember(self.message(edited_hour=13), edited=True)
        classify.assert_not_called()
        self.assertEqual(len(self.rows()), 1)

    async def test_deleted_and_bulk_deleted_sources_leave_upcoming_memory(self):
        self.save()
        self.save(102, 7002)
        events = DeadlineEvents(bot)
        await events.deleted(NS(guild_id=1001, channel_id=3001, message_id=101))
        self.assertEqual(len(self.rows()), 1)
        await events.deleted(NS(guild_id=1001, channel_id=3001, message_ids={102}))
        self.assertFalse(self.rows())

    async def test_ambiguous_correction_gives_actionable_prompt(self):
        self.save()
        self.save(102, due="2026-10-26")
        message = self.message("chemistry quiz moved to Monday", 103, edited_hour=13)
        with patch.object(bot, "analyze_message", return_value=self.result("2026-10-28", is_correction=True)):
            await DeadlineEvents(bot).remember(message)
        self.assertIn("/reschedule", message.reply.call_args.args[0])
        self.assertEqual(self.total(), 2)

    async def test_reply_correction_without_assignment_keyword_is_not_filtered_out(self):
        self.save()
        message = self.message("it moved to Monday", 103, edited_hour=13)
        message.reference = NS(message_id=101, resolved=None)
        with patch.object(bot, "analyze_message", return_value=self.result("2026-10-28", is_correction=True)) as classify:
            await DeadlineEvents(bot).remember(message)
        classify.assert_called_once()
        self.assertEqual(self.rows()[0]["due_date"], "2026-10-28")

    async def test_cancel_confirmation_cannot_cancel_rescheduled_assignment(self):
        first = self.save()
        row = self.rows()[0]
        view = CancelDeadlineView(bot.deadline_store, lambda _: False, row, 7001)
        self.store.change(first.deadline_id, 1001, 3001, 7001, new_date="2026-10-27")
        interaction = NS(guild_id=1001, channel_id=3001, user=NS(id=7001),
                         response=NS(defer=AsyncMock()), edit_original_response=AsyncMock())
        await view.confirm.callback(interaction)
        self.assertIn("Nothing was cancelled", interaction.edit_original_response.call_args.kwargs["content"])
        self.assertEqual(self.rows()[0]["due_date"], "2026-10-27")

    async def test_reschedule_command_handles_bad_dates_and_permissions(self):
        first = self.save()
        command = bot.tree.get_command("reschedule")
        interaction = NS(guild_id=1001, channel_id=3001, guild=None, user=NS(id=7002),
                         response=NS(defer=AsyncMock(), send_message=AsyncMock()),
                         followup=NS(send=AsyncMock()))
        await command.callback(interaction, str(first.deadline_id), "2026-02-30")
        self.assertIn("real date", interaction.response.send_message.call_args.args[0])
        await command.callback(interaction, str(first.deadline_id), "2026-10-27")
        self.assertIn("Only the original contributor", interaction.followup.send.call_args.args[0])

    async def test_valid_false_and_invalid_classifier_json_are_distinguished(self):
        with patch.object(bot.claude.messages, "create", return_value=model_response({"is_important": False})):
            self.assertNotIn("classification_failed", bot.analyze_message("lol"))
        with patch.object(bot.claude.messages, "create", return_value=model_response(["invalid"])):
            self.assertTrue(bot.analyze_message("quiz moved")["classification_failed"])

    async def test_reschedule_during_send_removes_stale_reminder_card(self):
        original = self.save()
        conn = bot.db()
        conn.execute("UPDATE deadlines SET created_at = NULL")
        conn.commit()
        conn.close()
        sent = NS(delete=AsyncMock())

        async def sending(**kwargs):
            self.store.change(original.deadline_id, 1001, 3001, 7001, new_date="2026-10-27")
            return sent

        channel = NS(id=3001, category=None, send=AsyncMock(side_effect=sending))
        with patch.object(bot.bot.__class__, "guilds", new_callable=lambda: property(
                lambda _: [NS(id=1001)])), \
                patch.object(bot.bot, "get_channel", return_value=channel), \
                patch.object(bot, "class_now", return_value=datetime(2026, 10, 24, 6, 0)), \
                patch.dict(bot._reminder_slots, {}, clear=True):
            await bot.reminder_loop.coro()
        sent.delete.assert_awaited_once()
        self.assertEqual(self.rows()[0]["group_reminded"], 0)


if __name__ == "__main__":
    unittest.main()
