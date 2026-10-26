"""Click-through onboarding and friendly, DST-aware timezone search."""

import asyncio
from zoneinfo import ZoneInfo, available_timezones

import discord
from discord import app_commands


COMMON_ZONES = (
    ("America/New_York", "New York · Eastern US / Canada"),
    ("America/Chicago", "Chicago · Central US / Canada"),
    ("America/Denver", "Denver · Mountain US / Canada"),
    ("America/Los_Angeles", "Los Angeles · Pacific US / Canada"),
    ("America/Phoenix", "Phoenix · Arizona"),
    ("America/Anchorage", "Anchorage · Alaska"),
    ("Pacific/Honolulu", "Honolulu · Hawaii"),
    ("America/Toronto", "Toronto · Eastern Canada"),
    ("America/Sao_Paulo", "São Paulo · Brazil"),
    ("Europe/London", "London · United Kingdom"),
    ("Europe/Paris", "Paris · Central Europe"),
    ("Europe/Berlin", "Berlin · Central Europe"),
    ("Europe/Helsinki", "Helsinki · Eastern Europe"),
    ("Africa/Lagos", "Lagos · Nigeria"),
    ("Africa/Johannesburg", "Johannesburg · South Africa"),
    ("Africa/Cairo", "Cairo · Egypt"),
    ("Asia/Dubai", "Dubai · UAE"),
    ("Asia/Kolkata", "Kolkata · India"),
    ("Asia/Dhaka", "Dhaka · Bangladesh"),
    ("Asia/Singapore", "Singapore"),
    ("Asia/Shanghai", "Shanghai · China"),
    ("Asia/Tokyo", "Tokyo · Japan"),
    ("Australia/Sydney", "Sydney · Australia"),
    ("Pacific/Auckland", "Auckland · New Zealand"),
    ("UTC", "UTC · Coordinated Universal Time"),
)
ZONE_LABELS = dict(COMMON_ZONES)
ALL_ZONES = tuple(sorted(available_timezones()))


def timezone_choices(query, preferred=None):
    """Keep Discord's 25-choice limit while allowing every installed IANA zone."""
    query = " ".join(query.casefold().replace("_", " ").split())
    zones = list(dict.fromkeys(([preferred] if preferred in ALL_ZONES else [])
                               + list(ZONE_LABELS) + list(ALL_ZONES)))
    if query:
        zones = [zone for zone in zones if query in
                 f"{zone.replace('_', ' ')} {ZONE_LABELS.get(zone, '')}".casefold()]
        zones.sort(key=lambda zone: (zone.casefold() != query,
                                    not zone.rsplit("/", 1)[-1].replace("_", " ").casefold().startswith(query)))
    return [app_commands.Choice(name=f"{ZONE_LABELS.get(zone, zone.replace('_', ' '))} — {zone}"[:100],
                                value=zone) for zone in zones[:25]]


class SetupView(discord.ui.View):
    def __init__(self, requester_id, timezone_name, scope, finish):
        super().__init__(timeout=180)
        self.requester_id = requester_id
        self.timezone_name = timezone_name
        self.scope = scope
        self.finish = finish
        self.message = None
        self.completed = False
        self.lock = asyncio.Lock()
        zones = list(COMMON_ZONES)
        if timezone_name not in ZONE_LABELS:
            zones = [(timezone_name, timezone_name.replace("_", " "))] + zones[:24]
        self.zone_menu = discord.ui.Select(
            placeholder="Choose your class timezone", row=0,
            options=[discord.SelectOption(label=label, value=zone,
                                          description=zone, default=zone == timezone_name)
                     for zone, label in zones])
        self.zone_menu.callback = self.choose_timezone
        self.add_item(self.zone_menu)

    def prompt(self):
        return (f"**Set up Memlo for {self.scope}**\n"
                f"Timezone: **{ZONE_LABELS.get(self.timezone_name, self.timezone_name)}** "
                f"(`{self.timezone_name}`)\n"
                "Pick your class timezone below, then press **Start Memlo**. "
                "Nothing is tracked until you confirm.\n"
                "City missing? Run `/setup` with the `timezone_name` option and search "
                "for your city. Daylight saving changes are handled automatically.")

    async def interaction_check(self, interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Run `/setup` to open your own setup menu.", ephemeral=True)
            return False
        if not getattr(getattr(interaction.user, "guild_permissions", None), "manage_guild", False):
            await interaction.response.send_message("You need **Manage Server** permission to finish setup.", ephemeral=True)
            return False
        return True

    async def choose_timezone(self, interaction):
        self.timezone_name = self.zone_menu.values[0]
        for option in self.zone_menu.options:
            option.default = option.value == self.timezone_name
        await interaction.response.edit_message(content=self.prompt(), view=self)

    @discord.ui.button(label="Start Memlo", style=discord.ButtonStyle.success, row=1)
    async def confirm(self, interaction, button):
        async with self.lock:
            if self.completed:
                await interaction.response.send_message("This setup is already complete. Use `/setup-status`.", ephemeral=True)
                return
            if not await self.interaction_check(interaction):
                return
            # Validate again before saving, including if a custom default was supplied.
            ZoneInfo(self.timezone_name)
            success = await self.finish(interaction, self.timezone_name)
            if success:
                self.completed = True
                for child in self.children:
                    child.disabled = True
                if self.message:
                    await self.message.edit(view=self)
                self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, row=1)
    async def cancel(self, interaction, button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="Setup cancelled. Your settings are unchanged.", view=self)
        self.stop()

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(content="This setup menu expired. Run `/setup` to try again. Nothing was changed by this menu.", view=self)
            except discord.HTTPException:
                pass
