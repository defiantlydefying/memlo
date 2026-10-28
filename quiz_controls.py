"""Explicit study-set selection, without AI topic guessing."""

import asyncio

import discord
from discord import app_commands

from presentation import clip
from quiz_service import QuizError


class QuizPicker(discord.ui.View):
    """A private, explicit choice; never guess a topic from a mention."""
    def __init__(self, host, requester_id, groups, *, private=True, level=None):
        super().__init__(timeout=180)
        self.host = host
        self.requester_id = requester_id
        self.private = private
        self.level = level
        self.group = None
        self.started = False
        self.material = discord.ui.Select(placeholder="Choose your study set", options=[
            discord.SelectOption(label=clip(item["topics"] or "Untitled set", 100),
                                 description=clip(f"{item['file_count']} files · {item['filenames']}", 100),
                                 value=item["quiz_group"]) for item in groups[:25]])
        self.material.callback = self.choose_material
        self.add_item(self.material)
        self.difficulty = discord.ui.Select(placeholder=f"Difficulty: {level.name if level else 'Mixed'}", options=[
            discord.SelectOption(label=label, value=value) for label, value in
            [("Warm-up", "warm-up: recall and basic understanding"),
             ("Mixed", "mixed: recall and application"),
             ("Challenge", "challenging: reasoning and application")]])
        self.difficulty.callback = self.choose_level
        self.add_item(self.difficulty)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Start your own quiz with `/quiz`.", ephemeral=True)
            return False
        return True

    async def choose_material(self, interaction):
        self.group = self.material.values[0]
        for option in self.material.options:
            option.default = option.value == self.group
        self.start.disabled = False
        await interaction.response.edit_message(view=self)

    async def choose_level(self, interaction):
        self.level = app_commands.Choice(name="Difficulty", value=self.difficulty.values[0])
        for option in self.difficulty.options:
            option.default = option.value == self.level.value
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Start quiz", style=discord.ButtonStyle.primary, disabled=True)
    async def start(self, interaction, button):
        if self.group is None:
            await interaction.response.send_message("Choose a study set first.", ephemeral=True)
            return
        if self.started:
            await interaction.response.send_message("This quiz has already been started. Use `/quiz` for another.", ephemeral=True)
            return
        self.started = True
        try:
            await self.host.tree.get_command("quiz").callback(
                interaction, self.group, self.level, self.private)
        finally:
            self.stop()


def register_command(host):
    async def choices(interaction, current: str):
        groups = await asyncio.to_thread(host.get_material_groups, interaction.channel_id)
        query = current.casefold()
        matches = [group for group in groups if query in (group["topics"] or "").casefold()
                   or query in (group["filenames"] or "").casefold()]
        return [app_commands.Choice(
            name=clip(f"{group['topics'] or 'Untitled set'} · {group['file_count']} "
                      f"{'file' if group['file_count'] == 1 else 'files'} · #{group['newest']}", 100),
            value=group["quiz_group"]) for group in matches[:25]]

    @host.tree.command(name="quiz", description="Practice from a saved study set you choose")
    @app_commands.guild_only()
    @app_commands.describe(study_set="Choose a saved set, or leave blank to open the picker",
                           level="How challenging should the questions be?",
                           private="Only you see the quiz (default). Turn off to share it in the channel.")
    @app_commands.autocomplete(study_set=choices)
    @app_commands.choices(level=[app_commands.Choice(name="Warm-up", value="warm-up: recall and basic understanding"),
                                app_commands.Choice(name="Mixed", value="mixed: recall and application"),
                                app_commands.Choice(name="Challenge", value="challenging: reasoning and application")])
    async def quiz_command(interaction: discord.Interaction, study_set: str = None,
                           level: app_commands.Choice[str] = None, private: bool = True):
        if not host.ai_available():
            await interaction.response.send_message(
                "AI quizzes aren’t enabled on this copy. `/add-deadline`, `/due`, "
                "and deadline reminders work without an API key.", ephemeral=True)
            return
        category = host._category_of(interaction.channel)
        scope = getattr(interaction.channel, "parent_id", None) or interaction.channel_id
        if not host.is_tracked(interaction.guild_id, category.id if category else None, scope):
            await interaction.response.send_message(
                "Use `/quiz` in a tracked class channel. `/setup-status` shows which ones.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=private, thinking=True)
        groups = await asyncio.to_thread(host.get_material_groups, interaction.channel_id)
        if not groups:
            await interaction.followup.send("Your study shelf is empty. Share screenshots, a PDF, "
                                            "a text file, or a study link first.", ephemeral=private)
            return
        if study_set is None:
            await interaction.followup.send(
                "Choose a study set and difficulty, then press **Start quiz**. "
                + ("Only you will see the quiz." if private else "You chose to share the quiz in this channel.")
                + " For older sets beyond this menu, use `/quiz study_set:` to search.",
                view=QuizPicker(host, interaction.user.id, groups, private=private, level=level),
                ephemeral=True)
            return
        else:
            exact = [item for item in groups if item["quiz_group"] == study_set
                     or (item["topics"] or "").casefold() == study_set.casefold()]
            if len(exact) != 1:
                await interaction.followup.send("Choose a study set from the suggestions. "
                                                "I won’t guess between different sets with the same topic.",
                                                ephemeral=private)
                return
            group = exact[0]["quiz_group"]
        if not host.rate_limiter.allow("quiz", interaction.guild_id, interaction.user.id):
            await interaction.followup.send("You’ve made a few quizzes already—try again in five minutes.",
                                            ephemeral=private)
            return
        try:
            request = f"Make a {level.value if level else 'mixed: recall and application'} quiz from this study set."
            prepared = await host.quiz_service.build(request, interaction.channel_id,
                                                      interaction.guild_id, group, allow_notes=False)

            async def send(**kwargs):
                return await interaction.followup.send(**kwargs, ephemeral=private, wait=True)

            await host.quiz_service.publish(prepared, send, interaction.channel_id,
                                             interaction.guild_id, interaction.user.id, private=private)
        except QuizError as exc:
            await interaction.followup.send(str(exc), ephemeral=private)

    return quiz_command
