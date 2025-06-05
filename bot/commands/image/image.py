"""
image.py
Command for generating images using OpenAI and saving them to disk.
"""

import asyncio

from typing import Optional
from discord import app_commands
from discord.ext import commands
from bot.api.openai.image_generation_client import ImageGenerationClient
from bot.api.openai.image_edit_client import ImageEditClient
from bot.api.os.file_service import FileService
from bot.domain.logger import get_logger
from bot.core.task_queue import get_task_queue
import uuid
import discord
from io import BytesIO

logger = get_logger()

class ImageCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.image_generation_client = ImageGenerationClient.factory()
        self.image_edit_client = ImageEditClient.factory()

    async def _image_handler(self, interaction: discord.Interaction, prompt: str, attachment: Optional[discord.Attachment] = None) -> None:
        """Internal image handler that processes the actual image generation/editing request"""
        await interaction.response.defer()

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
                await interaction.followup.send(f"{interaction.user.mention}: Failed to read the attached image.\n\nError: {e}")
                return

            image_list_or_none, error_msg_edit = await asyncio.to_thread(
                self.image_edit_client.edit_image,
                image=image_to_edit_bytes,
                prompt=prompt
            )
            final_error_message = error_msg_edit
            if image_list_or_none and len(image_list_or_none) > 0:
                final_image_bytes = image_list_or_none[0] # Use the first image
            
            if not final_image_bytes:
                # Ensure there's an error message if no image was produced
                if not final_error_message: final_error_message = "Image editing resulted in no image data."
                await interaction.followup.send(f"{interaction.user.mention}: Image editing failed\n\nprompt: *{prompt}*\nattachment: *{attachment.filename}*\n\n{final_error_message}")
                return
            
            filename = f"edited_{attachment.filename}_{uuid.uuid4().hex[:8]}.png"
            filepath = f"edited_images/{interaction.user.display_name}/{filename}"

        else:
            print("Generating image...")
            action_type = "generated"
            filename_prefix = "generated"
            generated_bytes_or_none, error_msg_gen = await self.image_generation_client.generate_image(prompt)
            final_error_message = error_msg_gen
            final_image_bytes = generated_bytes_or_none

            if not final_image_bytes:
                logger.error(f"Image operation resulted in None for final_image_bytes. Action: {action_type}, Prompt: {prompt}")
                await interaction.followup.send(f"{interaction.user.mention}: An unexpected error occurred while {action_type}ing the image.")
                return
        
        filename = f"{filename_prefix}_{uuid.uuid4().hex[:8]}.png"
        # Using relative paths, ensure the base directory ('generated_images', 'edited_images')
        # is writable by the application user in the Docker container.
        base_dir = "edited_images" if attachment else "generated_images"
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


        base_message_content = f"Image {action_type} for {interaction.user.mention}:\nPrompt: *{prompt}*"
        full_message_content = f"{base_message_content}{save_status_message}"

        await interaction.followup.send(
            content=full_message_content,
            file=discord_file_attachment  # This will now always be defined if final_image_bytes was valid
        )

    @app_commands.command(name="image", description="Generate or edit an image with OpenAI.")
    @app_commands.describe(prompt="Describe the image you want to generate or the edit you want to make.", attachment="Optional: The image to edit.")
    async def image(self, interaction: discord.Interaction, prompt: str, attachment: Optional[discord.Attachment] = None) -> None:
        """Queue an image generation/editing request for processing"""
        try:
            # Get the task queue and enqueue the image handler
            task_queue = get_task_queue()
            queue_status = task_queue.get_queue_status()
            
            # If there are tasks in queue, inform the user
            if queue_status["queue_size"] > 0:
                action = "edit" if attachment else "generate"
                await interaction.response.send_message(
                    f"🎨 Your image {action} request has been queued! There are {queue_status['queue_size']} tasks ahead of you. "
                    f"I'll start working on your image as soon as I finish the current requests.",
                    ephemeral=True
                )
            
            # Enqueue the actual image processing task
            task_id = await task_queue.enqueue_task(
                self._image_handler, 
                interaction, prompt, attachment
            )
            
            logger.info(f"Image command queued with task ID: {task_id}")
            
        except Exception as e:
            logger.error(f"Error queuing image command: {str(e)}")
            await interaction.response.send_message(
                "Sorry, I'm currently overwhelmed with requests. Please try again in a moment.",
                ephemeral=True
            )

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ImageCog(bot))
