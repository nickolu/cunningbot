"""
image_generation_client.py
Google Gemini image generation client for the bot.
"""

import os
import asyncio
from typing import Literal, Optional

from google import genai
from google.genai import types

from bot.app.utils.logger import get_logger

logger = get_logger()

# Gemini supports aspect ratios, not fixed pixel sizes
PermittedImageAspectRatioType = Literal['1:1', '2:3', '3:2', '3:4', '4:3', '4:5', '5:4', '9:16', '16:9', '21:9']

# Mapping from OpenAI-style size formats to Gemini aspect ratios
SIZE_TO_ASPECT_RATIO = {
    '1024x1024': '1:1',
    '1536x1024': '3:2',
    '1024x1536': '2:3',
    'auto': '1:1',  # Default to square
}

class GeminiImageGenerationClient:
    DEFAULT_MODEL = "gemini-2.5-flash-image"

    def __init__(self, model: str = DEFAULT_MODEL):
        self.model = model
        self.api_key = os.getenv("GOOGLE_API_KEY")
        if not self.api_key:
            raise EnvironmentError("GOOGLE_API_KEY environment variable is not set.")

        # Configure the Gemini API client
        self.client = genai.Client(api_key=self.api_key)

    async def generate_image(
        self,
        prompt: str,
        *,
        size: str = "1024x1024",
        aspect_ratio: Optional[PermittedImageAspectRatioType] = None
    ) -> tuple[Optional[bytes], str]:
        """
        Generate an image from a prompt using Google Gemini's image generation API.

        Args:
            prompt: The text description of the image to generate
            size: OpenAI-style size string (will be converted to aspect ratio)
            aspect_ratio: Direct Gemini aspect ratio specification (overrides size)

        Returns:
            A tuple of (image_bytes, error_message). If successful, error_message is empty.
        """
        try:
            # Determine the aspect ratio to use
            if aspect_ratio:
                ar = aspect_ratio
            else:
                ar = SIZE_TO_ASPECT_RATIO.get(size, '1:1')

            # Configure the generation request
            config = types.GenerateContentConfig(
                response_modalities=["Image"],
                image_config=types.ImageConfig(
                    aspect_ratio=ar
                )
            )

            # Run the API call in a thread to avoid blocking
            response = await asyncio.to_thread(
                self.client.models.generate_content,
                model=self.model,
                contents=[prompt],
                config=config
            )

            # Extract the image from the response
            if not response.candidates or not response.candidates[0].content.parts:
                error_msg = "Image generation failed: No parts returned from Gemini API."
                logger.warning(error_msg)
                return None, error_msg

            # Find the image part in the response
            image_parts = [
                part.inline_data.data
                for part in response.candidates[0].content.parts
                if part.inline_data
            ]

            if not image_parts:
                error_msg = "Image generation failed: No image data in response."
                logger.warning(error_msg)
                return None, error_msg

            # Get the first image bytes
            image_bytes = image_parts[0]
            logger.info(f"Image generated successfully with Gemini for prompt: '{prompt[:50]}...'")
            return image_bytes, ""

        except Exception as e:
            logger.error(f"Failed to generate image with Gemini for prompt='{prompt[:50]}...': {e}", exc_info=True)
            return None, str(e)

    @staticmethod
    def factory(model: str = DEFAULT_MODEL) -> "GeminiImageGenerationClient":
        return GeminiImageGenerationClient(model=model)
