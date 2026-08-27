"""The `generate_image` agent tool."""

import re
import uuid
from typing import Any, Dict, Optional

import discord

from bot.api.openai.image_generation_client import ImageGenerationClient
from bot.api.google.image_generation_client import GeminiImageGenerationClient
from bot.app.utils.logger import get_logger
from bot.domain.agent.tools.base import AgentTool
from bot.domain.agent.tools._shared import _send_image_and_describe

logger = get_logger()


SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "generate_image",
        "description": (
            "Generate an image from a text description. The image will be "
            "sent as an attachment in the Discord channel. Use vivid, detailed "
            "prompts for best results."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Detailed text description of the image to generate",
                },
                "size": {
                    "type": "string",
                    "description": (
                        "Image dimensions. Use preset sizes like '1024x1024', '1536x1024', '1024x1536' for most models. "
                        "When using gpt-image-2, you can specify custom dimensions as 'WIDTHxHEIGHT' (e.g. '1920x1080', '2048x1152'). "
                        "Custom size constraints for gpt-image-2: both edges must be multiples of 16, "
                        "max 4000px per edge, min 655360 total pixels, max 8294400 total pixels, "
                        "aspect ratio between 1:3 and 3:1."
                    ),
                    "default": "1024x1024",
                },
                "model": {
                    "type": "string",
                    "enum": ["gemini", "gpt-image-2", "gpt-image-1"],
                    "description": "Model to use for generation. Default gemini.",
                    "default": "gemini",
                },
                "host": {
                    "type": "boolean",
                    "description": (
                        "Set true when the image will go on a web page or needs a "
                        "lasting link. Returns a permanent URL instead of a Discord "
                        "one, which expires after about a day."
                    ),
                    "default": False,
                },
            },
            "required": ["prompt"],
        },
    },
}


def _validate_gpt_image_2_size(size: str) -> str:
    """Validate and normalize a custom size string for GPT Image 2.

    Constraints:
    - Both edges must be multiples of 16
    - Max 4000px per edge
    - Min 655,360 total pixels
    - Max 8,294,400 total pixels
    - Aspect ratio between 1:3 and 3:1

    Returns the validated size string, or an error message starting with 'Error'.
    """
    import re
    match = re.match(r"^(\d+)x(\d+)$", size.strip())
    if not match:
        return size  # Let the API handle preset sizes like "auto"

    w, h = int(match.group(1)), int(match.group(2))

    if w % 16 != 0 or h % 16 != 0:
        # Round to nearest multiple of 16
        w = max(16, round(w / 16) * 16)
        h = max(16, round(h / 16) * 16)

    if w > 4000 or h > 4000:
        return f"Error: each edge must be at most 4000px (got {w}x{h})."

    total = w * h
    if total < 655_360:
        return f"Error: total pixels must be at least 655,360 (got {total:,} for {w}x{h})."
    if total > 8_294_400:
        return f"Error: total pixels must be at most 8,294,400 (got {total:,} for {w}x{h})."

    ratio = max(w, h) / min(w, h)
    if ratio > 3.0:
        return f"Error: aspect ratio must be between 1:3 and 3:1 (got {w}:{h}, ratio {ratio:.1f})."

    return f"{w}x{h}"


async def execute_generate_image(
    arguments: Dict[str, Any],
    channel: discord.TextChannel,
) -> str:
    """Execute the generate_image tool. Returns status message; image is sent to channel."""
    prompt = arguments.get("prompt", "")
    size = arguments.get("size", "1024x1024")
    model_pref = arguments.get("model", "gemini")

    if not prompt:
        return "No prompt provided for image generation."

    image_bytes: Optional[bytes] = None
    error_msg = ""
    model_used = ""

    # Route to the requested model
    if model_pref == "gpt-image-2":
        # Validate and normalize custom dimensions for gpt-image-2
        validated_size = _validate_gpt_image_2_size(size)
        if isinstance(validated_size, str) and validated_size.startswith("Error"):
            return validated_size
        try:
            client = ImageGenerationClient.factory(model="gpt-image-2-2026-04-21")
            image_bytes, error_msg = await client.generate_image(prompt, size=validated_size)
            model_used = "GPT Image 2"
        except EnvironmentError:
            return "OpenAI image generation is not available (no API key configured)."
    elif model_pref == "gpt-image-1":
        try:
            client = ImageGenerationClient.factory()
            image_bytes, error_msg = await client.generate_image(prompt, size=size)
            model_used = "GPT Image 1"
        except EnvironmentError:
            return "OpenAI image generation is not available (no API key configured)."
    else:
        # Default: try Gemini first, fall back to OpenAI
        try:
            client = GeminiImageGenerationClient.factory()
            image_bytes, error_msg = await client.generate_image(prompt, size=size)
            model_used = "Gemini"
        except EnvironmentError:
            pass  # GOOGLE_API_KEY not set

        if image_bytes is None:
            try:
                client = ImageGenerationClient.factory()
                image_bytes, error_msg = await client.generate_image(prompt, size=size)
                model_used = "OpenAI"
            except EnvironmentError:
                return "Image generation is not available (no API keys configured)."

    if image_bytes is None:
        return f"Image generation failed: {error_msg}"

    filename = f"agent_image_{uuid.uuid4().hex[:8]}.png"
    return await _send_image_and_describe(
        channel,
        image_bytes,
        filename,
        f"Image generated and sent to channel using {model_used}. Prompt: '{prompt[:80]}'.",
        host=bool(arguments.get("host")),
    )


TOOL = AgentTool(
    config_key="image",
    schema=SCHEMA,
    executor=execute_generate_image,
    channel_aware=True,
)
