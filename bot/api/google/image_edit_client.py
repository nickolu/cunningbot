"""
image_edit_client.py
Google Gemini image editing client for the bot.
"""

import os
import asyncio
import base64
from typing import Literal, Optional, Tuple, List, Union, BinaryIO
from io import BytesIO

from google import genai
from google.genai import types

from bot.app.utils.logger import get_logger

logger = get_logger()

PermittedImageAspectRatioType = Literal['1:1', '2:3', '3:2', '3:4', '4:3', '4:5', '5:4', '9:16', '16:9', '21:9']

# Mapping from OpenAI-style size formats to Gemini aspect ratios
SIZE_TO_ASPECT_RATIO = {
    '1024x1024': '1:1',
    '1536x1024': '3:2',
    '1024x1536': '2:3',
    'auto': '1:1',  # Default to square
}

class GeminiImageEditClient:
    """
    Client for Google Gemini's image editing API.
    Gemini 2.5 Flash Image supports advanced image editing including
    adding/removing elements, style transfer, and multi-image composition.
    """
    DEFAULT_MODEL = "gemini-2.5-flash-image"

    def __init__(self) -> None:
        """
        Initializes the Gemini client.
        The Google API key is expected to be set in the GOOGLE_API_KEY environment variable.
        """
        self.api_key = os.getenv("GOOGLE_API_KEY")
        if not self.api_key:
            raise EnvironmentError("GOOGLE_API_KEY environment variable is not set.")

        self.client = genai.Client(api_key=self.api_key)

    async def edit_image(
        self,
        image: Union[str, bytes, BinaryIO],
        prompt: str,
        n: int = 1,
        size: str = "1024x1024",
        aspect_ratio: Optional[PermittedImageAspectRatioType] = None,
        **kwargs  # Accept but ignore OpenAI-specific params like quality, background
    ) -> Tuple[Optional[List[bytes]], str]:
        """
        Edits an image using Google Gemini's image editing capabilities.

        Args:
            image: Path to the source image file, image bytes, or a file-like object.
            prompt: A text description of the desired edits.
            n: The number of images to generate (Note: Gemini generates one at a time).
            size: OpenAI-style size string (will be converted to aspect ratio).
            aspect_ratio: Direct Gemini aspect ratio specification (overrides size).
            **kwargs: Accepts but ignores OpenAI-specific parameters for compatibility.

        Returns:
            A tuple containing:
                - A list of image bytes if successful.
                - An empty string if successful, or an error message string if not.
        """
        try:
            # Prepare the image data
            image_data = None
            mime_type = "image/png"

            if isinstance(image, str):
                # Read from file path
                with open(image, "rb") as f:
                    image_data = f.read()
                # Determine mime type from extension
                if image.lower().endswith('.jpg') or image.lower().endswith('.jpeg'):
                    mime_type = "image/jpeg"
                elif image.lower().endswith('.webp'):
                    mime_type = "image/webp"
            elif isinstance(image, bytes):
                image_data = image
            elif hasattr(image, 'read'):
                image_data = image.read()
            else:
                return None, "Invalid image input type. Must be path (str), bytes, or file-like object."

            if not image_data:
                return None, "Failed to read image data."

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

            # Prepare the content with image and prompt
            # Gemini expects the image data as base64-encoded string with proper formatting
            image_b64 = base64.b64encode(image_data).decode('utf-8')
            image_part = types.Part.from_image(
                image=types.Image(
                    image_bytes=image_data
                )
            )

            # Generate the edited image(s)
            image_bytes_list: List[bytes] = []

            for i in range(n):
                try:
                    response = await asyncio.to_thread(
                        self.client.models.generate_content,
                        model=self.DEFAULT_MODEL,
                        contents=[image_part, prompt],
                        config=config
                    )

                    # Extract the image from the response
                    if not response.candidates or not response.candidates[0].content.parts:
                        logger.warning(f"No parts returned from Gemini API for edit request {i+1}/{n}.")
                        continue

                    # Find the image part in the response
                    result_image_parts = [
                        part.inline_data.data
                        for part in response.candidates[0].content.parts
                        if part.inline_data
                    ]

                    if not result_image_parts:
                        logger.warning(f"No image data in response for edit request {i+1}/{n}.")
                        continue

                    # Get the image bytes
                    result_bytes = result_image_parts[0]
                    image_bytes_list.append(result_bytes)
                    logger.info(f"Image edited successfully with Gemini ({i+1}/{n})")

                except Exception as e:
                    logger.error(f"Failed to edit image with Gemini (attempt {i+1}/{n}): {e}", exc_info=True)
                    if n == 1:
                        # If only requesting one image, return the error
                        return None, str(e)
                    # Otherwise, continue trying to generate the rest

            if not image_bytes_list:
                return None, "Image editing resulted in no image data."

            return image_bytes_list, ""

        except FileNotFoundError as e:
            logger.error(f"Image edit failed: File not found - {e.filename if hasattr(e, 'filename') else e}")
            return None, f"File not found: {e.filename if hasattr(e, 'filename') else str(e)}"
        except Exception as e:
            logger.error(f"Unexpected error during Gemini image edit: {e}", exc_info=True)
            return None, f"An unexpected error occurred: {str(e)}"

    @staticmethod
    def factory() -> "GeminiImageEditClient":
        """Factory method to create an instance of GeminiImageEditClient."""
        return GeminiImageEditClient()
