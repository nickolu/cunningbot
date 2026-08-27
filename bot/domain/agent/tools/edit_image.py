"""The `edit_image` agent tool."""

import uuid
from typing import Any, Dict

import aiohttp
import discord

from bot.api.google.image_edit_client import GeminiImageEditClient
from bot.api.openai.image_edit_client import ImageEditClient
from bot.app.utils.logger import get_logger
from bot.domain.agent.tools.base import AgentTool
from bot.domain.agent.tools._shared import _send_image_and_describe

logger = get_logger()


SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "edit_image",
        "description": (
            "Edit an existing image from the chat. Use the image URL from "
            "[Image: filename | URL] annotations in the conversation history. "
            "The edited image will be sent as an attachment in the Discord channel."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "image_url": {
                    "type": "string",
                    "description": (
                        "URL of the image to edit. Use the URL from "
                        "[Image: filename | URL] annotations in the conversation history."
                    ),
                },
                "prompt": {
                    "type": "string",
                    "description": "Description of the edit to make to the image.",
                },
                "model": {
                    "type": "string",
                    "enum": [
                        "gemini-2.5-flash",
                        "gemini-3-pro",
                        "gpt-image-2",
                        "gpt-image-1",
                    ],
                    "description": (
                        "Image model to use. "
                        "gemini-2.5-flash (default, fast and cheap), "
                        "gemini-3-pro (higher quality), "
                        "gpt-image-2 (OpenAI latest), "
                        "gpt-image-1 (OpenAI)."
                    ),
                    "default": "gemini-2.5-flash",
                },
                "size": {
                    "type": "string",
                    "enum": ["1024x1024", "1536x1024", "1024x1536"],
                    "description": "Output image dimensions. Default square.",
                    "default": "1024x1024",
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
            "required": ["image_url", "prompt"],
        },
    },
}


# Friendly model names → actual model identifiers for image editing
IMAGE_EDIT_MODELS = {
    "gemini-2.5-flash": "gemini-2.5-flash-image",
    "gemini-3-pro": "gemini-3-pro-image-preview",
    "gpt-image-2": "gpt-image-2-2026-04-21",
    "gpt-image-1": "gpt-image-1",
}

DEFAULT_IMAGE_EDIT_MODEL = "gemini-2.5-flash"


async def execute_edit_image(
    arguments: Dict[str, Any],
    channel: discord.TextChannel,
) -> str:
    """Execute the edit_image tool. Downloads the source image, edits it via the selected model, and sends the result."""
    image_url = arguments.get("image_url", "")
    prompt = arguments.get("prompt", "")
    size = arguments.get("size", "1024x1024")
    model_key = arguments.get("model", DEFAULT_IMAGE_EDIT_MODEL)

    if not image_url:
        return "No image URL provided. Look for [Image: ...] annotations in the conversation."
    if not prompt:
        return "No edit prompt provided."

    # Download the source image
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(image_url) as resp:
                if resp.status != 200:
                    return f"Failed to download image: HTTP {resp.status}"
                image_bytes = await resp.read()
    except Exception as e:
        logger.error(f"Image download failed: {e}")
        return f"Failed to download image: {e}"

    actual_model = IMAGE_EDIT_MODELS.get(model_key, IMAGE_EDIT_MODELS[DEFAULT_IMAGE_EDIT_MODEL])
    is_openai = model_key == "gpt-image-1"

    # Edit the image
    try:
        if is_openai:
            client = ImageEditClient.factory()
            # OpenAI edit_image is synchronous — run in thread
            import asyncio
            result_images, error_msg = await asyncio.to_thread(
                client.edit_image,
                image=image_bytes,
                prompt=prompt,
                size=size,
            )
        else:
            client = GeminiImageEditClient.factory(model=actual_model)
            result_images, error_msg = await client.edit_image(
                image=image_bytes,
                prompt=prompt,
                size=size,
            )
    except EnvironmentError:
        key_name = "OPENAI_API_KEY" if is_openai else "GOOGLE_API_KEY"
        return f"Image editing is not available ({key_name} not configured)."
    except Exception as e:
        logger.error(f"Image edit failed: {e}", exc_info=True)
        return f"Image editing failed: {e}"

    if not result_images:
        return f"Image editing failed: {error_msg}"

    filename = f"agent_edit_{uuid.uuid4().hex[:8]}.png"
    return await _send_image_and_describe(
        channel,
        result_images[0],
        filename,
        f"Image edited using {model_key} and sent to channel. Edit prompt: '{prompt[:80]}'.",
        host=bool(arguments.get("host")),
    )


TOOL = AgentTool(
    config_key="edit_image",
    schema=SCHEMA,
    executor=execute_edit_image,
    channel_aware=True,
)
