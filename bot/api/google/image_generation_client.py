"""
image_generation_client.py
Google Gemini image generation client for the bot.
"""

import os
import re
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
            print(f"[GEMINI] generate_image called. prompt[:60]={prompt[:60]!r}, size={size}, aspect_ratio={aspect_ratio}", flush=True)
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
            print(f"[GEMINI] response received. type={type(response)} has candidates? {hasattr(response, 'candidates')}", flush=True)

            # Check if response has an error (rate limit, etc.)
            # The Gemini SDK doesn't raise exceptions for rate limits - it returns a response with error info
            if hasattr(response, 'prompt_feedback') and response.prompt_feedback:
                logger.warning(f"Prompt feedback received: {response.prompt_feedback}")
                if hasattr(response.prompt_feedback, 'block_reason'):
                    return None, f"Content blocked: {response.prompt_feedback.block_reason}"
            
            # Check for empty candidates (can indicate rate limiting or other errors)
            if not getattr(response, 'candidates', None):
                print("=" * 80, flush=True)
                print("[GEMINI] NO CANDIDATES IN RESPONSE!", flush=True)
                print(f"Response: {response}", flush=True)
                print(f"Response dir: {dir(response)}", flush=True)
                if hasattr(response, '__dict__'):
                    print(f"Response __dict__: {response.__dict__}", flush=True)
                print("=" * 80, flush=True)
                logger.error(f"No candidates in response. Full response: {response}")
                logger.error(f"Response attributes: {dir(response)}")
                if hasattr(response, '__dict__'):
                    logger.error(f"Response dict: {response.__dict__}")
                
                # Treat empty candidates as a rate limit condition for Gemini
                # The SDK often returns empty candidates on 429 without raising
                if hasattr(response, 'usage_metadata'):
                    logger.error(f"Usage metadata: {response.usage_metadata}")
                return None, "RATE_LIMIT: Google Gemini is currently experiencing high demand. Please try again in a few moments."
            
            # Extract the image from the response
            if not response.candidates[0].content.parts:
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
            print(f"[GEMINI] Exception in generate_image: {type(e).__name__}: {e}", flush=True)
            error_str = str(e)
            error_repr = repr(e)
            error_type = type(e).__name__
            logger.error(f"Failed to generate image with Gemini for prompt='{prompt[:50]}...': {e} (type: {error_type}, repr: {error_repr})", exc_info=True)
            
            # Check for rate limiting (429 errors) - check multiple sources
            is_rate_limit = False
            retry_after_seconds: int | None = None
            
            # Check string representation
            if "429" in error_str or "Too Many Requests" in error_str.lower() or "quota" in error_str.lower():
                is_rate_limit = True
            
            # Check repr
            if "429" in error_repr or "Too Many Requests" in error_repr:
                is_rate_limit = True
            
            # Check exception attributes
            if hasattr(e, 'status_code') and e.status_code == 429:
                is_rate_limit = True
            
            # Check if exception has a response object
            if hasattr(e, 'response') and hasattr(e.response, 'status_code') and e.response.status_code == 429:
                is_rate_limit = True
            
            # Check exception details attribute (some Google API errors)
            if hasattr(e, 'details'):
                details_str = str(e.details)
                if "429" in details_str or "RESOURCE_EXHAUSTED" in details_str:
                    is_rate_limit = True
                # Try to extract RetryInfo from structured details
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
                        # Fallback: parse "retry in Xs" from message
                        try:
                            m = re.search(r"retry in\s+([0-9]+(?:\.[0-9]+)?)s", arg_str.lower())
                            if m:
                                retry_after_seconds = max(1, int(float(m.group(1))))
                        except Exception:
                            pass
                        break
            
            # Check underlying exceptions (__cause__ and __context__)
            if hasattr(e, '__cause__') and e.__cause__:
                cause_str = str(e.__cause__)
                cause_repr = repr(e.__cause__)
                if "429" in cause_str or "429" in cause_repr or "Too Many Requests" in cause_str or "Too Many Requests" in cause_repr:
                    is_rate_limit = True
                    logger.info(f"Found 429 in __cause__: {cause_repr}")
            
            if hasattr(e, '__context__') and e.__context__:
                context_str = str(e.__context__)
                context_repr = repr(e.__context__)
                if "429" in context_str or "429" in context_repr or "Too Many Requests" in context_str or "Too Many Requests" in context_repr:
                    is_rate_limit = True
                    logger.info(f"Found 429 in __context__: {context_repr}")
            
            # Log all exception attributes for debugging
            logger.info(f"Exception attributes: {dir(e)}")
            logger.info(f"Exception args: {getattr(e, 'args', None)}")
            if hasattr(e, '__dict__'):
                logger.info(f"Exception __dict__: {e.__dict__}")
            
            if is_rate_limit:
                retry_hint = f" Please try again in approximately {retry_after_seconds}s." if retry_after_seconds else " Please try again in a few moments."
                return None, f"RATE_LIMIT: Google Gemini is currently experiencing high demand.{retry_hint}"
            
            # Return more detailed error info
            if error_str == "'error'" or not error_str:
                return None, f"Gemini API error ({error_type}): {error_repr}"
            
            return None, error_str

    @staticmethod
    def factory(model: str = DEFAULT_MODEL) -> "GeminiImageGenerationClient":
        return GeminiImageGenerationClient(model=model)
