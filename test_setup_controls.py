"""Offline checks for the no-typing setup flow."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import bot
from setup_controls import SetupView, timezone_choices


def interaction():
    channel = MagicMock(spec=discord.TextChannel)
    channel.id, channel.name, channel.mention = 123, "class", "<#123>"
    return SimpleNamespace(
        user=SimpleNamespace(id=42, display_name="Admin",
                             guild_permissions=SimpleNamespace(manage_guild=True)),
        guild_id=456, channel=channel,
        guild=SimpleNamespace(get_channel=lambda channel_id: channel if channel_id == 123 else None),
        response=SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock(), defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()), original_response=AsyncMock())


class TimezoneSearchTests(unittest.TestCase):
    def test_city_search(self):
        self.assertEqual(timezone_choices("chicago")[0].value, "America/Chicago")
        self.assertEqual(timezone_choices("London")[0].value, "Europe/London")
        self.assertEqual(timezone_choices("central us")[0].value, "America/Chicago")

    def test_less_common_city_and_iana_name(self):
        self.assertIn("Asia/Kathmandu", [choice.value for choice in timezone_choices("Kathmandu")])
        self.assertEqual(timezone_choices("America/Los_Angeles")[0].value, "America/Los_Angeles")

    def test_limits_and_saved_default(self):
        choices = timezone_choices("", "Asia/Kathmandu")
        self.assertEqual(choices[0].value, "Asia/Kathmandu")
        self.assertLessEqual(len(choices), 25)
        self.assertEqual(timezone_choices("not a real timezone"), [])
        self.assertTrue(all(len(choice.name) <= 100 for choice in choices))


class SetupFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_bare_setup_opens_menu_without_writing(self):
        ctx = interaction()
        with patch.object(bot, "_require_admin", AsyncMock(return_value=True)), \
             patch.object(bot, "get_guild_settings", return_value={"timezone": "America/Chicago"}), \
             patch.object(bot, "configure_guild") as configure:
            await bot.setup_command.callback(ctx)
        configure.assert_not_called()
        view = ctx.response.send_message.call_args.kwargs["view"]
        self.assertIsInstance(view, SetupView)
        self.assertEqual(view.timezone_name, "America/Chicago")
        self.assertIn("<#123>", view.prompt())
        self.assertEqual(len(view.zone_menu.options), 25)

    async def test_explicit_timezone_keeps_fast_path(self):
        ctx = interaction()
        with patch.object(bot, "_require_admin", AsyncMock(return_value=True)), \
             patch.object(bot, "get_guild_settings", return_value=None), \
             patch.object(bot, "configure_guild") as configure, \
             patch.object(bot, "activate_channel") as activate, \
             patch.object(bot, "record_event"):
            await bot.setup_command.callback(ctx, timezone_name="Europe/London")
        configure.assert_called_once_with(456, "Europe/London", 42)
        activate.assert_called_once_with(123, 456, "class", 42)

    async def test_invalid_timezone_does_not_write(self):
        ctx = interaction()
        with patch.object(bot, "_require_admin", AsyncMock(return_value=True)), \
             patch.object(bot, "get_guild_settings", return_value=None), \
             patch.object(bot, "configure_guild") as configure:
            await bot.setup_command.callback(ctx, timezone_name="../bad")
        configure.assert_not_called()
        self.assertIn("suggestions", ctx.response.send_message.call_args.args[0])

    async def test_menu_requires_original_admin(self):
        ctx = interaction()
        view = SetupView(42, "America/Chicago", "<#123>", AsyncMock())
        ctx.user.id = 99
        self.assertFalse(await view.interaction_check(ctx))
        ctx.user.id = 42
        ctx.user.guild_permissions.manage_guild = False
        self.assertFalse(await view.interaction_check(ctx))

    async def test_confirmation_runs_once(self):
        ctx = interaction()
        finish = AsyncMock(return_value=True)
        view = SetupView(42, "America/Chicago", "<#123>", finish)
        await view.confirm.callback(ctx)
        await view.confirm.callback(ctx)
        finish.assert_awaited_once_with(ctx, "America/Chicago")
        self.assertTrue(all(child.disabled for child in view.children))

    async def test_custom_saved_timezone_is_in_menu(self):
        view = SetupView(42, "Asia/Kathmandu", "<#123>", AsyncMock())
        self.assertEqual(len(view.zone_menu.options), 25)
        self.assertTrue(view.zone_menu.options[0].default)
        self.assertEqual(view.zone_menu.options[0].value, "Asia/Kathmandu")

    async def test_cancel_and_expiry_do_not_save(self):
        ctx = interaction()
        finish = AsyncMock()
        view = SetupView(42, "America/Chicago", "<#123>", finish)
        await view.cancel.callback(ctx)
        finish.assert_not_awaited()
        view = SetupView(42, "America/Chicago", "<#123>", finish)
        view.message = SimpleNamespace(edit=AsyncMock())
        await view.on_timeout()
        finish.assert_not_awaited()
        self.assertTrue(all(child.disabled for child in view.children))

    async def test_removed_channel_does_not_save(self):
        ctx = interaction()
        ctx.guild.get_channel = lambda _: None
        with patch.object(bot, "_require_admin", AsyncMock(return_value=True)), \
             patch.object(bot, "configure_guild") as configure:
            self.assertFalse(await bot._complete_setup(ctx, "America/Chicago", None, ctx.channel))
        configure.assert_not_called()


if __name__ == "__main__":
    unittest.main()
