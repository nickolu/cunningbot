"""Routes clicks on the agent's suggested-reply buttons.

The buttons and what a click does live in bot/app/suggested_replies.py; this
extension only registers them with the bot when it loads, so clicks on buttons
posted before a restart still reach the handler.
"""

from discord.ext import commands

from bot.app.suggested_replies import register_suggestion_buttons


async def setup(bot: commands.Bot) -> None:
    register_suggestion_buttons(bot)
