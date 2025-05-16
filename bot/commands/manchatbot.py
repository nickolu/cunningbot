"""
ManchatBot.py
Command handler for chat functionality.
"""

import discord
from discord import app_commands
from discord.ext import commands
from typing import List, Optional
from langchain_core.messages.base import BaseMessage
from langchain_core.messages import HumanMessage, AIMessage
from bot.core.llm_client import LLMClient, PermittedModelType
from bot.core.logger import get_logger
import os
import re

logger = get_logger()

print(f"Loaded token: '{os.getenv('DISCORD_TOKEN')}'")

# Helper function to sanitize names for OpenAI API
def sanitize_name(name: str) -> str:
    """Sanitizes a name to conform to OpenAI's required pattern and length."""
    if not name: # Handle empty input name
        return "unknown_user" # Default for empty name

    # Replace disallowed characters (whitespace, <, |, \, /, >) with underscore
    sanitized = re.sub(r"[\s<|\\/>]+", "_", name)
    
    # If sanitization results in an empty string (e.g., name was only disallowed chars), provide a default
    if not sanitized:
        return "unknown_user"
        
    # Ensure name is not longer than 64 characters
    return sanitized[:64]


class ManchatBot(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.llm = LLMClient.factory()

    @app_commands.command(name="manchatbot", description="Chat with the ManchatBot LLM")
    @app_commands.describe(input_text="Your message for the chatbot", message_count="Number of previous messages to include (default: 20)")
    @app_commands.choices(  
        model=[
            app_commands.Choice(name="gpt-4o-mini (default)", value="gpt-4o-mini"),
            app_commands.Choice(name="gpt-4o", value="gpt-4o"),
            app_commands.Choice(name="gpt-4-turbo", value="gpt-4-turbo"),
            app_commands.Choice(name="gpt-3.5-turbo", value="gpt-3.5-turbo"),
            app_commands.Choice(name="o4-mini", value="o4-mini"),
        ]
    )
    async def manchatbot(self, interaction: discord.Interaction, input_text: str, model: Optional[PermittedModelType] = None, message_count: Optional[int] = 20) -> None:
        was_default = False
        if model is None:
            model = "gpt-4o-mini"
            was_default = True
        
        author_id = interaction.user.id
        channel_id = interaction.channel_id
        log_payload = {
            "event": "chat_command_invoked",
            "author_id": author_id,
            "channel_id": channel_id,
            "input_text": input_text,
            "model": model,
            "was_default": was_default,
            "message_count": message_count
        }
        logger.info(log_payload)

        # Retrieve last messages from the channel based on message_count
        history: List[BaseMessage] = []
        if isinstance(interaction.channel, discord.TextChannel):
            async for message in interaction.channel.history(limit=message_count, oldest_first=False):
                content = str(message.content) if message.content is not None else ""
                author_name = sanitize_name(message.author.display_name) # Sanitized name
                if message.author.bot:
                    history.append(AIMessage(content=content, name=author_name))
                else:
                    history.append(HumanMessage(content=content, name=author_name))
        history.reverse()  # Oldest first for LLM context
        # Add the current user input as the last message
        current_user_name = sanitize_name(interaction.user.display_name) # Sanitized name
        history.append(HumanMessage(content=input_text, name=current_user_name))

        # Convert messages to dicts as expected by LLMClient
        messages = []
        for msg in history:
            if isinstance(msg, HumanMessage):
                messages.append({"role": "user", "content": msg.content, "name": msg.name})
            elif isinstance(msg, AIMessage):
                messages.append({"role": "assistant", "content": msg.content, "name": msg.name})
            else:
                logger.warning({"event": "unexpected_message_type", "type": str(type(msg))})
        print(messages)

        await interaction.response.defer()
        try:
            # Use the specified model if provided, otherwise use the default
            current_llm = LLMClient.factory(model=model)
            response = await current_llm.chat(history)
            model_text = "" if was_default else  "\n_model: " + model +"_"
            formatted_response = f"**You:** {input_text}\n**ManchatBot:** {response}{model_text}"
            await interaction.followup.send(formatted_response)
        except Exception as e:
            print("Exception: ", e)
            logger.error({
                "event": "llm_error",
                "error": str(e),
                "author_id": author_id,
                "channel_id": channel_id,
                "model": model,
                "was_default": was_default
            })
            try:
                await interaction.followup.send("An error occurred while generating a response.", ephemeral=True)
            except Exception:
                pass  # Interaction may have expired
            return

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ManchatBot(bot))
