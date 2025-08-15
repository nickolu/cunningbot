"""
image.py
Command for generating images using OpenAI and saving them to disk.
"""

import asyncio
import random

from typing import Optional
from discord import app_commands
from discord.ext import commands
from bot.api.openai.image_generation_client import ImageGenerationClient
from bot.api.openai.image_edit_client import ImageEditClient
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
        self.image_generation_client = ImageGenerationClient.factory()
        self.image_edit_client = ImageEditClient.factory()

    async def _image_handler(
        self, 
        interaction: discord.Interaction, 
        prompt: str, 
        attachment: Optional[discord.Attachment] = None,
        size: Optional[str] = None,
        quality: Optional[str] = None,
        background: Optional[str] = None,
        already_responded: bool = False
    ) -> None:
        """Internal image handler that processes the actual image generation/editing request"""
        # Only defer if we haven't already responded to the interaction
        if not already_responded and not interaction.response.is_done():
            await interaction.response.defer()

        # Set defaults
        size = size or "auto"
        quality = quality or "auto"
        background = background or "auto"

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

            image_list_or_none, error_msg_edit = await asyncio.to_thread(
                self.image_edit_client.edit_image,
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
            generated_bytes_or_none, error_msg_gen = await self.image_generation_client.generate_image(prompt, size=size)
            final_error_message = error_msg_gen
            final_image_bytes = generated_bytes_or_none

            if not final_image_bytes:
                logger.error(f"Image operation resulted in None for final_image_bytes. Action: {action_type}, Prompt: {prompt}")
                error_msg = f"{interaction.user.mention}: An unexpected error occurred while {action_type}ing the image."
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
        if size != "1024x1024":
            params_used.append(f"Size: {size}")
        if quality != "auto":
            params_used.append(f"Quality: {quality}")
        if background != "auto":
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

    @app_commands.command(name="image", description="Generate or edit an image with OpenAI.")
    @app_commands.describe(
        prompt="Describe the image you want to generate or the edit you want to make.", 
        attachment="Optional: The image to edit.", 
        size="Size of the generated image", 
        quality="Quality of the generated image (editing only)", 
        background="Background setting for the generated image (editing only)"
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
            
        try:
            # Get the task queue and enqueue the image handler
            task_queue = get_task_queue()
            queue_status = task_queue.get_queue_status()
            
            already_responded = False
            
            # If there are tasks in queue, inform the user
            if queue_status["queue_size"] > 0:
                action = "edit" if attachment else "generate"
                await interaction.response.send_message(
                    f"🎨 Your image {action} request has been queued! There are {queue_status['queue_size']} tasks ahead of you. "
                    f"I'll start working on your image as soon as I finish the current requests.",
                    ephemeral=True
                )
                already_responded = True
            else:
                # If no queue, defer immediately to avoid "application did not respond"
                await interaction.response.defer()
                already_responded = True
            
            # Enqueue the actual image processing task
            task_id = await task_queue.enqueue_task(
                self._image_handler, 
                interaction, prompt, attachment, size, quality, background, already_responded
            )
            
            logger.info(f"Image command queued with task ID: {task_id}")
            
        except Exception as e:
            logger.error(f"Error queuing image command: {str(e)}")
            
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
    await bot.add_cog(ImageCog(bot))
