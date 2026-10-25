"""The entire manual deadline workflow with AI disabled and Discord mocked."""

import os
import unittest
from datetime import date
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

os.environ["ANTHROPIC_API_KEY"] = os.environ.get("ANTHROPIC_API_KEY") or "offline-test"

import bot
from deadline_form import AddDeadlineModal, RetryDeadlineView
from test_functionality import DatabaseCase


class DeadlineFormTests(DatabaseCase, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        for patcher in (
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}),
            patch.object(bot, "class_today", return_value=date(2026, 10, 24)),
            patch.object(bot, "rate_limiter", bot.RateLimiter()),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def interaction(self, user_id=7001, message_id=8001):
        sent = NS(id=message_id, jump_url=f"https://discord.com/channels/1001/3001/{message_id}",
                  edit=AsyncMock(), delete=AsyncMock())
        return NS(id=message_id + 10000, guild_id=1001, channel_id=3001, guild=None,
                  channel=NS(id=3001, category=None), user=NS(id=user_id, display_name="Emma"),
                  response=NS(send_modal=AsyncMock(), send_message=AsyncMock(), defer=AsyncMock(),
                              is_done=Mock(return_value=True)), followup=NS(send=AsyncMock(return_value=sent)),
                  original_response=AsyncMock(return_value=sent), edit_original_response=AsyncMock())

    def form(self, *, title="Chemistry quiz", due="2026-10-25", audience=""):
        form = AddDeadlineModal(bot, 1001, 3001, 7001)
        form.assignment._value = title
        form.due_date._value = due
        form.audience._value = audience
        return form

    async def test_command_opens_form_with_no_ai(self):
        interaction = self.interaction()
        self.assertFalse(bot.ai_available())
        await bot.tree.get_command("add-deadline").callback(interaction)
        self.assertIsInstance(interaction.response.send_modal.call_args.args[0], AddDeadlineModal)

    async def test_add_browse_reschedule_cancel_work_without_model_calls(self):
        interaction = self.interaction()
        with patch.object(bot, "claude", Mock()) as model:
            await self.form().on_submit(interaction)
            rows = bot.get_upcoming_deadlines(3001, 1001)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["source_message_id"], 8001)
            self.assertEqual(rows[0]["link"], "https://discord.com/channels/1001/3001/8001")
            await bot._browse_memory(interaction, "due")
            await bot.tree.get_command("reschedule").callback(interaction, str(rows[0]["id"]), "2026-10-27")
            self.assertEqual(bot.get_upcoming_deadlines(3001, 1001)[0]["due_date"], "2026-10-27")
            await bot.tree.get_command("cancel-deadline").callback(interaction, str(rows[0]["id"]))
            view = interaction.followup.send.call_args.kwargs["view"]
            await view.confirm.callback(interaction)
            self.assertFalse(bot.get_upcoming_deadlines(3001, 1001))
            model.messages.create.assert_not_called()

    async def test_invalid_date_preserves_other_fields_for_retry(self):
        interaction = self.interaction()
        await self.form(due="2026-02-30", audience="Period 3").on_submit(interaction)
        options = interaction.response.send_message.call_args.kwargs
        self.assertTrue(options["ephemeral"])
        view = options["view"]
        self.assertIsInstance(view, RetryDeadlineView)
        self.assertEqual(view.values["summary"], "Chemistry quiz")
        self.assertEqual(view.values["audience"], "Period 3")
        self.assertFalse(bot.get_upcoming_deadlines(3001, 1001))
        await view.edit_details.callback(interaction)
        modal = interaction.response.send_modal.call_args.args[0]
        self.assertEqual(modal.assignment.default, "Chemistry quiz")

    async def test_webhook_receipt_without_guild_still_gets_server_source_link(self):
        interaction = self.interaction()
        interaction.followup.send.return_value.jump_url = "https://discord.com/channels/@me/3001/8001"
        await self.form().on_submit(interaction)
        self.assertEqual(bot.get_upcoming_deadlines(3001, 1001)[0]["link"],
                         "https://discord.com/channels/1001/3001/8001")

    async def test_past_date_and_blank_title_do_not_save(self):
        for form in (self.form(due="2026-10-23"), self.form(title="   ")):
            interaction = self.interaction()
            await form.on_submit(interaction)
            interaction.followup.send.assert_not_called()
        self.assertFalse(bot.get_upcoming_deadlines(3001, 1001))

    async def test_today_is_allowed_and_audience_is_recorded(self):
        interaction = self.interaction()
        await self.form(due="2026-10-24", audience="Period 3").on_submit(interaction)
        self.assertEqual(bot.get_upcoming_deadlines(3001, 1001)[0]["audience"], "Period 3")

    async def test_two_forms_for_exact_copy_still_show_one_assignment(self):
        await self.form().on_submit(self.interaction(message_id=8001))
        await self.form().on_submit(self.interaction(message_id=8002))
        self.assertEqual(len(bot.get_upcoming_deadlines(3001, 1001)), 1)
        self.assertEqual(bot.count_user_data_by_id(7001, 1001)["deadlines"], 2)

    async def test_same_modal_cannot_save_twice(self):
        form = self.form()
        await form.on_submit(self.interaction())
        await form.on_submit(self.interaction())
        self.assertEqual(bot.count_user_data_by_id(7001, 1001)["deadlines"], 1)

    async def test_modal_rechecks_channel_after_admin_untracks_it(self):
        form = self.form()
        bot.deactivate_channel(3001)
        interaction = self.interaction()
        await form.on_submit(interaction)
        self.assertIn("no longer tracked", interaction.response.send_message.call_args.args[0])
        self.assertFalse(bot.get_upcoming_deadlines(3001, 1001))

    async def test_someone_else_cannot_submit_form_or_retry_it(self):
        form = self.form()
        interaction = self.interaction(user_id=7002)
        await form.on_submit(interaction)
        self.assertFalse(bot.get_upcoming_deadlines(3001, 1001))
        view = RetryDeadlineView(bot, 1001, 3001, 7001, {})
        self.assertFalse(await view.interaction_check(interaction))

    async def test_untracked_command_does_not_open_modal(self):
        bot.deactivate_channel(3001)
        interaction = self.interaction()
        await bot.tree.get_command("add-deadline").callback(interaction)
        interaction.response.send_modal.assert_not_called()

    async def test_rate_limit_stops_form_before_database_write(self):
        for _ in range(bot.RATE_LIMITS["deadline"][0]):
            bot.rate_limiter.allow("deadline", 1001, 7001)
        interaction = self.interaction()
        await self.form().on_submit(interaction)
        self.assertIn("several deadlines", interaction.response.send_message.call_args.args[0])
        self.assertFalse(bot.get_upcoming_deadlines(3001, 1001))

    async def test_deleting_receipt_removes_its_active_contribution(self):
        await self.form().on_submit(self.interaction())
        await bot.deadline_events.deleted(NS(guild_id=1001, channel_id=3001, message_id=8001))
        self.assertFalse(bot.get_upcoming_deadlines(3001, 1001))

    async def test_form_data_can_be_deleted_by_user_id(self):
        await self.form().on_submit(self.interaction())
        bot.forget_user_by_id(7001, 1001)
        self.assertFalse(bot.get_upcoming_deadlines(3001, 1001))

    async def test_no_ai_mode_does_not_classify_passive_messages(self):
        from test_functionality import fake_message
        with patch.object(bot, "analyze_message") as classifier:
            await bot.deadline_events.remember(fake_message("quiz tomorrow", addressed=False))
        classifier.assert_not_called()

    async def test_no_ai_quiz_command_explains_which_tools_work(self):
        interaction = self.interaction()
        await bot.tree.get_command("quiz").callback(interaction)
        self.assertIn("/add-deadline", interaction.response.send_message.call_args.args[0])
        interaction.followup.send.assert_not_called()

    async def test_manual_deadline_sends_scheduled_reminder_without_ai(self):
        await self.form().on_submit(self.interaction())
        conn = bot.db()
        conn.execute("UPDATE deadlines SET created_at = NULL")
        conn.commit()
        conn.close()
        channel = NS(id=3001, category=None, send=AsyncMock(return_value=NS(delete=AsyncMock())))
        from datetime import datetime
        with patch.object(bot.bot.__class__, "guilds", new_callable=lambda: property(lambda _: [NS(id=1001)])), \
                patch.object(bot.bot, "get_channel", return_value=channel), \
                patch.object(bot, "class_now", return_value=datetime(2026, 10, 24, 6)), \
                patch.dict(bot._reminder_slots, {}, clear=True), patch.object(bot, "claude", Mock()) as model:
            await bot.reminder_loop.coro()
            await bot.reminder_loop.coro()
        channel.send.assert_awaited_once()
        model.messages.create.assert_not_called()
        self.assertIn("Chemistry quiz", str(channel.send.call_args.kwargs["embed"].to_dict()))

    async def test_failed_public_receipt_does_not_lose_saved_deadline(self):
        form = self.form()
        interaction = self.interaction()
        interaction.followup.send.side_effect = RuntimeError("network")
        with self.assertRaises(RuntimeError):
            await form.on_submit(interaction)
        self.assertEqual(len(bot.get_upcoming_deadlines(3001, 1001)), 1)
        interaction.followup.send.side_effect = None
        await form.on_error(interaction, RuntimeError())
        self.assertIn("was saved", interaction.followup.send.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
