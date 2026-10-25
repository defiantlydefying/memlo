"""A fully local deadline-entry flow. No classifier, model, or paid API required."""

import asyncio
from datetime import date, timedelta

import discord
from discord import app_commands

from deadline_store import valid_date
from presentation import DEADLINE_AMBER, card, clip, plain


def tracked_here(host, channel, guild_id):
    category = host._category_of(channel)
    return host.is_tracked(guild_id, category.id if category else None,
                           getattr(channel, "parent_id", None) or channel.id)


class RetryDeadlineView(discord.ui.View):
    def __init__(self, host, guild_id, channel_id, requester_id, values):
        super().__init__(timeout=120)
        self.host = host
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.requester_id = requester_id
        self.values = values
        self.message = None

    async def interaction_check(self, interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("This form belongs to someone else.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Edit details", style=discord.ButtonStyle.primary)
    async def edit_details(self, interaction, button):
        await interaction.response.send_modal(AddDeadlineModal(
            self.host, self.guild_id, self.channel_id, self.requester_id, self.values))

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class AddDeadlineModal(discord.ui.Modal, title="Add a class deadline"):
    def __init__(self, host, guild_id, channel_id, requester_id, values=None):
        super().__init__(timeout=600)
        self.host = host
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.requester_id = requester_id
        self.saved = None
        self._lock = asyncio.Lock()
        values = values or {}
        tomorrow = host.class_today(guild_id) + timedelta(days=1)
        self.assignment = discord.ui.TextInput(
            label="Assignment or test", placeholder="e.g. Chemistry chapter 4 quiz",
            default=clip(values.get("summary", ""), 160) or None, max_length=160)
        self.due_date = discord.ui.TextInput(
            label="Due date (YYYY-MM-DD)",
            placeholder=clip(f"e.g. {tomorrow.isoformat()} · {host._timezone_for_guild(guild_id)}", 100),
            default=clip(values.get("due_date", ""), 10) or None, max_length=10)
        self.audience = discord.ui.TextInput(
            label="Who is it for? (optional)", placeholder="e.g. Period 3; leave blank for everyone here",
            default=clip(values.get("audience", ""), 100) or None, max_length=100, required=False)
        for item in (self.assignment, self.due_date, self.audience):
            self.add_item(item)

    async def interaction_check(self, interaction):
        if (interaction.user.id != self.requester_id or interaction.guild_id != self.guild_id
                or interaction.channel_id != self.channel_id):
            await interaction.response.send_message("Open your own form with `/add-deadline`.", ephemeral=True)
            return False
        return True

    async def _invalid(self, interaction, message, values):
        view = RetryDeadlineView(self.host, self.guild_id, self.channel_id, self.requester_id, values)
        await interaction.response.send_message(message + " Your other details are kept in the form.",
                                                  view=view, ephemeral=True)
        view.message = await interaction.original_response()

    async def on_submit(self, interaction):
        # Recheck at submission as well as dispatch: a scope may have been removed
        # or this form may have been opened before a server reset.
        if not await self.interaction_check(interaction):
            return
        if not tracked_here(self.host, interaction.channel, self.guild_id):
            await interaction.response.send_message(
                "This channel is no longer tracked. Nothing was saved; use `/setup-status`.", ephemeral=True)
            return
        values = dict(summary=" ".join(self.assignment.value.split()),
                      due_date=self.due_date.value.strip(), audience=" ".join(self.audience.value.split()))
        if not values["summary"] or len(values["summary"]) > 160:
            await self._invalid(interaction, "Use an assignment title between 1 and 160 characters.", values)
            return
        if len(values["audience"]) > 100:
            await self._invalid(interaction, "Keep the class or audience label under 101 characters.", values)
            return
        if not valid_date(values["due_date"]):
            await self._invalid(interaction, "Use a real date in `YYYY-MM-DD` form.", values)
            return
        if date.fromisoformat(values["due_date"]) < self.host.class_today(self.guild_id):
            await self._invalid(interaction, "That date has already passed in this server’s timezone. "
                                "Choose today or a future date.", values)
            return
        if not self.host.rate_limiter.allow("deadline", self.guild_id, self.requester_id):
            await interaction.response.send_message("You’ve added several deadlines recently. "
                                                     "Try again in a few minutes.", ephemeral=True)
            return
        await interaction.response.defer(thinking=True)
        async with self._lock:
            if self.saved:
                await interaction.followup.send("This form was already saved. Check `/due`.")
                return
            pending_link = f"manual:{interaction.id}"
            store = self.host.deadline_store()
            self.saved = await asyncio.to_thread(
                store.save, values["summary"], values["due_date"], interaction.user.display_name,
                self.channel_id, pending_link, values["audience"], self.guild_id, self.requester_id)
            duplicate = self.saved.action == "duplicate"
            embed = card("Already on the deadline list" if duplicate else "Deadline added",
                         f"**{plain(values['summary'])}**\nDue **{values['due_date']}** "
                         f"· {self.host._timezone_for_guild(self.guild_id)}",
                         color=DEADLINE_AMBER,
                         footer=f"Memlo · #{self.saved.deadline_id} · No AI used")
            if values["audience"]:
                embed.add_field(name="For", value=plain(values["audience"], 100), inline=False)
            embed.add_field(name="What’s next", value=(
                "Exact copies appear once in `/due`. Your contribution is still attributed to you.\n"
                if duplicate else "View it with `/due`. Reminders run automatically before it’s due.\n") +
                "The original contributor or an admin can use `/reschedule` or `/cancel-deadline`.", inline=False)
            embed.add_field(name="Added by", value=plain(interaction.user.display_name), inline=False)
            sent = await interaction.followup.send(embed=embed, wait=True)
            # WebhookMessage.guild may be None even for a guild interaction;
            # its jump_url then incorrectly points into /channels/@me/.
            source_url = f"https://discord.com/channels/{self.guild_id}/{self.channel_id}/{sent.id}"
            await asyncio.to_thread(store.link_source, self.saved.deadline_id, self.guild_id,
                                    self.channel_id, pending_link, sent.id, source_url)
            self.stop()

    async def on_error(self, interaction, error):
        self.host.record_operational_error("add_deadline_form", error, self.guild_id)
        message = ("The deadline was saved, but I couldn’t finish its confirmation. Check `/due`."
                   if self.saved else "I couldn’t save that deadline. Nothing was added; try `/add-deadline` again.")
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


def register_command(host):
    @host.tree.command(name="add-deadline", description="Add a deadline for this class with a form—no AI required")
    @app_commands.guild_only()
    async def add_deadline(interaction: discord.Interaction):
        if not tracked_here(host, interaction.channel, interaction.guild_id):
            await interaction.response.send_message(
                "Use this in a tracked class channel. An admin can choose one with `/setup`.", ephemeral=True)
            return
        await interaction.response.send_modal(AddDeadlineModal(
            host, interaction.guild_id, interaction.channel_id, interaction.user.id))

    return add_deadline
