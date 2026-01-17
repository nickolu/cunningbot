"""
image_edit_client.py
Google Gemini image editing client for the bot.
"""

import os
import asyncio
import base64
from typing import Literal, Optional, Tuple, List, Union, BinaryIO
from io import BytesIO
import re

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
        image: Union[str, bytes, BinaryIO, List[Union[str, bytes, BinaryIO]]],
        prompt: str,
        n: int = 1,
        size: str = "1024x1024",
        aspect_ratio: Optional[PermittedImageAspectRatioType] = None,
        **kwargs  # Accept but ignore OpenAI-specific params like quality, background
    ) -> Tuple[Optional[List[bytes]], str]:
        """
        Edits an image or multiple images using Google Gemini's image editing capabilities.

        Args:
            image: Path to the source image file, image bytes, a file-like object,
                   or a list of any of these for multi-image composition.
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
            print(f"[GEMINI] edit_image called. prompt[:60]={prompt[:60]!r}, n={n}, size={size}, aspect_ratio={aspect_ratio}", flush=True)

            # Normalize input to list for uniform processing
            images_to_process = image if isinstance(image, list) else [image]
            print(f"[GEMINI] Processing {len(images_to_process)} image(s)", flush=True)

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

            # Process each image and create image parts
            image_parts = []
            for idx, img in enumerate(images_to_process):
                image_data = None
                mime_type = "image/png"

                if isinstance(img, str):
                    # Read from file path
                    with open(img, "rb") as f:
                        image_data = f.read()
                    # Determine mime type from extension
                    if img.lower().endswith('.jpg') or img.lower().endswith('.jpeg'):
                        mime_type = "image/jpeg"
                    elif img.lower().endswith('.webp'):
                        mime_type = "image/webp"
                elif isinstance(img, bytes):
                    image_data = img
                elif hasattr(img, 'read'):
                    image_data = img.read()
                else:
                    return None, f"Invalid image input type at index {idx}. Must be path (str), bytes, or file-like object."

                if not image_data:
                    return None, f"Failed to read image data at index {idx}."

                # Prepare the content with image - prefer modern API if available; fall back for older SDKs
                image_part = None
                try:
                    # Newer SDK path
                    image_part = types.Part.from_image(
                        image=types.Image(
                            image_bytes=image_data
                        )
                    )
                except AttributeError:
                    # Older SDKs may not have from_image; use from_bytes
                    try:
                        image_part = types.Part.from_bytes(
                            data=image_data,
                            mime_type=mime_type
                        )
                    except Exception as part_err:
                        logger.error(f"Failed to build Gemini image part using from_bytes: {part_err}")
                        return None, f"An unexpected error occurred: image_part_build"
                except Exception as part_err:
                    logger.error(f"Failed to build Gemini image part using from_image: {part_err}")
                    # Try fallback once more in case of non-AttributeError
                    try:
                        image_part = types.Part.from_bytes(
                            data=image_data,
                            mime_type=mime_type
                        )
                    except Exception:
                        return None, f"An unexpected error occurred: from_image"

                image_parts.append(image_part)

            # Generate the edited image(s)
            image_bytes_list: List[bytes] = []

            for i in range(n):
                try:
                    # Build contents: all image parts followed by the prompt
                    contents = image_parts + [prompt]
                    response = await asyncio.to_thread(
                        self.client.models.generate_content,
                        model=self.DEFAULT_MODEL,
                        contents=contents,
                        config=config
                    )

                    print(f"[GEMINI] edit response {i+1}/{n} received. type={type(response)} has candidates? {hasattr(response, 'candidates')}", flush=True)
                    # Extract the image from the response
                    if not getattr(response, 'candidates', None):
                        print("=" * 80, flush=True)
                        print(f"[GEMINI] NO CANDIDATES IN EDIT RESPONSE {i+1}/{n}!", flush=True)
                        print(f"Response: {response}", flush=True)
                        print(f"Response dir: {dir(response)}", flush=True)
                        if hasattr(response, '__dict__'):
                            print(f"Response __dict__: {response.__dict__}", flush=True)
                        print("=" * 80, flush=True)
                        logger.warning(f"No candidates returned from Gemini API for edit request {i+1}/{n}.")
                        # Treat empty candidates as rate limit; short-circuit if n==1 for clear UX
                        if n == 1:
                            return None, "RATE_LIMIT: Google Gemini is currently experiencing high demand. Please try again in a few moments."
                        continue
                    if not response.candidates[0].content.parts:
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
                    error_str = str(e)
                    error_repr = repr(e)
                    error_type = type(e).__name__
                    logger.error(f"Failed to edit image with Gemini (attempt {i+1}/{n}): {e} (type: {error_type}, repr: {error_repr})", exc_info=True)
                    if n == 1:
                        # If only requesting one image, return the error
                        # Check for rate limiting (429 errors) - check multiple sources
                        is_rate_limit = False
                        retry_after_seconds: int | None = None
                        
                        if "429" in error_str or "Too Many Requests" in error_str.lower() or "quota" in error_str.lower():
                            is_rate_limit = True
                        if "429" in error_repr or "Too Many Requests" in error_repr:
                            is_rate_limit = True
                        if hasattr(e, 'status_code') and e.status_code == 429:
                            is_rate_limit = True
                        if hasattr(e, 'response') and hasattr(e.response, 'status_code') and e.response.status_code == 429:
                            is_rate_limit = True
                        if hasattr(e, 'details') and ("429" in str(e.details) or "RESOURCE_EXHAUSTED" in str(e.details)):
                            is_rate_limit = True
                            # Try extracting RetryInfo
                            try:
                                details_obj = e.details
                                if isinstance(details_obj, (list, tuple)):
                                    for d in details_obj:
                                        if isinstance(d, dict) and str(d.get('@type', '')).endswith('RetryInfo'):
                                            retry_delay = d.get('retryDelay')  # e.g., '26s'
                                            if isinstance(retry_delay, str) and retry_delay.endswith('s'):
                                                sec_str = retry_delay[:-1]
                                                try:
                                                    retry_after_seconds = max(1, int(float(sec_str)))
                                                except Exception:
                                                    pass
                                            break
                            except Exception:
                                pass
                        
                        # Check exception args
                        if hasattr(e, 'args') and e.args:
                            for arg in e.args:
                                arg_str = str(arg)
                                if "429" in arg_str or "Too Many Requests" in arg_str.lower() or "resource_exhausted" in arg_str.lower():
                                    is_rate_limit = True
                                    try:
                                        m = re.search(r"retry in\s+([0-9]+(?:\.[0-9]+)?)s", arg_str.lower())
                                        if m:
                                            retry_after_seconds = max(1, int(float(m.group(1))))
                                    except Exception:
                                        pass
                                    break
                        
                        # Check underlying exceptions
                        if hasattr(e, '__cause__') and e.__cause__:
                            cause_str = str(e.__cause__)
                            if "429" in cause_str or "Too Many Requests" in cause_str:
                                is_rate_limit = True
                        
                        if hasattr(e, '__context__') and e.__context__:
                            context_str = str(e.__context__)
                            if "429" in context_str or "Too Many Requests" in context_str:
                                is_rate_limit = True
                        
                        if is_rate_limit:
                            retry_hint = f" Please try again in approximately {retry_after_seconds}s." if retry_after_seconds else " Please try again in a few moments."
                            return None, f"RATE_LIMIT: Google Gemini is currently experiencing high demand.{retry_hint}"
                        
                        # Return more detailed error info
                        if error_str == "'error'" or not error_str:
                            return None, f"Gemini API error ({error_type}): {error_repr}"
                        return None, error_str
                    # Otherwise, continue trying to generate the rest

            if not image_bytes_list:
                return None, "Image editing resulted in no image data."

            return image_bytes_list, ""

        except FileNotFoundError as e:
            logger.error(f"Image edit failed: File not found - {e.filename if hasattr(e, 'filename') else e}")
            return None, f"File not found: {e.filename if hasattr(e, 'filename') else str(e)}"
        except Exception as e:
            error_str = str(e)
            error_repr = repr(e)
            error_type = type(e).__name__
            logger.error(f"Unexpected error during Gemini image edit: {e} (type: {error_type}, repr: {error_repr})", exc_info=True)
            
            # Check for rate limiting (429 errors) - check multiple sources
            is_rate_limit = False
            
            if "429" in error_str or "Too Many Requests" in error_str.lower() or "quota" in error_str.lower():
                is_rate_limit = True
            if "429" in error_repr or "Too Many Requests" in error_repr:
                is_rate_limit = True
            if hasattr(e, 'status_code') and e.status_code == 429:
                is_rate_limit = True
            if hasattr(e, 'response') and hasattr(e.response, 'status_code') and e.response.status_code == 429:
                is_rate_limit = True
            if hasattr(e, 'details') and ("429" in str(e.details) or "RESOURCE_EXHAUSTED" in str(e.details)):
                is_rate_limit = True
            
            # Check exception args
            if hasattr(e, 'args') and e.args:
                for arg in e.args:
                    arg_str = str(arg)
                    if "429" in arg_str or "Too Many Requests" in arg_str.lower() or "resource_exhausted" in arg_str.lower():
                        is_rate_limit = True
                        break
            
            # Check underlying exceptions
            if hasattr(e, '__cause__') and e.__cause__:
                cause_str = str(e.__cause__)
                cause_repr = repr(e.__cause__)
                if "429" in cause_str or "429" in cause_repr or "Too Many Requests" in cause_str:
                    is_rate_limit = True
                    logger.info(f"Found 429 in __cause__: {cause_repr}")
            
            if hasattr(e, '__context__') and e.__context__:
                context_str = str(e.__context__)
                context_repr = repr(e.__context__)
                if "429" in context_str or "429" in context_repr or "Too Many Requests" in context_str:
                    is_rate_limit = True
                    logger.info(f"Found 429 in __context__: {context_repr}")
            
            # Log all exception attributes for debugging
            logger.info(f"Exception attributes: {dir(e)}")
            logger.info(f"Exception args: {getattr(e, 'args', None)}")
            if hasattr(e, '__dict__'):
                logger.info(f"Exception __dict__: {e.__dict__}")
            
            if is_rate_limit:
                return None, "RATE_LIMIT: Google Gemini is currently experiencing high demand. Please try again in a few moments."
            
            # Return more detailed error info
            if error_str == "'error'" or not error_str:
                return None, f"Gemini API error ({error_type}): {error_repr}"
            
            return None, f"An unexpected error occurred: {error_str}"

    @staticmethod
    def factory() -> "GeminiImageEditClient":
        """Factory method to create an instance of GeminiImageEditClient."""
        return GeminiImageEditClient()
