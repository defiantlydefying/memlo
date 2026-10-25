"""Slash commands for explicit, permission-checked deadline corrections."""

import asyncio

import discord
from discord import app_commands

from deadline_store import DeadlineNotFound, DeadlinePermissionError, valid_date
from presentation import DEADLINE_AMBER, card, clip, plain


class CancelDeadlineView(discord.ui.View):
    def __init__(self, store_factory, is_admin, row, requester_id):
        super().__init__(timeout=120)
        self.store_factory = store_factory
        self.is_admin = is_admin
        self.row = row
        self.requester_id = requester_id
        self.message = None
        self._lock = asyncio.Lock()
        self.resolved = False

    async def interaction_check(self, interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("This confirmation belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Cancel this deadline", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction, button):
        await interaction.response.defer()
        async with self._lock:
            if self.resolved:
                return
            try:
                await asyncio.to_thread(self.store_factory().change, self.row["id"],
                                        interaction.guild_id, interaction.channel_id, interaction.user.id,
                                        admin=self.is_admin(interaction), expected_revision=self.row["revision"])
                content = "Deadline cancelled. It won’t appear in `/due` or send reminders."
            except (DeadlineNotFound, DeadlinePermissionError, ValueError) as exc:
                content = str(exc) + ". Nothing was cancelled."
            self.resolved = True
            for child in self.children:
                child.disabled = True
            await interaction.edit_original_response(content=content, embed=None, view=self)
            self.stop()

    @discord.ui.button(label="Keep deadline", style=discord.ButtonStyle.secondary)
    async def keep(self, interaction, button):
        async with self._lock:
            if self.resolved:
                await interaction.response.defer()
                return
            self.resolved = True
            for child in self.children:
                child.disabled = True
            await interaction.response.edit_message(content="Kept. Nothing was changed.", embed=None, view=self)
            self.stop()

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(content="Confirmation expired. Nothing was cancelled.",
                                        embed=None, view=self)
            except discord.HTTPException:
                pass


def register_commands(tree, store_factory, is_admin):
    async def choices(interaction, current: str):
        rows = await asyncio.to_thread(store_factory().active, interaction.guild_id, interaction.channel_id)
        current = current.casefold()
        matches = [row for row in rows if current in row["summary"].casefold()
                   or current in str(row["id"]) or current in row["due_date"]]
        return [app_commands.Choice(name=clip(f"{row['summary']} · {row['due_date']} · #{row['id']}", 100),
                                   value=str(row["id"])) for row in matches[:25]]

    @tree.command(name="reschedule", description="Correct a saved deadline’s date")
    @app_commands.guild_only()
    @app_commands.describe(deadline="Choose a saved deadline (or its ID from /due)",
                           new_date="New due date, YYYY-MM-DD, in this server’s timezone")
    @app_commands.autocomplete(deadline=choices)
    async def reschedule(interaction: discord.Interaction, deadline: str, new_date: str):
        if not valid_date(new_date):
            await interaction.response.send_message("Use a real date in `YYYY-MM-DD` form, "
                                                     "for example `2026-11-02`.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await asyncio.to_thread(store_factory().change, int(deadline),
                                             interaction.guild_id, interaction.channel_id,
                                             interaction.user.id, new_date=new_date,
                                             admin=is_admin(interaction))
            content = (f"Already due **{new_date}**. Nothing changed." if result.action == "unchanged" else
                       f"Rescheduled: {result.old_date} → **{result.new_date}**. "
                       "Exact duplicate copies were updated too; reminders now use the new date.")
        except (ValueError, DeadlinePermissionError) as exc:
            content = ("Choose a saved deadline from the suggestions or use its numeric ID."
                       if not deadline.isdigit() else str(exc))
        await interaction.followup.send(content, ephemeral=True)

    @tree.command(name="cancel-deadline", description="Remove a cancelled assignment from upcoming deadlines")
    @app_commands.guild_only()
    @app_commands.describe(deadline="Choose the assignment to cancel")
    @app_commands.autocomplete(deadline=choices)
    async def cancel_deadline(interaction: discord.Interaction, deadline: str):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            row = await asyncio.to_thread(store_factory().inspect, int(deadline),
                                          interaction.guild_id, interaction.channel_id,
                                          interaction.user.id, admin=is_admin(interaction))
        except (ValueError, DeadlinePermissionError) as exc:
            await interaction.followup.send(
                str(exc) if deadline.isdigit() else "Choose a deadline from the suggestions.", ephemeral=True)
            return
        view = CancelDeadlineView(store_factory, is_admin, row, interaction.user.id)
        view.message = await interaction.followup.send(
            embed=card("Cancel this deadline?", f"**{plain(row['summary'])}** · {row['due_date']}\n"
                       "This removes the assignment and its exact copies from upcoming "
                       "deadlines and stops their reminders. Stored source attribution is kept "
                       "until you delete your data.", color=DEADLINE_AMBER),
            view=view, ephemeral=True, wait=True)

    return reschedule, cancel_deadline
