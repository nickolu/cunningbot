"""
roll.py
Command for rolling dice with expressions like '4d6', '1d20+5', etc.
Supports complex expressions with multiple dice types and mathematical operations.
"""

from typing import Optional
from discord import app_commands
from discord.ext import commands
import discord
from bot.app.utils.logger import get_logger
from bot.domain.dice import DiceRoller

logger = get_logger()


class DiceCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.dice_roller = DiceRoller()

    @app_commands.command(name="roll", description="Roll dice using expressions like '4d6', '1d20+5', or just 'd20'. Defaults to 1d20.")
    @app_commands.describe(
        r="Dice expression (e.g., '4d6', '1d20+3d4*10', 'd20'). Leave blank for 1d20."
    )
    async def roll(
        self, 
        interaction: discord.Interaction, 
        r: Optional[str] = None
    ) -> None:
        """Roll dice based on the provided expression."""
        try:
            # Default to d20 if no expression provided
            expression = r if r is not None else "1d20"
            
            # Parse and roll the dice
            breakdown, total, original = self.dice_roller.parse_and_roll(expression)
            
            # Format the response
            if original.lower() in ["1d20", "d20"] and r is None:
                # Simple default case
                response = f"🎲 {interaction.user.mention} rolled a **d20**: **{total}**"
            else:
                # Complex expression
                response = (
                    f"🎲 {interaction.user.mention} rolled `{original}`:\n"
                    f"**Result:** {breakdown}\n"
                    f"**Total:** **{total}**"
                )
            
            await interaction.response.send_message(response)
            
        except ValueError as e:
            error_msg = f"❌ {interaction.user.mention}: {str(e)}"
            await interaction.response.send_message(error_msg, ephemeral=True)
        except Exception as e:
            logger.error(f"Unexpected error in dice roll command: {e}")
            error_msg = f"❌ {interaction.user.mention}: An unexpected error occurred while rolling dice."
            await interaction.response.send_message(error_msg, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DiceCog(bot)) 