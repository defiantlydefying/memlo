"""Explicit study-set selection, without AI topic guessing."""

import asyncio

import discord
from discord import app_commands

from presentation import clip
from quiz_service import QuizError


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
    @app_commands.describe(study_set="Choose a study set; defaults to the most recently shared set",
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
            group = groups[0]["quiz_group"]
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
