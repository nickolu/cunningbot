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
from bot.api.discord.utils import flatten_discord_message, format_response_with_interaction_user_message, to_tiny_text
from bot.core.task_queue import get_task_queue

logger = get_logger()

class ChatCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.llm = ChatCompletionsClient.factory()

    async def _chat_handler(self, interaction: discord.Interaction, msg: str, model: Optional[PermittedModelType] = None, message_count: Optional[int] = 20, private: Optional[int] = 0, already_responded: bool = False) -> None:
        """Internal chat handler that processes the actual chat request"""
        # Only defer if we haven't already responded to the interaction
        if not already_responded and not interaction.response.is_done():
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

            meta_data =[]
            if not was_default:
                meta_data.append(to_tiny_text(model))
            
            personality = get_personality()
            if personality:
                meta_data.append(to_tiny_text(personality))

            # Get response from LLM
            response = await chat_service(msg, model, interaction.user.display_name, personality, history)
            response = format_response_with_interaction_user_message(response + "\n\n" + ' • '.join(meta_data), interaction, msg)
            
            # Split the response into chunks of 2000 characters or less
            chunks = split_message(response)
            
            # Send the first chunk as a follow-up to the deferred interaction
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(chunks[0], ephemeral=private)
                else:
                    await interaction.response.send_message(chunks[0], ephemeral=private)
                
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

    @app_commands.command(name="chat", description="Chat with the CunningBot LLM")
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
        """Queue a chat request for processing"""
        try:
            # Get the task queue and enqueue the chat handler
            task_queue = get_task_queue()
            queue_status = task_queue.get_queue_status()
            
            already_responded = False
            
            # If there are tasks in queue, inform the user
            if queue_status["queue_size"] > 0:
                await interaction.response.send_message(
                    f"🕐 Your request has been queued! There are {queue_status['queue_size']} tasks ahead of you. "
                    f"I'll respond as soon as I finish processing the current requests.",
                    ephemeral=True
                )
                already_responded = True
            else:
                # If no queue, defer immediately to avoid "application did not respond"
                await interaction.response.defer(thinking=True, ephemeral=bool(private))
                already_responded = True
            
            # Enqueue the actual chat processing task
            task_id = await task_queue.enqueue_task(
                self._chat_handler, 
                interaction, msg, model, message_count, private, already_responded
            )
            
            logger.info(f"Chat command queued with task ID: {task_id}")
            
        except Exception as e:
            logger.error(f"Error queuing chat command: {str(e)}")
            
            # Check if it's a queue full error
            if "queue is full" in str(e).lower():
                error_message = "🚫 I'm currently at maximum capacity (10 tasks queued). Please wait a moment for some tasks to complete before trying again."
            else:
                error_message = "Sorry, I'm currently overwhelmed with requests. Please try again in a moment."
            
            if not interaction.response.is_done():
                await interaction.response.send_message(error_message, ephemeral=True)
            else:
                await interaction.followup.send(error_message, ephemeral=True)

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ChatCog(bot))
