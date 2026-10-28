"""Small, requester-owned shortcuts to the real slash-command handlers."""

import discord


class CommandMenu(discord.ui.View):
    def __init__(self, host, requester_id, *, quiz_only=False):
        super().__init__(timeout=180)
        self.requester_id = requester_id
        actions = [("Start quiz", "quiz")]
        if not quiz_only:
            actions = [("Upcoming deadlines", "due"), ("Add deadline", "add-deadline"),
                       ("Study material", "materials"), *actions, ("Setup status", "setup-status")]
        for label, command in actions:
            button = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary)

            async def run(interaction, name=command):
                await host.tree.get_command(name).callback(interaction)

            button.callback = run
            self.add_item(button)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Open your own menu with `/help`.", ephemeral=True)
            return False
        return True
