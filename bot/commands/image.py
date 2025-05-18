"""
image.py
Command for generating images using OpenAI and saving them to disk.
"""

from discord import app_commands
from discord.ext import commands
from bot.services.openai.image_generation_client import ImageGenerationClient
from bot.services.os.file_service import FileService
from bot.core.logger import get_logger
import uuid
import discord
from io import BytesIO

logger = get_logger()

class ImageCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.image_client = ImageGenerationClient.factory()

    @app_commands.command(name="image", description="Generate an image with OpenAI.")
    @app_commands.describe(prompt="Describe the image you want to generate.")
    async def image(self, interaction: discord.Interaction, prompt: str) -> None:
        print("Generating image...")
        await interaction.response.defer()
        image_bytes = await self.image_client.generate_image(prompt)
        if not image_bytes:
            await interaction.followup.send("Image generation failed.")
            return
        filename = f"generated_{uuid.uuid4().hex[:8]}.png"
        filepath = f"generated_images/{interaction.user.display_name}/{filename}"
        try:
            FileService.write_bytes(filepath, image_bytes)
            file_obj = BytesIO(image_bytes)
            file_obj.seek(0)

            await interaction.followup.send(
                content=f"Image generated for {interaction.user.mention}:\n{prompt}\n`{filename}`.",
                file=discord.File(fp=file_obj, filename=filename)
            )
            logger.info(f"Generated image: {filepath}")
        except Exception as e:
            logger.error(f"Failed to save image: {e}")
            await interaction.followup.send("Image generated but failed to save to disk.")

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ImageCog(bot))
