"""
roll.py
Command for rolling dice with expressions like '4d6', '1d20+5', etc.
Supports complex expressions with multiple dice types and mathematical operations.
"""

import re
import random
from typing import List, Tuple, Optional
from discord import app_commands
from discord.ext import commands
import discord
from bot.app.utils.logger import get_logger

logger = get_logger()


class DiceRoller:
    """Handles parsing and evaluation of dice expressions."""
    
    def __init__(self):
        # Pattern to match dice notation like "3d6", "1d20", "d4" (implied 1d4)
        self.dice_pattern = re.compile(r'(\d*)d(\d+)', re.IGNORECASE)
    
    def roll_die(self, sides: int) -> int:
        """Roll a single die with the specified number of sides."""
        if sides <= 0:
            raise ValueError(f"Invalid number of sides: {sides}")
        return random.randint(1, sides)
    
    def roll_dice(self, count: int, sides: int) -> Tuple[List[int], int]:
        """Roll multiple dice and return individual results and sum."""
        if count <= 0:
            raise ValueError(f"Invalid number of dice: {count}")
        if count > 100:  # Reasonable limit to prevent spam
            raise ValueError(f"Too many dice (max 100): {count}")
        if sides > 1000:  # Reasonable limit for sides
            raise ValueError(f"Die has too many sides (max 1000): {sides}")
            
        rolls = [self.roll_die(sides) for _ in range(count)]
        return rolls, sum(rolls)
    
    def parse_and_roll(self, expression: str) -> Tuple[str, int, str]:
        """
        Parse a dice expression and return the result.
        Returns: (detailed_breakdown, total, original_expression)
        """
        if not expression.strip():
            expression = "1d20"  # Default to d20
        
        original_expr = expression.strip()
        
        # Handle shorthand like "d20" -> "1d20"
        expression = re.sub(r'\bd(\d+)', r'1d\1', expression, flags=re.IGNORECASE)
        
        # Find all dice expressions
        dice_matches = list(self.dice_pattern.finditer(expression))
        
        if not dice_matches:
            raise ValueError(f"No valid dice notation found in: {original_expr}")
        
        # Replace dice expressions with their results
        result_expression = expression
        breakdown_parts = []
        
        # Process matches in reverse order to avoid position shifting
        for match in reversed(dice_matches):
            count_str, sides_str = match.groups()
            count = int(count_str) if count_str else 1
            sides = int(sides_str)
            
            rolls, dice_sum = self.roll_dice(count, sides)
            
            # Format the breakdown
            if count == 1:
                breakdown = f"d{sides}: {rolls[0]}"
            else:
                rolls_str = ', '.join(map(str, rolls))
                breakdown = f"{count}d{sides}: [{rolls_str}] = {dice_sum}"
            
            breakdown_parts.insert(0, breakdown)
            
            # Replace the dice notation with the sum
            result_expression = result_expression[:match.start()] + str(dice_sum) + result_expression[match.end():]
        
        # Evaluate the mathematical expression
        try:
            # Simple safety check - only allow numbers, operators, and parentheses
            safe_expr = re.sub(r'[^0-9+\-*/() ]', '', result_expression)
            if not safe_expr:
                raise ValueError("Invalid expression after parsing dice")
            
            total = eval(safe_expr)
            if not isinstance(total, (int, float)):
                raise ValueError("Expression did not evaluate to a number")
            total = int(total)
            
        except Exception as e:
            raise ValueError(f"Failed to evaluate expression '{result_expression}': {e}")
        
        detailed_breakdown = " | ".join(breakdown_parts)
        
        return detailed_breakdown, total, original_expr


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