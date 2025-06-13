import discord
from discord import app_commands
from discord.ext import commands
from typing import Any
from urllib.parse import urlparse

from bot.domain.app_state import (
    set_state_value_from_interaction,
    get_state_value_from_interaction,
)

MINUTE_CHOICES = [0, 10, 20, 30, 40, 50]


def _is_valid_url(url: str) -> bool:
    """Simple URL validation."""
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


class DailyGameCog(commands.Cog):
    """Cog for registering daily game reminders."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    daily_game = app_commands.Group(
        name="daily-game", description="Manage daily scheduled game reminders."
    )

    @daily_game.command(name="register", description="Register a daily game for this channel.")
    @app_commands.describe(
        name="The short name of the game (e.g. framed.wtf)",
        link="A URL players will visit to play the game",
        hour="Hour of day (0–23) to post the game link (Pacific time)",
        minute="Minute of the hour (in increments of 10) to post the game link (Pacific time)",
    )
    @app_commands.choices(
        minute=[app_commands.Choice(name=f"{m:02d}", value=m) for m in MINUTE_CHOICES]
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def register(
        self,
        interaction: discord.Interaction,
        name: str,
        link: str,
        hour: app_commands.Range[int, 0, 23],
        minute: int,
    ) -> None:
        """Registers/updates a daily game reminder for the guild and channel."""
        # Extra safety: confirm minute is in our allowed list (should always be true via choices).
        if minute not in MINUTE_CHOICES:
            await interaction.response.send_message(
                f"Minute must be one of {MINUTE_CHOICES}.", ephemeral=True
            )
            return

        if not _is_valid_url(link):
            await interaction.response.send_message(
                "Please provide a valid URL starting with http:// or https://", ephemeral=True
            )
            return

        # Fetch current games dict
        games: dict[str, Any] | None = get_state_value_from_interaction(
            "daily_games", interaction.guild_id
        )
        if games is None:
            games = {}

        # Ensure uniqueness: if game exists in another channel, error
        if name in games and games[name]["channel_id"] != interaction.channel_id:
            await interaction.response.send_message(
                f"⚠️ A game with the name '{name}' is already registered in another channel. Please choose a different name or unregister the existing one first.",
                ephemeral=True,
            )
            return

        # Build/overwrite game info
        game_info: dict[str, Any] = {
            "name": name,
            "link": link,
            "hour": int(hour),
            "minute": int(minute),
            "channel_id": interaction.channel_id,
            "enabled": True,
        }

        games[name] = game_info

        # Save back to state
        set_state_value_from_interaction("daily_games", games, interaction.guild_id)

        await interaction.response.send_message(
            f"✅ Daily game **{name}** registered! I will post the link <{link}> to {interaction.channel.mention} every day at {int(hour):02d}:{int(minute):02d}.",
            ephemeral=True,
        )

    @daily_game.command(name="enable", description="Enable a registered daily game.")
    @app_commands.describe(name="The name of the registered game to enable")
    @app_commands.checks.has_permissions(administrator=True)
    async def enable_game(self, interaction: discord.Interaction, name: str) -> None:
        games = get_state_value_from_interaction("daily_games", interaction.guild_id) or {}

        if name not in games:
            await interaction.response.send_message(
                f"No registered game named '{name}' found for this guild.", ephemeral=True
            )
            return

        games[name]["enabled"] = True
        set_state_value_from_interaction("daily_games", games, interaction.guild_id)
        await interaction.response.send_message(
            f"✅ The game '{name}' has been enabled.", ephemeral=True
        )

    @daily_game.command(name="disable", description="Disable a registered daily game.")
    @app_commands.describe(name="The name of the registered game to disable")
    @app_commands.checks.has_permissions(administrator=True)
    async def disable_game(self, interaction: discord.Interaction, name: str) -> None:
        games = get_state_value_from_interaction("daily_games", interaction.guild_id) or {}

        if name not in games:
            await interaction.response.send_message(
                f"No registered game named '{name}' found for this guild.", ephemeral=True
            )
            return

        games[name]["enabled"] = False
        set_state_value_from_interaction("daily_games", games, interaction.guild_id)
        await interaction.response.send_message(
            f"🚫 The game '{name}' has been disabled.", ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(DailyGameCog(bot)) 