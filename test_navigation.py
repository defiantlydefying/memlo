"""Command-first navigation; all checks run without Discord or paid APIs."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import bot
from navigation import CommandMenu
from quiz_controls import QuizPicker
from test_functionality import DatabaseCase, fake_message


class MenuTests(unittest.IsolatedAsyncioTestCase):
    async def test_help_buttons_use_existing_handlers(self):
        callback = AsyncMock()
        host = NS(tree=NS(get_command=lambda name: NS(callback=callback)))
        view = CommandMenu(host, 7)
        self.assertEqual(len(view.children), 5)
        interaction = NS(user=NS(id=7))
        await view.children[0].callback(interaction)
        callback.assert_awaited_once_with(interaction)

    async def test_other_user_cannot_use_public_menu(self):
        view = CommandMenu(bot, 7, quiz_only=True)
        interaction = NS(user=NS(id=8), response=NS(send_message=AsyncMock()))
        self.assertFalse(await view.interaction_check(interaction))
        self.assertTrue(interaction.response.send_message.call_args.kwargs["ephemeral"])

    async def test_help_is_private_and_clickable(self):
        interaction = NS(user=NS(id=7), response=NS(send_message=AsyncMock()))
        await bot.help_command.callback(interaction)
        options = interaction.response.send_message.call_args.kwargs
        self.assertTrue(options["ephemeral"])
        self.assertIsInstance(options["view"], CommandMenu)
        self.assertIn("Mentions to ask questions", options["embed"].description)


class PickerTests(DatabaseCase, unittest.IsolatedAsyncioTestCase):
    def material(self, group="chem", *, topic="Chemistry"):
        return bot.save_material(f"{group}.pdf", "Chemical reactions", "Alex", 3001,
                                 "https://example.com/notes", topic, group, 1001, 7001)

    def interaction(self):
        return NS(guild_id=1001, channel_id=3001, user=NS(id=7001),
                  channel=NS(id=3001, category=None),
                  response=NS(defer=AsyncMock(), send_message=AsyncMock()),
                  followup=NS(send=AsyncMock())), None

    async def test_no_args_requires_explicit_selection_before_generation(self):
        self.material("chem")
        self.material("bio", topic="Biology")
        interaction, _ = self.interaction()
        with patch.object(bot, "make_quiz") as make:
            await bot.tree.get_command("quiz").callback(interaction)
        make.assert_not_called()
        options = interaction.followup.send.call_args.kwargs
        self.assertTrue(options["ephemeral"])
        picker = options["view"]
        self.assertIsInstance(picker, QuizPicker)
        self.assertIsNone(picker.group)
        self.assertTrue(picker.start.disabled)
        self.assertEqual({option.value for option in picker.material.options}, {"chem", "bio"})

    async def test_picker_passes_selection_to_same_handler_once(self):
        self.material()
        groups = bot.get_material_groups(3001)
        callback = AsyncMock()
        host = NS(tree=NS(get_command=lambda name: NS(callback=callback)))
        picker = QuizPicker(host, 7001, groups)
        picker.material._values = ["chem"]
        interaction = NS(user=NS(id=7001), response=NS(edit_message=AsyncMock(), send_message=AsyncMock()))
        await picker.choose_material(interaction)
        self.assertFalse(picker.start.disabled)
        await picker.start.callback(interaction)
        await picker.start.callback(interaction)
        callback.assert_awaited_once_with(interaction, "chem", None, True)

    async def test_picker_rejects_other_user(self):
        self.material()
        picker = QuizPicker(bot, 7001, bot.get_material_groups(3001))
        interaction = NS(user=NS(id=99), response=NS(send_message=AsyncMock()))
        self.assertFalse(await picker.interaction_check(interaction))

    async def test_mention_tracking_cannot_change_settings(self):
        message = fake_message("@Memlo stop tracking")
        message.author.guild_permissions.manage_guild = True
        with patch.object(bot, "deactivate_category") as change:
            self.assertTrue(await bot.handle_admin_command(message))
        change.assert_not_called()
        self.assertIn("Nothing was changed", message.reply.call_args.args[0])

    async def test_picker_rechecks_tracking_before_generation(self):
        self.material()
        picker = QuizPicker(bot, 7001, bot.get_material_groups(3001))
        picker.group = "chem"
        bot.deactivate_channel(3001)
        interaction, _ = self.interaction()
        with patch.object(bot, "make_quiz") as make:
            await picker.start.callback(interaction)
        make.assert_not_called()
        self.assertIn("tracked", interaction.response.send_message.call_args.args[0])
