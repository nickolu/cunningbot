"""
Chat.py
Command handler for chat functionality.
"""

import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional

from bot.core.chat.chat_service import chat_service
from bot.core.chat_completions_client import ChatCompletionsClient, PermittedModelType
from bot.core.settings.personality_service import get_personality
from bot.core.logger import get_logger
from bot.services.openai.utils import sanitize_name
from bot.utils import split_message
from bot.services.discord.utils import flatten_discord_message

logger = get_logger()

class SummarizeCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.llm = ChatCompletionsClient.factory()

    @app_commands.command(name="summarize", description="Summarize a conversation or text")
    @app_commands.describe(message_count="Number of previous messages to include (default: 20)")
    @app_commands.choices(
        private=[
            app_commands.Choice(name="True", value=1),
            app_commands.Choice(name="False", value=0),
        ]
    )
    @app_commands.choices(  
        model=[
            app_commands.Choice(name="gpt-3.5-turbo (cheapest)", value="gpt-3.5-turbo"),
            app_commands.Choice(name="gpt-4.1-nano", value="gpt-4.1-nano"),
            app_commands.Choice(name="gpt-4o-mini (default)", value="gpt-4o-mini"),
            app_commands.Choice(name="gpt-4.1-mini", value="gpt-4.1-mini"),
            app_commands.Choice(name="o4-mini", value="o4-mini"),
            app_commands.Choice(name="gpt-4.1", value="gpt-4.1"),
            app_commands.Choice(name="gpt-4o", value="gpt-4o"),
            app_commands.Choice(name="gpt-4-turbo", value="gpt-4-turbo"),
            app_commands.Choice(name="gpt-4", value="gpt-4"),
            app_commands.Choice(name="gpt-4.5-preview (most expensive)", value="gpt-4.5-preview"),
            
        ]
    )
    async def summarize(self, interaction: discord.Interaction, model: Optional[PermittedModelType] = None, message_count: Optional[int] = 20, private: Optional[int] = 0) -> None:
        was_default = False
        if model is None:
            model = "gpt-4o-mini"
            was_default = True

        name = interaction.user.display_name
        author_id = interaction.user.id
        channel_id = interaction.channel_id
        log_payload = {
            "event": "summarize_command_invoked",
            "name": name,
            "author_id": author_id,
            "channel_id": channel_id,
            "model": model,
            "was_default": was_default,
            "message_count": message_count,
            "private": private,
        }
        logger.info(log_payload)

        await interaction.response.defer(thinking=True, ephemeral=bool(private))

        # Retrieve last messages from the channel based on message_count
        history = []
        if isinstance(interaction.channel, discord.TextChannel):
            async for message in interaction.channel.history(limit=message_count, oldest_first=False):
                content = flatten_discord_message(message)
                author_name = sanitize_name(message.author.display_name)
                if message.author.bot:
                    history.append({"role": "assistant", "content": content, "name": author_name})
                else:
                    history.append({"role": "user", "content": content, "name": author_name})
        history.reverse()  # Oldest first for LLM context

        model_text = "\n_model: " + model + "_"
        history_text = "\n".join([f"{msg['name']}: {msg['content']}" for msg in history])
        response = await chat_service(
            "You are summarizing a conversation in a discord channel. Please summarize the conversation, making sure to mention each user in the thread. Here is the content of the conversation: \n\n" + history_text,
            model,
            interaction.user.display_name,
            get_personality(),
            []
        )

        try:
            for chunk in split_message(response):
                await interaction.followup.send(chunk, ephemeral=bool(private))
            if not was_default:
                await interaction.followup.send(model_text, ephemeral=bool(private))
        except Exception:
            logger.exception("Failed to send followup messages in summarize command.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SummarizeCog(bot))
