import discord
from discord import app_commands
from discord.ext import commands
from typing import Any
from urllib.parse import urlparse

from bot.domain.app_state import set_state_value_from_interaction

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

        # Build game info dict to store in state
        game_info: dict[str, Any] = {
            "name": name,
            "link": link,
            "hour": int(hour),
            "minute": int(minute),
            "channel_id": interaction.channel_id,
        }

        # Store under single key so future features can read whole object
        set_state_value_from_interaction("daily_game", game_info, interaction.guild_id)

        await interaction.response.send_message(
            f"✅ Daily game **{name}** registered! I will post the link <{link}> to {interaction.channel.mention} every day at {int(hour):02d}:{int(minute):02d}.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(DailyGameCog(bot)) 