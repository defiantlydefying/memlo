"""Behavior and Discord UX regressions. Offline; never reads the real database."""

import json
import os
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

os.environ["ANTHROPIC_API_KEY"] = os.environ.get("ANTHROPIC_API_KEY") or "offline-test"

import bot
import presentation as ui


def model_response(value):
    return NS(content=[NS(type="text", text=json.dumps(value))])


def fake_message(text="", *, addressed=True, channel_id=3001, author_id=7001):
    author = NS(id=author_id, display_name="Alex", bot=False, roles=[],
                guild_permissions=NS(manage_guild=False))
    channel = NS(id=channel_id, category=None, send=AsyncMock())
    return NS(id=9001, guild=NS(id=1001, me=None), channel=channel,
              author=author, mentions=[bot.bot.user] if addressed else [],
              role_mentions=[], content=text, clean_content=text,
              attachments=[], reference=None, reply=AsyncMock(return_value=NS(
                  id=8001, edit=AsyncMock(), delete=AsyncMock())),
              jump_url=f"https://discord.com/channels/1001/{channel_id}/9001",
              webhook_id=None)


class PresentationTests(unittest.TestCase):
    def test_quiz_questions_are_not_quiz_requests(self):
        for text in ("when is our quiz?", "what date is the chemistry quiz?",
                     "is the quiz tomorrow", "what is on the quiz?",
                     "don't make a quiz", "no quiz please"):
            with self.subTest(text=text):
                self.assertFalse(ui.is_quiz_request(text))

    def test_quiz_actions(self):
        for text in ("quiz", "quiz me", "quiz us on chemistry", "please quiz",
                     "can you make a quiz from these pages?", "create a biology quiz",
                     "quiz on the civil war"):
            with self.subTest(text=text):
                self.assertTrue(ui.is_quiz_request(text))

    def test_addresses(self):
        self.assertEqual(ui.command_text("@Memlo quiz me"), "quiz me")
        self.assertEqual(ui.command_text("<@123> help"), "help")
        self.assertEqual(ui.command_text("<@!123> help"), "help")
        self.assertEqual(ui.command_text("why is Memlo useful?"), "why is Memlo useful?")

    def test_acknowledgments_need_no_model(self):
        with patch.object(bot, "_haiku_json", side_effect=AssertionError("Unexpected API call")):
            for text in ("ok", "thanks!", "👍", "lol", "OK thanks", "got it"):
                self.assertFalse(bot.wants_a_reply(text))

    def test_dates(self):
        today = date(2026, 10, 24)
        self.assertEqual(ui.due_label("2026-10-24", today), "Today")
        self.assertEqual(ui.due_label("2026-10-25", today), "Tomorrow")
        self.assertEqual(ui.due_label("bad", today), "Date needs checking")

    def test_long_output_is_split_without_losing_words(self):
        original = " ".join(f"word{i}" for i in range(1500))
        parts = ui.split_message(original)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(0 < len(p) <= 1900 for p in parts))
        self.assertEqual(" ".join(parts), original)
        self.assertEqual("".join(ui.split_message("x" * 5000)), "x" * 5000)

    def test_source_links_reject_markup_injection(self):
        self.assertIn("View source", ui.source_link("https://example.com/notes"))
        for source in ("javascript:alert(1)", "https://example.com/a)@everyone", "bad", None):
            self.assertEqual(ui.source_link(source), "")

    def test_cards_fit_discord_limits_with_long_user_labels(self):
        rows = [dict(summary="*" * 500, due_date="2026-10-25", audience="x" * 500,
                     link="https://example.com/" + "a" * 3000) for _ in range(12)]
        pages = ui.deadline_cards(rows, date(2026, 10, 24), "America/Chicago")
        self.assertEqual(len(pages), 3)
        for page in pages:
            self.assertLessEqual(len(page), 6000)
            for field in page.fields:
                self.assertLessEqual(len(field.name), 256)
                self.assertLessEqual(len(field.value), 1024)
        self.assertIn("haven’t saved", ui.deadline_cards([], date.today(), "UTC")[0].description)

    def test_materials_are_paginated(self):
        groups = [dict(topics="Chemistry", filenames="chapter.pdf", file_count=2,
                       link="https://example.com/chapter") for _ in range(11)]
        self.assertEqual(len(ui.material_cards(groups)), 3)
        self.assertIn("empty", ui.material_cards([])[0].title)

    def test_worksheet_keeps_multiline_questions(self):
        text = "Let’s practice\n1. Explain the reaction.\n   Include a formula.\n2) Why?\n3. When?"
        worksheet = bot._answer_worksheet(text)
        self.assertIn("Include a formula.", worksheet)
        self.assertEqual(worksheet.count("Answer:"), 3)
        self.assertLessEqual(len(bot._answer_worksheet("1. " + "x" * 5000)), 3000)


class DatabaseCase(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path_patch = patch.object(bot, "DB_PATH", str(Path(self.tempdir.name) / "test.db"))
        self.path_patch.start()
        bot.init_db()
        bot.configure_guild(1001, "America/Chicago", 1)
        bot.activate_channel(3001, 1001, "chemistry", 1)

    def tearDown(self):
        self.path_patch.stop()
        self.tempdir.cleanup()

    def add_material(self, group, channel=3001, source_id=None):
        bot.save_material("notes.pdf", "A digest", "Alex", channel,
                          "https://example.com/notes", "Chemistry", group,
                          1001, 7001, source_id)


class StorageAndModelTests(DatabaseCase):
    def test_group_lookup_stays_in_channel(self):
        self.add_material("one")
        self.assertFalse(bot.get_materials_in_group("one", 9999))

    def test_group_merge_is_scoped_and_requires_live_groups(self):
        self.add_material("one", source_id=123)
        self.add_material("two", source_id=456)
        self.add_material("one", channel=9999)
        self.assertTrue(bot.merge_quiz_groups("one", "two", 3001))
        self.assertEqual(bot.material_group_for_message(123, 3001), "two")
        self.assertEqual(len(bot.get_materials_in_group("one", 9999)), 1)
        self.assertFalse(bot.merge_quiz_groups("two", "deleted", 3001))
        self.assertEqual(len(bot.get_materials_in_group("two", 3001)), 2)

    def test_migration_idempotent_and_backfills_source_message(self):
        self.add_material("msg-123")
        bot.init_db()
        bot.init_db()
        self.assertEqual(bot.material_group_for_message(123, 3001), "msg-123")

    def test_migration_prefers_original_message_over_merged_group(self):
        bot.save_material("notes.pdf", "Digest", "Alex", 3001,
                          "https://discord.com/channels/1001/3001/456", "Chemistry",
                          "msg-123", 1001, 7001)
        bot.init_db()
        self.assertEqual(bot.material_group_for_message(456, 3001), "msg-123")

    def test_invalid_group_cannot_switch_subject(self):
        groups = [dict(quiz_group="chem", topics="Chemistry", filenames="chem.pdf")]
        with patch.object(bot, "_haiku_json", return_value={"group": "made-up"}):
            self.assertIsNone(bot.pick_material_group("quiz me on biology", groups))
        with patch.object(bot, "_haiku_json", side_effect=AssertionError("No API needed")):
            self.assertEqual(bot.pick_material_group("quiz me", groups), "chem")

    def test_bad_json_shapes_do_not_crash_helpers(self):
        with patch.object(bot.claude.messages, "create", return_value=model_response([1, 2])):
            self.assertEqual(bot._haiku_json("prompt", {"safe": True}), {"safe": True})
        with patch.object(bot, "_haiku_json", return_value={"group": ["chem"]}):
            self.assertIsNone(bot.pick_material_group("quiz me on biology", [
                dict(quiz_group="chem", topics="Chemistry", filenames="chem.pdf")]))

    def test_classifier_does_not_save_string_false_or_invalid_date(self):
        with patch.object(bot.claude.messages, "create", return_value=model_response(
                {"is_important": "false", "summary": "Oops"})):
            self.assertFalse(bot.analyze_message("test tomorrow")["is_important"])
        with patch.object(bot.claude.messages, "create", return_value=model_response(
                {"is_important": True, "summary": "Test", "type": "deadline", "due_date": "2026-02-30"})):
            self.assertEqual(bot.analyze_message("test tomorrow")["due_date"], "")

    def test_quiz_needs_a_key_and_three_questions(self):
        for value in ({"quiz": "1. Why?\n2. How?\n3. What?"},
                      {"quiz": "1. Why?", "answer_key": "Because"},
                      {"quiz": ["question"], "answer_key": "Because"}):
            with patch.object(bot.claude.messages, "create", return_value=model_response(value)):
                self.assertEqual(bot.make_quiz("quiz", [], []), {})

    def test_selected_set_excludes_other_topic_notes(self):
        response = model_response({"quiz": "1. Why?\n2. How?\n3. What?", "answer_key": "Answers"})
        with patch.object(bot.claude.messages, "create", return_value=response) as create:
            bot.make_quiz("quiz", [dict(summary="UNRELATED CIVIL WAR NOTE")],
                          [dict(filename="chem.pdf", digest="Chemical reactions")])
        self.assertNotIn("UNRELATED CIVIL WAR NOTE", create.call_args.kwargs["messages"][0]["content"])

    def test_memory_uses_guild_date_and_includes_sources(self):
        with patch.object(bot, "class_today", return_value=date(2026, 10, 24)) as today:
            memory = bot._format_memory([dict(summary="Essay", due_date="2026-10-25",
                author="Alex", audience="", link="https://example.com/essay")], [], [], 1001)
        today.assert_called_once_with(1001)
        self.assertIn("Source: https://example.com/essay", memory)


class AsyncBehaviorTests(DatabaseCase, unittest.IsolatedAsyncioTestCase):
    async def test_when_is_quiz_answers_instead_of_generating(self):
        message = fake_message("@Memlo when is our quiz?")
        with patch.object(bot, "answer_question", return_value="Friday") as answer, \
                patch.object(bot, "make_quiz") as quiz:
            await bot._handle_message(message)
        answer.assert_called_once()
        quiz.assert_not_called()
        self.assertEqual(message.reply.call_args.args[0], "Friday")

    async def test_acks_are_silent(self):
        message = fake_message("@Memlo thanks!")
        await bot._handle_message(message)
        message.reply.assert_not_called()

    async def test_deletion_works_untracked_and_does_not_read_attachments(self):
        message = fake_message("@Memlo forget me", channel_id=9999)
        message.attachments = [object()]
        bot.save_note("note", "original", "Alex", 3001, "link", guild_id=1001, author_id=7001)
        with patch.object(bot, "read_attachments", new_callable=AsyncMock) as read:
            await bot._handle_message(message)
        read.assert_not_called()
        self.assertIsInstance(message.reply.call_args.kwargs["view"], bot.ForgetView)

    async def test_same_name_legacy_records_are_not_deleted(self):
        bot.save_note("Legacy", "Someone's old note", "Alex", 3001, "link", guild_id=1001)
        bot.save_note("Mine", "original", "Alex", 3001, "link", guild_id=1001, author_id=7001)
        view = bot.ForgetView(7001, 1001, "Alex", 3001)
        interaction = NS(response=NS(defer=AsyncMock()), edit_original_response=AsyncMock())
        await view.confirm.callback(interaction)
        self.assertEqual(bot.count_user_data("Alex", 3001)["notes"], 1)
        self.assertEqual(bot.count_user_data_by_id(7001, 1001)["notes"], 0)

    async def test_combine_only_requester_can_choose(self):
        view = bot.CombineView("new", "old", "notes", 7001, 3001)
        interaction = NS(user=NS(id=7002), response=NS(send_message=AsyncMock()))
        self.assertFalse(await view.interaction_check(interaction))
        interaction.user.id = 7001
        self.assertTrue(await view.interaction_check(interaction))

    async def test_stale_combine_does_not_move_data(self):
        self.add_material("new")
        view = bot.CombineView("new", "missing", "notes", 7001, 3001)
        interaction = NS(response=NS(defer=AsyncMock()), edit_original_response=AsyncMock())
        await view.combine.callback(interaction)
        self.assertIn("Nothing was combined", interaction.edit_original_response.call_args.kwargs["content"])
        self.assertEqual(len(bot.get_materials_in_group("new", 3001)), 1)

    async def test_upload_failure_does_not_swallow_second_file(self):
        message = fake_message()
        message.attachments = [NS(filename=name, content_type="image/png", size=10,
                                  read=AsyncMock(return_value=b"bytes"))
                               for name in ("bad.png", "good.png")]
        with patch.object(bot, "ingest_attachment", side_effect=[
                ValueError("bad response"), dict(topic="Chemistry", digest="notes")]):
            saved, issues = await bot.read_attachments(message)
        self.assertEqual([item["filename"] for item in saved], ["good.png"])
        self.assertEqual(len(issues), 1)

    async def test_large_file_is_rejected_before_download(self):
        att = NS(filename="big.pdf", content_type="application/pdf",
                 size=bot.MAX_ATTACHMENT_BYTES + 1, read=AsyncMock())
        message = fake_message()
        message.attachments = [att]
        saved, issues = await bot.read_attachments(message)
        att.read.assert_not_called()
        self.assertFalse(saved)
        self.assertIn("5 MB", issues[0])

    async def test_reply_to_older_upload_uses_it_not_latest(self):
        self.add_material("old", source_id=123)
        self.add_material("new", source_id=456)
        message = fake_message("@Memlo quiz me")
        message.reference = NS(message_id=123, resolved=None)
        with patch.object(bot, "make_quiz", return_value={
                "quiz": "1. Why?\n2. How?\n3. What?", "answer_key": "secret"}) as make:
            await bot._handle_message(message)
        self.assertEqual(make.call_args.args[2][0]["quiz_group"], "old")
        sent_embed = message.reply.call_args.kwargs["embed"]
        self.assertNotIn("secret", sent_embed.description)

    async def test_empty_memory_does_not_generate_fictional_quiz(self):
        with patch.object(bot, "make_quiz") as make:
            message = fake_message("@Memlo quiz me")
            await bot._handle_message(message)
        make.assert_not_called()
        self.assertIn("nothing to quiz", message.reply.call_args.args[0])

    async def test_failed_new_upload_does_not_quiz_old_material(self):
        self.add_material("old", source_id=123)
        message = fake_message("@Memlo quiz me")
        message.attachments = [object()]
        with patch.object(bot, "read_attachments", new_callable=AsyncMock,
                          return_value=([], ["This file failed"])), \
                patch.object(bot, "make_quiz") as make:
            await bot._handle_message(message)
        make.assert_not_called()
        self.assertIn("No new study material", message.reply.call_args.args[0])

    async def test_multisource_message_gets_one_receipt(self):
        message = fake_message("study notes https://example.com/new", addressed=False)
        message.attachments = [object()]
        with patch.object(bot, "read_attachments", new_callable=AsyncMock,
                          return_value=([dict(topic="Chemistry", filename="page.png")], [])), \
                patch.object(bot, "read_links", new_callable=AsyncMock,
                          return_value=([dict(topic="Chemistry", filename="article")], [])):
            await bot._handle_message(message)
        message.reply.assert_called_once()
        receipt = message.reply.call_args.kwargs["embed"]
        self.assertIn("page.png", receipt.description)
        self.assertIn("article", receipt.description)
        self.assertIn("2 files", receipt.fields[0].value)

    async def test_single_page_browse_omits_none_view(self):
        interaction = NS(guild_id=1001, channel_id=3001, user=NS(id=7001),
                         channel=NS(id=3001, category=None),
                         response=NS(defer=AsyncMock()), followup=NS(send=AsyncMock()))
        await bot._browse_memory(interaction, "due")
        self.assertNotIn("view", interaction.followup.send.call_args.kwargs)

    async def test_page_timeout_disables_buttons(self):
        view = bot.CardPages([ui.card("One"), ui.card("Two")], 7001)
        self.assertTrue(view.previous.disabled)
        self.assertFalse(view.next_page.disabled)
        view.message = NS(edit=AsyncMock())
        await view.on_timeout()
        self.assertTrue(all(child.disabled for child in view.children))

    async def test_threads_inherit_selected_parent(self):
        message = fake_message("@Memlo thanks!", channel_id=4001)
        message.channel.parent_id = 3001
        with patch.object(bot, "wants_a_reply", return_value=False) as wants:
            await bot._handle_message(message)
        wants.assert_called_once()

    async def test_bot_messages_never_reach_handler(self):
        message = fake_message()
        message.author.bot = True
        with patch.object(bot, "_handle_message", new_callable=AsyncMock) as handle:
            await bot.on_message(message)
        handle.assert_not_called()

    async def test_reminders_skip_untracked_channels_and_isolate_errors(self):
        for channel in (3001, 3002, 3003):
            bot.save_deadline("Essay", "2026-10-25", "Alex", channel,
                              "https://example.com/essay", guild_id=1001, author_id=7001)
        bot.activate_channel(3002, 1001, "bio", 1)
        conn = bot.db()
        conn.execute("UPDATE deadlines SET created_at = NULL")
        conn.commit()
        conn.close()
        channels = {i: NS(id=i, category=None, send=AsyncMock()) for i in (3001, 3002, 3003)}
        channels[3001].send.side_effect = RuntimeError("No permission")
        with patch.object(bot.bot.__class__, "guilds", new_callable=lambda: property(
                lambda _: [NS(id=1001)])), \
                patch.object(bot.bot, "get_channel", side_effect=channels.get), \
                patch.object(bot, "class_now", return_value=datetime(2026, 10, 24, 6, 0)), \
                patch.dict(bot._reminder_slots, {}, clear=True):
            await bot.reminder_loop.coro()
        channels[3002].send.assert_called_once()
        channels[3003].send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
