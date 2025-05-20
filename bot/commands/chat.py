"""
Chat.py
Command handler for chat functionality.
"""

import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional

from bot.domain.chat.chat_service import chat_service
from bot.api.openai.chat_completions_client import ChatCompletionsClient, PermittedModelType
from bot.domain.settings.personality_service import get_personality
from bot.domain.logger import get_logger
from bot.api.openai.utils import sanitize_name
from bot.utils import split_message
from bot.api.discord.utils import flatten_discord_message

logger = get_logger()

class ChatCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.llm = ChatCompletionsClient.factory()

    @app_commands.command(name="chat", description="Chat with the ManchatBot LLM")
    @app_commands.describe(msg="Your message for the chatbot", message_count="Number of previous messages to include (default: 20)")
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
    async def chat(self, interaction: discord.Interaction, msg: str, model: Optional[PermittedModelType] = None, message_count: Optional[int] = 20, private: Optional[int] = 0) -> None:
        # Defer the response immediately to prevent interaction timeout
        await interaction.response.defer(thinking=True, ephemeral=bool(private))
        
        try:
            # Set defaults and convert types
            was_default = False
            if model is None:
                model = "gpt-4o-mini"
                was_default = True
            
            private = bool(private)
            
            # Log the interaction
            name = interaction.user.display_name
            author_id = interaction.user.id
            channel_id = interaction.channel_id
            log_payload = {
                "event": "chat_command_invoked",
                "author_id": author_id,
                "channel_id": channel_id,
                "msg": msg,
                "model": model,
                "was_default": was_default,
                "message_count": message_count,
                "private": private,
            }
            logger.info(log_payload)
            
            # Retrieve last messages from the channel based on message_count
            history = []

            if isinstance(interaction.channel, discord.TextChannel):
                async for message in interaction.channel.history(limit=message_count, oldest_first=False):
                    content = flatten_discord_message(message)
                    author_name = sanitize_name(message.author.display_name) # Sanitized name
                    if message.author.bot:
                        history.append({"role": "assistant", "content": content, "name": author_name})
                    else:
                        history.append({"role": "user", "content": content, "name": author_name})
            
            history.reverse()  # Oldest first for LLM context  

            model_text = "\n_model: " + model +"_"

            # Get response from LLM
            response = await chat_service(msg, model, interaction.user.display_name, get_personality(), history)
            
            response = f"{interaction.user.mention}: \n> {msg}\n\n{response}"
            # Prepare the response text
            if not was_default:
                response = f"{response}\n{model_text}"
            
            # Split the response into chunks of 2000 characters or less
            chunks = split_message(response)
            
            # Send the first chunk as a follow-up to the deferred interaction
            try:
                await interaction.followup.send(chunks[0], ephemeral=private)
                
                # Send remaining chunks as regular messages
                for chunk in chunks[1:]:
                    try:
                        await interaction.followup.send(chunk, ephemeral=private)
                    except discord.errors.NotFound as e:
                        logger.error(f"Error sending followup response: {str(e)}")
                        if interaction.channel and isinstance(interaction.channel, discord.TextChannel):
                            await interaction.channel.send(f"I had trouble sending my response. Here's a shorter version:\n{chunk}")
                            return
                            
            except Exception as e:
                logger.error(f"Error sending initial response: {str(e)}")
                if interaction.channel and isinstance(interaction.channel, discord.TextChannel):
                    await interaction.channel.send(f"{str(e)}\n{response[:1500]}...")

                    
        except Exception as e:
            logger.error(f"Error in chat command: {str(e)}")
            try:
                # Try to send an error message if we haven't responded yet
                if not interaction.response.is_done():
                    await interaction.response.send_message("Sorry, an error occurred while processing your request.", ephemeral=True)
                else:
                    await interaction.followup.send("Sorry, an error occurred while processing your request.", ephemeral=True)
            except Exception as inner_e:
                logger.error(f"Failed to send error message: {str(inner_e)}")
                pass

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ChatCog(bot))
