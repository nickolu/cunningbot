# bot/commands/personality.py
import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional

from bot.core.settings.personality_service import get_personality, set_personality

class PersonalityCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="personality",
        description="Sets or gets the bot's current personality. Leave text empty to clear."
    )
    @app_commands.describe(
        text="The personality description (e.g., 'a helpful assistant'). Max 200 chars. Type 'get' to view current."
    )
    async def set_personality(self, interaction: discord.Interaction, text: Optional[str] = None) -> None:
        """Sets, gets, or clears the bot's personality for chat interactions."""
        if text and text.lower() == "get":
            current_pers = get_personality()
            if current_pers:
                await interaction.response.send_message(
                    f"Current personality: '{current_pers}'", 
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "No personality is currently set.", 
                    ephemeral=True
                )
            return
        
        try:
            set_personality(text)
            if text:
                processed_text = get_personality()
                await interaction.response.send_message(
                    f"Personality set to: '{processed_text}'. I will now try to act this way in chat.", 
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "Personality cleared. I will revert to my default behavior.", 
                    ephemeral=True
                )
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(
                "An unexpected error occurred while setting the personality.", 
                ephemeral=True
            )

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PersonalityCog(bot))
