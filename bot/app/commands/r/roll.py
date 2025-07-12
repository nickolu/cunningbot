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


class SubRedditLinkCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="r", description="Post a link to a subreddit.")
    @app_commands.describe(
        r="Subreddit name (for r/subredditname enter 'subredditname')."
    )
    async def r(
        self, 
        interaction: discord.Interaction, 
        r: Optional[str] = None
    ) -> None:
        await interaction.response.send_message(f"https://www.reddit.com/r/{r}.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SubRedditLinkCog(bot)) 