"""Quiz selection, publication order, and failure recovery without live APIs."""

import asyncio
import sqlite3
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

from test_functionality import DatabaseCase
import bot
from quiz_service import MAX_CONTEXT_CHARS, QuizError, QuizService


QUIZ = dict(quiz="1. What is a mole?\n2. Explain a reaction.\n3. Define an ion.",
            answer_key="PRIVATE ANSWER KEY: 1. Amount of substance 2. Transformation 3. Charged particle")


class QuizTests(DatabaseCase, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.service = QuizService(bot)
        for name, value in (("quiz_service", self.service), ("rate_limiter", bot.RateLimiter())):
            patcher = patch.object(bot, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def material(self, group="chem", *, topic="Chemistry", channel=3001, digest="Chemical reactions"):
        return bot.save_material(f"{group}.pdf", digest, "Alex", channel,
                                 "https://example.com/notes", topic, group, 1001, 7001)

    def interaction(self):
        sent = NS(id=8001, edit=AsyncMock(), delete=AsyncMock())
        return NS(guild_id=1001, channel_id=3001, user=NS(id=7001),
                  channel=NS(id=3001, category=None),
                  response=NS(defer=AsyncMock(), send_message=AsyncMock()),
                  followup=NS(send=AsyncMock(return_value=sent))), sent

    async def prepared(self, group="chem"):
        with patch.object(bot, "make_quiz", return_value=QUIZ):
            return await self.service.build("quiz me", 3001, 1001, group)

    async def test_explicit_set_never_calls_ai_topic_selector(self):
        self.material("bio", topic="Biology")
        self.material("chem")
        interaction, _ = self.interaction()
        with patch.object(bot, "make_quiz", return_value=QUIZ) as make, \
                patch.object(bot, "pick_material_group", side_effect=AssertionError("No guessing")):
            await bot.tree.get_command("quiz").callback(interaction, "bio")
        self.assertEqual(make.call_args.args[2][0]["quiz_group"], "bio")

    async def test_slash_quizzes_are_private_by_default_and_label_the_set(self):
        self.material()
        interaction, _ = self.interaction()
        with patch.object(bot, "make_quiz", return_value=QUIZ):
            await bot.tree.get_command("quiz").callback(interaction)
        self.assertTrue(interaction.response.defer.call_args.kwargs["ephemeral"])
        sent_options = interaction.followup.send.call_args.kwargs
        self.assertTrue(sent_options["ephemeral"])
        self.assertIn("Chemistry", sent_options["embed"].footer.text)
        self.assertNotIn(QUIZ["answer_key"], sent_options["embed"].description)

    async def test_public_quiz_is_explicit_and_difficulty_is_passed_through(self):
        self.material()
        interaction, _ = self.interaction()
        with patch.object(bot, "make_quiz", return_value=QUIZ) as make:
            await bot.tree.get_command("quiz").callback(interaction, "chem", NS(value="challenging"), False)
        self.assertFalse(interaction.response.defer.call_args.kwargs["ephemeral"])
        self.assertFalse(interaction.followup.send.call_args.kwargs["ephemeral"])
        self.assertIn("challenging", make.call_args.args[0])

    async def test_unknown_or_ambiguous_selection_does_not_generate(self):
        self.material("chem1")
        self.material("chem2")
        for selection in ("deleted", "Chemistry"):
            interaction, _ = self.interaction()
            with patch.object(bot, "make_quiz") as make:
                await bot.tree.get_command("quiz").callback(interaction, selection)
            make.assert_not_called()
            self.assertIn("won’t guess", interaction.followup.send.call_args.args[0])

    async def test_empty_shelf_does_not_generate_fiction(self):
        interaction, _ = self.interaction()
        with patch.object(bot, "make_quiz") as make:
            await bot.tree.get_command("quiz").callback(interaction)
        make.assert_not_called()
        self.assertIn("empty", interaction.followup.send.call_args.args[0])

    async def test_untracked_channel_stays_unread(self):
        interaction, _ = self.interaction()
        interaction.channel_id = 9999
        interaction.channel.id = 9999
        with patch.object(bot, "get_material_groups") as read:
            await bot.tree.get_command("quiz").callback(interaction)
        read.assert_not_called()
        self.assertIn("tracked", interaction.response.send_message.call_args.args[0])

    async def test_rate_limit_prevents_extra_model_call(self):
        self.material()
        for _ in range(bot.RATE_LIMITS["quiz"][0]):
            bot.rate_limiter.allow("quiz", 1001, 7001)
        interaction, _ = self.interaction()
        with patch.object(bot, "make_quiz") as make:
            await bot.tree.get_command("quiz").callback(interaction)
        make.assert_not_called()
        self.assertIn("five minutes", interaction.followup.send.call_args.args[0])

    async def test_key_is_committed_before_button_is_enabled(self):
        self.material()
        prepared = await self.prepared()
        sent = NS(id=8001, edit=AsyncMock(), delete=AsyncMock())

        async def enable(**kwargs):
            self.assertIn(QUIZ["answer_key"], bot.get_quiz(8001))
            self.assertIsInstance(kwargs["view"], bot.QuizView)

        sent.edit.side_effect = enable
        send = AsyncMock(return_value=sent)
        await self.service.publish(prepared, send, 3001, 1001, 7001)
        self.assertNotIn("view", send.call_args.kwargs)
        sent.edit.assert_awaited_once()

    async def test_failed_key_save_removes_ungradeable_card(self):
        self.material()
        prepared = await self.prepared()
        sent = NS(id=8001, edit=AsyncMock(), delete=AsyncMock())
        with patch.object(bot, "save_quiz", side_effect=sqlite3.OperationalError("failed")):
            with self.assertRaises(QuizError):
                await self.service.publish(prepared, AsyncMock(return_value=sent), 3001, 1001, 7001)
        sent.edit.assert_not_called()
        sent.delete.assert_awaited_once()
        self.assertIsNone(bot.get_quiz(8001))

    async def test_failed_button_attach_cleans_up_key_and_card(self):
        self.material()
        prepared = await self.prepared()
        sent = NS(id=8001, edit=AsyncMock(side_effect=RuntimeError("network")), delete=AsyncMock())
        with self.assertRaises(QuizError):
            await self.service.publish(prepared, AsyncMock(return_value=sent), 3001, 1001, 7001)
        self.assertIsNone(bot.get_quiz(8001))
        sent.delete.assert_awaited_once()

    async def test_deleted_sources_are_not_published(self):
        self.material()
        prepared = await self.prepared()
        bot.forget_user_by_id(7001, 1001)
        send = AsyncMock()
        with self.assertRaisesRegex(QuizError, "deleted"):
            await self.service.publish(prepared, send, 3001, 1001, 7001)
        send.assert_not_called()

    async def test_deletion_during_post_also_cleans_up(self):
        self.material()
        prepared = await self.prepared()
        sent = NS(id=8001, edit=AsyncMock(), delete=AsyncMock())

        async def send(**kwargs):
            bot.forget_user_by_id(7001, 1001)
            return sent

        with self.assertRaisesRegex(QuizError, "deleted"):
            await self.service.publish(prepared, send, 3001, 1001, 7001)
        sent.edit.assert_not_called()
        sent.delete.assert_awaited_once()
        self.assertIsNone(bot.get_quiz(8001))

    async def test_source_lookup_never_crosses_channel_boundary(self):
        self.material("chem", channel=9999)
        with patch.object(bot, "make_quiz") as make:
            with self.assertRaises(QuizError):
                await self.service.build("quiz", 3001, 1001, "chem")
        make.assert_not_called()

    async def test_incomplete_quiz_is_not_publishable(self):
        self.material()
        with patch.object(bot, "make_quiz", return_value={"quiz": "1. Question"}):
            with self.assertRaisesRegex(QuizError, "answer key"):
                await self.service.build("quiz", 3001, 1001, "chem")
        self.assertFalse(self.service.slots.locked())

    async def test_oversized_context_is_rejected_before_api_call(self):
        self.material(digest="x" * (MAX_CONTEXT_CHARS + 1))
        with patch.object(bot, "make_quiz") as make:
            with self.assertRaisesRegex(QuizError, "too large"):
                await self.service.build("quiz", 3001, 1001, "chem")
        make.assert_not_called()

    async def test_generation_error_releases_capacity(self):
        self.material()
        with patch.object(bot, "make_quiz", side_effect=RuntimeError("API unavailable")):
            with self.assertRaisesRegex(QuizError, "unavailable"):
                await self.service.build("quiz", 3001, 1001, "chem")
        self.assertFalse(self.service.slots.locked())

    async def test_busy_generator_has_actionable_recovery(self):
        self.material()
        self.service.slots = NS(acquire=AsyncMock(side_effect=asyncio.TimeoutError), release=Mock())
        with self.assertRaisesRegex(QuizError, "few quizzes"):
            await self.service.build("quiz", 3001, 1001, "chem")
        self.service.slots.release.assert_not_called()

    async def test_support_uses_confirmed_discord_contact(self):
        interaction, _ = self.interaction()
        await bot.tree.get_command("support").callback(interaction)
        self.assertIn("@kidwithakrow._.", interaction.response.send_message.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
