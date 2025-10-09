"""
image.py
Command for generating images using OpenAI or Google Gemini and saving them to disk.
"""

import asyncio
import random

from typing import Optional
from discord import app_commands
from discord.ext import commands
from bot.api.openai.image_generation_client import ImageGenerationClient
from bot.api.openai.image_edit_client import ImageEditClient
from bot.api.google.image_generation_client import GeminiImageGenerationClient
from bot.api.google.image_edit_client import GeminiImageEditClient
from bot.api.os.file_service import FileService
from bot.app.utils.logger import get_logger
from bot.app.task_queue import get_task_queue
from bot.config import IMAGE_GENERATION_ENABLED, IMAGE_GENERATION_DISABLED_FOR_USERS
import uuid
import discord
from io import BytesIO

logger = get_logger()

class ImageCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.openai_generation_client = ImageGenerationClient.factory()
        self.openai_edit_client = ImageEditClient.factory()

        # Initialize Gemini clients (will only work if GOOGLE_API_KEY is set)
        self.gemini_generation_client = None
        self.gemini_edit_client = None
        try:
            self.gemini_generation_client = GeminiImageGenerationClient.factory()
            self.gemini_edit_client = GeminiImageEditClient.factory()
        except EnvironmentError as e:
            logger.warning(f"Gemini image generation unavailable: {e}")

    async def _image_handler(
        self,
        interaction: discord.Interaction,
        prompt: str,
        attachment: Optional[discord.Attachment] = None,
        size: Optional[str] = None,
        quality: Optional[str] = None,
        background: Optional[str] = None,
        model: Optional[str] = None,
        already_responded: bool = False
    ) -> None:
        """Internal image handler that processes the actual image generation/editing request"""
        try:
            # Only defer if we haven't already responded to the interaction
            if not already_responded and not interaction.response.is_done():
                await interaction.response.defer()

            # Set defaults
            size = size or "auto"
            quality = quality or "auto"
            background = background or "auto"
            model = model or "openai"

            # Validate model selection
            if model == "gemini" and not self.gemini_generation_client:
                error_msg = "Google Gemini model is not available. Please ensure GOOGLE_API_KEY is configured."
                if interaction.response.is_done():
                    await interaction.followup.send(error_msg)
                else:
                    await interaction.response.send_message(error_msg)
                return

            # Select the appropriate clients based on model
            if model == "gemini":
                generation_client = self.gemini_generation_client
                edit_client = self.gemini_edit_client
            else:
                generation_client = self.openai_generation_client
                edit_client = self.openai_edit_client

            final_image_bytes: Optional[bytes] = None
            final_error_message: str = ""
            filename: str = ""
            filepath: str = ""
            action_type: str = ""

            if attachment:
                print(f"Editing image: {attachment.filename}...")
                action_type = "edited"
                try:
                    image_to_edit_bytes = await attachment.read()
                except Exception as e:
                    logger.error(f"Failed to read attachment: {e}")
                    error_msg = f"{interaction.user.mention}: Failed to read the attached image.\n\nError: {e}"
                    if interaction.response.is_done():
                        await interaction.followup.send(error_msg)
                    else:
                        await interaction.response.send_message(error_msg)
                    return

                # For Gemini, check if editing is supported
                if model == "gemini":
                    # Gemini supports editing
                    image_list_or_none, error_msg_edit = await edit_client.edit_image(
                        image=image_to_edit_bytes,
                        prompt=prompt,
                        size=size
                    )
                else:
                    # OpenAI editing
                    image_list_or_none, error_msg_edit = await asyncio.to_thread(
                        edit_client.edit_image,
                        image=image_to_edit_bytes,
                        prompt=prompt,
                        size=size,
                        quality=quality,
                        background=background
                    )
                final_error_message = error_msg_edit
                if image_list_or_none and len(image_list_or_none) > 0:
                    final_image_bytes = image_list_or_none[0] # Use the first image
                
                if not final_image_bytes:
                    # Ensure there's an error message if no image was produced
                    if not final_error_message: final_error_message = "Image editing resulted in no image data."
                    
                    # Check if it's a rate limit error from Gemini
                    if (
                        final_error_message.startswith("RATE_LIMIT:")
                        or "429" in final_error_message
                        or "resource_exhausted" in final_error_message.lower()
                        or "quota" in final_error_message.lower()
                        or final_error_message.strip() == "'error'"
                    ):
                        rate_limit_msg = final_error_message.replace("RATE_LIMIT: ", "")
                        error_msg = (
                            f"⏱️ **Rate Limit Reached**\n\n"
                            f"{interaction.user.mention}, {rate_limit_msg}\n\n"
                            f"*This is not a problem with the bot - Google's API is just experiencing high demand. "
                            f"Your request is valid and will work once the rate limit clears.*\n\n"
                            f"**Your request:**\n"
                            f"• Prompt: *{prompt}*\n"
                            f"• Model: {model.upper()}\n"
                            f"• Attachment: *{attachment.filename}*"
                        )
                    else:
                        error_msg = f"{interaction.user.mention}: Image editing failed\n\nprompt: *{prompt}*\nattachment: *{attachment.filename}*\n\n{final_error_message}"
                    
                    if interaction.response.is_done():
                        await interaction.followup.send(error_msg)
                    else:
                        await interaction.response.send_message(error_msg)
                    return
                
                filename = f"edited_{attachment.filename}_{uuid.uuid4().hex[:8]}.png"
                filepath = f"edited_images/{interaction.user.display_name}/{filename}"

            else:
                print("Generating image...")
                action_type = "generated"
                generated_bytes_or_none, error_msg_gen = await generation_client.generate_image(prompt, size=size)
                final_error_message = error_msg_gen
                final_image_bytes = generated_bytes_or_none

                if not final_image_bytes:
                    logger.error(f"Image operation resulted in None for final_image_bytes. Action: {action_type}, Prompt: {prompt}, Error: {final_error_message}")
                    
                    # Check if it's a rate limit error from Gemini
                    if (
                        final_error_message
                        and (
                            final_error_message.startswith("RATE_LIMIT:")
                            or "429" in final_error_message
                            or ("resource_exhausted" in final_error_message.lower())
                            or ("quota" in final_error_message.lower())
                            or (final_error_message.strip() == "'error'")
                        )
                    ):
                        rate_limit_msg = final_error_message.replace("RATE_LIMIT: ", "")
                        error_msg = (
                            f"⏱️ **Rate Limit Reached**\n\n"
                            f"{interaction.user.mention}, {rate_limit_msg}\n\n"
                            f"*This is not a problem with the bot - Google's API is just experiencing high demand. "
                            f"Your request is valid and will work once the rate limit clears.*\n\n"
                            f"**Your request:**\n"
                            f"• Prompt: *{prompt}*\n"
                            f"• Model: {model.upper()}\n"
                            f"• Size: {size}"
                        )
                    else:
                        error_msg = f"{interaction.user.mention}: An unexpected error occurred while {action_type}ing the image."
                        if final_error_message:
                            error_msg += f"\n\n{final_error_message}"
                    
                    if interaction.response.is_done():
                        await interaction.followup.send(error_msg)
                    else:
                        await interaction.response.send_message(error_msg)
                    return
            
                filename = f"generated_{uuid.uuid4().hex[:8]}.png"
                # Using relative paths, ensure the base directory ('generated_images', 'edited_images')
                # is writable by the application user in the Docker container.
                base_dir = "generated_images"
                filepath = f"{base_dir}/{interaction.user.display_name}/{filename}"

            discord_file_attachment = None
            # Prepare BytesIO object for Discord message from final_image_bytes.
            # This is done before attempting to save, so it's available even if saving fails.
            image_stream = BytesIO(final_image_bytes)
            image_stream.seek(0)
            discord_file_attachment = discord.File(fp=image_stream, filename=filename)

            save_status_message = ""
            try:
                FileService.write_bytes(filepath, final_image_bytes)
                logger.info(f"Image {action_type} and saved: {filepath}")
                # Positive confirmation of saving can be part of the message if desired.
                # For example: save_status_message = f"\nImage saved as `{filepath}`."
            except Exception as e:
                # This 'e' will be the PermissionError from the traceback in your case.
                logger.error(f"Failed to save image to {filepath}: {e}", exc_info=True) # Log with traceback
                error_type = type(e).__name__
                save_status_message = f"\n\n**Warning:** Failed to save image to disk ({error_type}: {e}). The image is still attached to this message."

            # Build message with parameters used
            params_used = []
            params_used.append(f"Model: {model.upper()}")
            if size != "1024x1024" and size != "auto":
                params_used.append(f"Size: {size}")
            if quality != "auto" and model == "openai":
                params_used.append(f"Quality: {quality}")
            if background != "auto" and model == "openai":
                params_used.append(f"Background: {background}")

            params_text = f" ({', '.join(params_used)})" if params_used else ""
            base_message_content = f"Image {action_type} for {interaction.user.mention}:\nPrompt: *{prompt}*{params_text}"
            full_message_content = f"{base_message_content}{save_status_message}"
            
            # Check if we should send donation message (1/20 chance)
            random_number = random.randint(1, 20)
            should_send_donation = random_number == 1
            logger.info(f"Should send donation: {should_send_donation}, random number: {random_number}")

            # Send the result using the appropriate method
            if interaction.response.is_done():
                await interaction.followup.send(
                    content=full_message_content,
                    file=discord_file_attachment
                )
            else:
                await interaction.response.send_message(
                    content=full_message_content,
                    file=discord_file_attachment
                )
            
            # Send donation message as follow-up if selected
            if should_send_donation:
                donation_message = f"Hey {interaction.user.mention}, if you're getting value out of this bot, consider suporting it by donating! (Each image generation costs about $0.25)\n Donate here: https://www.paypal.com/donate/?hosted_button_id=MV6C7HNDU45EU \n\n (this message is sent randomly about 5% of the time)"
                await interaction.followup.send(donation_message, ephemeral=True)
                logger.info(f"Donation message sent to {interaction.user.mention}")
                
        except Exception as e:
            # Catch any unexpected errors and send a friendly message
            error_str = str(e)
            logger.error(f"Unexpected error in _image_handler: {e}", exc_info=True)
            
            # Check if it's a rate limit error
            if (
                "RATE_LIMIT:" in error_str
                or "429" in error_str
                or "Too Many Requests" in error_str
                or error_str.strip() == "'error'"  # Gemini SDK sometimes yields a bare 'error'
                or (model == "gemini")  # Force friendly message for Gemini on unexpected errors
            ):
                error_msg = (
                    f"⏱️ **Rate Limit Reached**\n\n"
                    f"{interaction.user.mention}, Google Gemini is currently experiencing high demand. Please try again in a few moments.\n\n"
                    f"*This is not a problem with the bot - Google's API is just experiencing high demand. "
                    f"Your request is valid and will work once the rate limit clears.*\n\n"
                    f"**Your request:**\n"
                    f"• Prompt: *{prompt}*\n"
                    f"• Model: {model if model else 'OPENAI'}\n"
                    f"• Size: {size if size else 'auto'}"
                )
            else:
                # Generic error message
                error_msg = (
                    f"{interaction.user.mention}, an unexpected error occurred while processing your image request.\n\n"
                    f"**Error:** {error_str}\n\n"
                    f"Please try again or contact support if this persists."
                )
            
            # Send error message
            if interaction.response.is_done():
                await interaction.followup.send(error_msg, ephemeral=True)
            else:
                await interaction.response.send_message(error_msg, ephemeral=True)

    @app_commands.command(name="image", description="Generate or edit an image with OpenAI or Google Gemini.")
    @app_commands.describe(
        prompt="Describe the image you want to generate or the edit you want to make.",
        attachment="Optional: The image to edit.",
        model="AI model to use for generation",
        size="Size of the generated image",
        quality="Quality of the generated image (OpenAI only, editing only)",
        background="Background setting for the generated image (OpenAI only, editing only)"
    )
    @app_commands.choices(
        model=[
            app_commands.Choice(name="OpenAI (GPT Image)", value="openai"),
            app_commands.Choice(name="Google Gemini 2.5 Flash", value="gemini"),
        ]
    )
    @app_commands.choices(
        size=[
            app_commands.Choice(name="Auto", value="auto"),
            app_commands.Choice(name="1024x1024 (Square)", value="1024x1024"),
            app_commands.Choice(name="1536x1024 (Landscape)", value="1536x1024"),
            app_commands.Choice(name="1024x1536 (Portrait)", value="1024x1536"),
        ]
    )
    @app_commands.choices(
        quality=[
            app_commands.Choice(name="Auto", value="auto"),
            app_commands.Choice(name="High", value="high"),
            app_commands.Choice(name="Medium", value="medium"),
            app_commands.Choice(name="Low", value="low"),
        ]
    )
    @app_commands.choices(
        background=[
            app_commands.Choice(name="Auto", value="auto"),
            app_commands.Choice(name="Transparent", value="transparent"),
            app_commands.Choice(name="Opaque", value="opaque"),
        ]
    )
    async def image(
        self,
        interaction: discord.Interaction,
        prompt: str,
        attachment: Optional[discord.Attachment] = None,
        model: Optional[str] = None,
        size: Optional[str] = None,
        quality: Optional[str] = None,
        background: Optional[str] = None
    ) -> None:
        """Queue an image generation/editing request for processing"""
        # Check if image generation is globally enabled
        if not IMAGE_GENERATION_ENABLED:
            error_message = "🔧 Image generation is temporarily unavailable due to maintenance. Please try again later."
            await interaction.response.send_message(error_message, ephemeral=True)
            return
            
        # Check if user is in the disabled list
        if str(interaction.user.id) in IMAGE_GENERATION_DISABLED_FOR_USERS:
            error_message = "🔧 Image generation is temporarily unavailable due to maintenance. Please try again later."
            await interaction.response.send_message(error_message, ephemeral=True)
            return
        
        # CRITICAL: Defer immediately to avoid Discord's 3-second timeout
        # We must respond to the interaction before doing any other logic
        await interaction.response.defer()
        already_responded = True
            
        try:
            # Get the task queue and enqueue the image handler
            task_queue = get_task_queue()
            queue_status = task_queue.get_queue_status()
            
            # If there are tasks in queue, inform the user via followup
            if queue_status["queue_size"] > 0:
                action = "edit" if attachment else "generate"
                await interaction.followup.send(
                    f"🎨 Your image {action} request has been queued! There are {queue_status['queue_size']} tasks ahead of you. "
                    f"I'll start working on your image as soon as I finish the current requests.",
                    ephemeral=True
                )
            
            # Enqueue the actual image processing task
            task_id = await task_queue.enqueue_task(
                self._image_handler,
                interaction, prompt, attachment, size, quality, background, model, already_responded
            )
            
            logger.info(f"Image command queued with task ID: {task_id}")
            
        except Exception as e:
            logger.error(f"Error queuing image command: {str(e)}")
            
            # Check if it's a queue full error
            if "queue is full" in str(e).lower():
                error_message = "🚫 I'm currently at maximum capacity (10 tasks queued). Please wait a moment for some tasks to complete before trying again."
            else:
                error_message = "Sorry, I'm currently overwhelmed with requests. Please try again in a moment."
            
            # We've already deferred, so use followup
            await interaction.followup.send(error_message, ephemeral=True)

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ImageCog(bot))
