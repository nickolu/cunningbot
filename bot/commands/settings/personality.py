# bot/commands/settings.py
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot.domain.settings.personality_service import (get_personality,
                                                     set_personality)


class SettingsCog(commands.Cog):
    """Cog for managing bot settings."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    settings_group = app_commands.Group(name="settings", description="Manage bot settings.")

    @settings_group.command(
        name="personality",
        description="Set, get, or clear the bot's personality."
    )
    @app_commands.describe(
        description="Personality description (e.g., 'a helpful assistant'). Leave empty to clear. Type 'get' to view current."
    )
    async def set_personality_setting(self, interaction: discord.Interaction, description: Optional[str] = None) -> None:
        """Manages the bot's personality setting."""
        if description and description.lower() == "get":
            current_pers = get_personality()
            if current_pers:
                await interaction.response.send_message(f"Current personality: '{current_pers}'", ephemeral=False)
            else:
                await interaction.response.send_message("No personality is currently set.", ephemeral=False)
            return

        try:
            set_personality(description)
            if description:
                processed_text = get_personality() # Get the potentially processed text
                await interaction.response.send_message(f"Personality setting updated to: '{processed_text}'.", ephemeral=False)
            else:
                await interaction.response.send_message("Personality setting cleared.", ephemeral=False)
        except ValueError as e:
            await interaction.response.send_message(f"Error: {str(e)}", ephemeral=False)
        except Exception as e:
            # Consider logging the error e
            await interaction.response.send_message("An unexpected error occurred while updating the personality setting.", ephemeral=False)

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SettingsCog(bot))
