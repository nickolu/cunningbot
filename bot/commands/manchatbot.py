"""
ManchatBot.py
Command handler for chat functionality.
"""

import discord
from discord import app_commands
from discord.ext import commands
from typing import List
from langchain_core.messages.base import BaseMessage
from langchain_core.messages import HumanMessage, AIMessage
from bot.core.llm_client import LLMClient
from bot.core.logger import get_logger

logger = get_logger()

import os
print(f"Loaded token: '{os.getenv('DISCORD_TOKEN')}'")

class ManchatBot(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.llm = LLMClient.factory()

    @app_commands.command(name="manchatbot", description="Chat with the ManchatBot LLM")
    @app_commands.describe(input_text="Your message for the chatbot")
    async def manchatbot(self, interaction: discord.Interaction, input_text: str) -> None:
        author_id = interaction.user.id
        channel_id = interaction.channel_id
        logger.info({
            "event": "chat_command_invoked",
            "author_id": author_id,
            "channel_id": channel_id,
            "input_text": input_text
        })

        # Retrieve last 20 messages from the channel
        history: List[BaseMessage] = []
        if isinstance(interaction.channel, discord.TextChannel):
            async for message in interaction.channel.history(limit=20, oldest_first=False):
                content = str(message.content) if message.content is not None else ""
                if message.author.bot:
                    history.append(AIMessage(content=content))
                else:
                    history.append(HumanMessage(content=content))
        history.reverse()  # Oldest first for LLM context
        # Add the current user input as the last message
        history.append(HumanMessage(content=input_text))

        # Convert messages to dicts as expected by LLMClient
        messages = []
        for msg in history:
            if isinstance(msg, HumanMessage):
                messages.append({"role": "user", "content": msg.content})
            elif isinstance(msg, AIMessage):
                messages.append({"role": "assistant", "content": msg.content})
            else:
                logger.warning({"event": "unexpected_message_type", "type": str(type(msg))})

        try:
            response = await self.llm.chat(history)
        except Exception as e:
            print("Exception: ", e)
            logger.error({
                "event": "llm_error",
                "error": str(e),
                "author_id": author_id,
                "channel_id": channel_id
            })
            try:
                await interaction.response.send_message("An error occurred while generating a response.", ephemeral=True)
            except Exception:
                pass  # Interaction may have expired
            return

        await interaction.response.send_message(response)

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ManchatBot(bot))
