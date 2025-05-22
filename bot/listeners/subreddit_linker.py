"""
subreddit_linker.py
Listener for subreddit linking events.
"""

import re

import discord
from discord.ext import commands

from bot.domain.logger import get_logger

logger = get_logger()

class SubredditLinker(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Regex pattern to match subreddit mentions (r/subredditname)
        self.subreddit_pattern = re.compile(r'\br/([a-zA-Z0-9_]{3,21})\b')

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        # Skip messages from bots
        if message.author.bot:
            return
            
        # Look for subreddit mentions
        subreddits = self.subreddit_pattern.findall(message.content)
        if not subreddits:
            return
            
        # Log the event
        logger.info({
            "event": "subreddit_mentioned",
            "author_id": message.author.id,
            "channel_id": message.channel.id,
            "subreddits": subreddits
        })
        
        # Deduplicate subreddits
        unique_subreddits = list(set(subreddits))
        
        # Create links for each subreddit
        links = []
        for subreddit in unique_subreddits:
            links.append(f"https://reddit.com/r/{subreddit}")
        
        # Only respond if there are valid links
        if links:
            response = "Subreddit links: " + " | ".join(links)
            await message.channel.send(response)

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SubredditLinker(bot))
