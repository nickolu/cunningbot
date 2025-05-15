"""
summarize.py
Command handler for summarization functionality.
"""

import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional

from langchain_core.messages import HumanMessage
from bot.core.llm_client import LLMClient
from bot.core.logger import get_logger

logger = get_logger()

class Summarize(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.llm = LLMClient.factory()

    @app_commands.command(name="summarize", description="Summarize a conversation or text")
    @app_commands.describe(
        message_count="Number of recent messages to summarize (default: 10)",
        text="Custom text to summarize instead of messages"
    )
    async def summarize(
        self, 
        interaction: discord.Interaction, 
        message_count: Optional[int] = 10,
        text: Optional[str] = None
    ) -> None:
        await interaction.response.defer(thinking=True)
        
        author_id = interaction.user.id
        channel_id = interaction.channel_id
        logger.info({
            "event": "summarize_command_invoked",
            "author_id": author_id,
            "channel_id": channel_id,
            "message_count": message_count,
            "custom_text": bool(text)
        })

        content_to_summarize = ""
        history: List[BaseMessage] = []
        
        # Either use provided text or gather messages
        if text:
            content_to_summarize = text
        elif isinstance(interaction.channel, discord.TextChannel):
            messages = []
            async for message in interaction.channel.history(limit=message_count):
                author = f"{message.author.display_name}: " if not message.author.bot else ""
                content = message.content if message.content else "[No text content]"
                messages.append(f"{author}{content}")
            
            content_to_summarize = "\n".join(reversed(messages))
        
        if not content_to_summarize:
            await interaction.followup.send("No content to summarize.")
            return
            
        try:
            # Create a prompt for summarization
            system_prompt = "You are a helpful assistant. Summarize the following conversation or text concisely."
            user_prompt = f"Please summarize the following:\n\n{content_to_summarize}"
            
            history = [
                HumanMessage(content=system_prompt),
                HumanMessage(content=user_prompt)
            ]
            
            response = await self.llm.chat(history)
            await interaction.followup.send(response)
            
        except Exception as e:
            logger.error({
                "event": "summarization_error",
                "error": str(e),
                "author_id": author_id,
                "channel_id": channel_id
            })
            await interaction.followup.send("An error occurred while generating the summary.")

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Summarize(bot))
