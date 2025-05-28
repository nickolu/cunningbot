"""
image.py
Command for generating images using OpenAI and saving them to disk.
"""

from typing import Optional
from discord import app_commands
from discord.ext import commands
from bot.api.openai.image_generation_client import ImageGenerationClient
from bot.api.openai.image_edit_client import ImageEditClient
from bot.api.os.file_service import FileService
from bot.domain.logger import get_logger
import uuid
import discord
from io import BytesIO

logger = get_logger()

class ImageCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.image_generation_client = ImageGenerationClient.factory()
        self.image_edit_client = ImageEditClient.factory()

    @app_commands.command(name="image", description="Generate or edit an image with OpenAI.")
    @app_commands.describe(prompt="Describe the image you want to generate or the edit you want to make.", attachment="Optional: The image to edit.")
    async def image(self, interaction: discord.Interaction, prompt: str, attachment: Optional[discord.Attachment] = None) -> None:
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

            image_list_or_none, error_msg_edit = self.image_edit_client.edit_image(image=image_to_edit_bytes, prompt=prompt)
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
            generated_bytes_or_none, error_msg_gen = await self.image_generation_client.generate_image(prompt)
            final_error_message = error_msg_gen
            final_image_bytes = generated_bytes_or_none

            if not final_image_bytes:
                # Ensure there's an error message if no image was produced
                if not final_error_message: final_error_message = "Image generation resulted in no image data."
                await interaction.followup.send(f"{interaction.user.mention}: Image generation failed\n\nprompt: *{prompt}*\n\n{final_error_message}")
                return
            
            filename = f"generated_{uuid.uuid4().hex[:8]}.png"
            filepath = f"generated_images/{interaction.user.display_name}/{filename}"
        try:
            # At this point, final_image_bytes should be bytes, not None, due to the checks above.
            if final_image_bytes is None: # Should not happen if logic above is correct, but as a safeguard
                logger.error(f"Internal error: final_image_bytes is None before saving. Action: {action_type}, Prompt: {prompt}")
                await interaction.followup.send(f"{interaction.user.mention}: An unexpected internal error occurred before saving the image.")
                return

            FileService.write_bytes(filepath, final_image_bytes)
            file_obj = BytesIO(final_image_bytes)
            file_obj.seek(0)

            await interaction.followup.send(
                content=f"Image {action_type} for {interaction.user.mention}:\nPrompt: *{prompt}*\n`{filename}`.",
                file=discord.File(fp=file_obj, filename=filename)
            )
            logger.info(f"Image {action_type} and saved: {filepath}")
        except Exception as e:
            logger.error(f"Failed to save image: {e}")
            await interaction.followup.send(
                content=f"Image {action_type} for {interaction.user.mention}:\nPrompt: *{prompt}*\n`{filename}`.\n\nFailed to save to disk.\n\n{e}",
                file=discord.File(fp=file_obj, filename=filename)
            )

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ImageCog(bot))
