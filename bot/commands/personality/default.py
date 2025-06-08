# bot/commands/personality/default.py
import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional

from bot.domain.app_state import get_default_persona, set_default_persona
from bot.domain.chat.chat_personas import CHAT_PERSONAS

class PersonalityDefaultCog(commands.Cog):
    """Cog for managing bot settings."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    settings_group = app_commands.Group(name="persona", description="Manage bot default persona.")

    @settings_group.command(
        name="default",
        description="Set the default persona for the bot in this guild."
    )
    @app_commands.describe(
        persona="Choose a persona to set as default for this guild. Leave empty to view current default."
    )
    @app_commands.choices(
        persona=[
            app_commands.Choice(name=CHAT_PERSONAS[key]["name"], value=key) 
            for key in CHAT_PERSONAS.keys()
        ]
    )
    async def set_default_persona_setting(self, interaction: discord.Interaction, persona: Optional[str] = None) -> None:
        """Manages the bot's default persona setting for this guild."""
        if persona is None:
            # Show current default persona
            try:
                current_persona = get_default_persona(interaction.guild_id)
                if current_persona and current_persona in CHAT_PERSONAS:
                    persona_info = CHAT_PERSONAS[current_persona]
                    await interaction.response.send_message(
                        f"**Current default persona:** {persona_info['name']}\n"
                        f"**Description:** {persona_info.get('instructions') or persona_info.get('personality', 'No description available')}",
                        ephemeral=False
                    )
                else:
                    await interaction.response.send_message(
                        f"Current default persona: '{current_persona}' (fallback - persona not found in available personas)",
                        ephemeral=False
                    )
            except Exception as e:
                if "No app state configured for guild" in str(e):
                    await interaction.response.send_message(
                        f"❌ **Guild not configured**\n"
                        f"This guild is not configured for bot settings. Using global default: **{CHAT_PERSONAS['discord_user']['name']}**\n\n"
                        f"Contact the bot administrator to configure this guild for custom settings.",
                        ephemeral=True
                    )
                else:
                    await interaction.response.send_message(f"Error retrieving current persona: {str(e)}", ephemeral=True)
            return

        try:
            if persona not in CHAT_PERSONAS:
                await interaction.response.send_message(f"Error: Unknown persona '{persona}'. Please choose from the available options.", ephemeral=True)
                return
                
            set_default_persona(persona, interaction.guild_id)
            persona_info = CHAT_PERSONAS[persona]
            await interaction.response.send_message(
                f"✅ **Default persona updated!**\n"
                f"**New default:** {persona_info['name']}\n"
                f"**Description:** {persona_info.get('instructions') or persona_info.get('personality', 'No description available')}\n\n"
                f"This persona will now be used by default for all chat commands in this {'guild' if interaction.guild else 'DM'}.",
                ephemeral=False
            )
        except ValueError as e:
            if "No app state configured for guild" in str(e):
                await interaction.response.send_message(
                    f"❌ **Guild not configured**\n"
                    f"This guild is not configured for bot settings. Please contact the bot administrator to configure this guild.\n\n"
                    f"Error: {str(e)}",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(f"Error: {str(e)}", ephemeral=True)
        except Exception as e:
            # Consider logging the error e
            await interaction.response.send_message("An unexpected error occurred while updating the default persona setting.", ephemeral=True)

    @settings_group.command(
        name="list",
        description="List all available personas that can be set as default."
    )
    async def list_personas(self, interaction: discord.Interaction) -> None:
        """Lists all available personas."""
        persona_list = []
        for key, persona_data in CHAT_PERSONAS.items():
            name = persona_data['name']
            description = persona_data.get('instructions') or persona_data.get('personality', 'No description')
            # Truncate long descriptions
            if len(description) > 100:
                description = description[:97] + "..."
            persona_list.append(f"**{name}** (`{key}`)\n{description}")
        
        # Get current default with error handling
        try:
            current_default = get_default_persona(interaction.guild_id)
            current_name = CHAT_PERSONAS.get(current_default, {}).get('name', current_default) if current_default else 'Unknown'
            current_status = f"**Current default for this {'guild' if interaction.guild else 'DM'}:** {current_name}"
        except Exception as e:
            if "No app state configured for guild" in str(e):
                current_status = f"**This guild is not configured** - using global default: **{CHAT_PERSONAS['discord_user']['name']}**"
            else:
                current_status = f"**Error getting current default:** {str(e)}"
        
        embed = discord.Embed(
            title="Available Personas",
            description=f"{current_status}\n\n" + "\n\n".join(persona_list),
            color=0x00ff00
        )
        embed.set_footer(text="Use /persona default <persona> to set a new default for this guild")
        
        await interaction.response.send_message(embed=embed, ephemeral=False)

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PersonalityDefaultCog(bot))
