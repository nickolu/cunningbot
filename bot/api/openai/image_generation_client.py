"""
image_generation_client.py
OpenAI image generation client for the bot.
"""

import base64
import os
import asyncio
from openai import OpenAI

from typing import Literal, Optional
from bot.app.utils.logger import get_logger

logger = get_logger()
openai = OpenAI()

PermittedImageModelType = str  # OpenAI currently supports 'dall-e-3', 'gpt-image-1', etc.
PermittedImageSizeType = Literal['auto', '1024x1024', '1536x1024', '1024x1536', '256x256', '512x512', '1792x1024', '1024x1792']

class ImageGenerationClient:
    DEFAULT_MODEL = "gpt-image-1"

    def __init__(self, model: PermittedImageModelType = DEFAULT_MODEL):
        self.model = model
        self.api_key = os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise EnvironmentError("OPENAI_API_KEY environment variable is not set.")

    async def generate_image(self, prompt: str, *, size: PermittedImageSizeType = "1024x1024", n: int = 1) -> tuple[Optional[bytes], str]:
        """
        Generate an image from a prompt using OpenAI's image API.
        Returns the image bytes if successful, else None.
        """
        try:
            # Run the synchronous OpenAI API call in a separate thread
            img = await asyncio.to_thread(
                openai.images.generate,
                model="gpt-image-1",  # Preserving original hardcoded model
                prompt=prompt,
                n=n,
                size=size 
            )

            if not img.data or not img.data[0].b64_json:
                error_msg = "Image generation failed: No image data or b64_json returned from API."
                # Optionally, include revised_prompt if available and useful for debugging
                if img.data and img.data[0].revised_prompt:
                    error_msg += f" Revised prompt by API: '{img.data[0].revised_prompt}'"
                logger.warning(error_msg)
                return None, error_msg

            image_bytes = base64.b64decode(img.data[0].b64_json)
            logger.info(f"Image generated successfully for prompt: '{prompt}'") # Added success log
            return image_bytes, ""
        except Exception as e:
            logger.error(f"Failed to generate image for prompt='{prompt}': {e}", exc_info=True) # Improved logging
            message = str(e) # Simplified message extraction
            return None, message

    @staticmethod
    def factory(model: PermittedImageModelType = DEFAULT_MODEL) -> "ImageGenerationClient":
        return ImageGenerationClient(model=model)
