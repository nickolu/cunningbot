import discord
from discord import app_commands
from discord.ext import commands

from bot.domain.app_state import set_state_value_from_interaction


class DailyFramedCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    daily_framed = app_commands.Group(
        name="daily-framed", description="Commands for Daily Framed"
    )

    @daily_framed.command(
        name="enable", description="Enable Daily Framed in this server."
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def enable(self, interaction: discord.Interaction):
        """Enables Daily Framed."""
        try:
            set_state_value_from_interaction(
                "daily_framed_enabled", True, interaction.guild_id
            )
            set_state_value_from_interaction(
                "daily_framed_channel_id", interaction.channel_id, interaction.guild_id
            )
            await interaction.response.send_message(
                f"Daily Framed has been enabled for this server. Posts will be sent to this channel: {interaction.channel.mention}",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.response.send_message(
                f"An error occurred: {e}", ephemeral=True
            )

    @daily_framed.command(
        name="disable", description="Disable Daily Framed in this server."
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def disable(self, interaction: discord.Interaction):
        """Disables Daily Framed."""
        try:
            set_state_value_from_interaction(
                "daily_framed_enabled", False, interaction.guild_id
            )
            await interaction.response.send_message(
                "Daily Framed has been disabled.", ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(
                f"An error occurred: {e}", ephemeral=True
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(DailyFramedCog(bot))
