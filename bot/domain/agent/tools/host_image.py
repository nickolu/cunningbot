"""The `host_image` agent tool."""

from typing import Any, Dict, Optional

import discord

from bot.app.utils.logger import get_logger
from bot.domain.agent.tools.base import AgentTool

logger = get_logger()


SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "host_image",
        "description": (
            "Give an image from this chat a permanent public URL. Discord's own "
            "image links expire after about a day, so use this before putting an "
            "image on a published page, or when someone asks for a lasting link "
            "to an image. Pass the URL from an [Image: filename | URL] annotation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "image_url": {
                    "type": "string",
                    "description": (
                        "URL of the image to host, from an "
                        "[Image: filename | URL] annotation in the conversation."
                    ),
                },
                "filename": {
                    "type": "string",
                    "description": "Optional short descriptive name for the image.",
                },
            },
            "required": ["image_url"],
        },
    },
}


async def execute_host_image(
    arguments: Dict[str, Any], channel: discord.TextChannel
) -> str:
    """Execute the host_image tool."""
    image_url = (arguments.get("image_url") or "").strip()
    filename = (arguments.get("filename") or "").strip() or None

    if not image_url:
        return "No image URL was provided."

    guild = getattr(channel, "guild", None)
    if guild is None:
        return "Images can only be hosted from inside a server."

    try:
        from bot.domain.pages.image_service import host_image_from_url

        url = await host_image_from_url(str(guild.id), image_url, filename=filename)
    except EnvironmentError:
        return "Image hosting is not available (PAGES_BASE_URL / PAGES_PUBLISH_TOKEN not configured)."
    except RuntimeError as e:
        return f"Could not host that image: {e}"

    return f"Image hosted at a permanent URL: {url}"


TOOL = AgentTool(
    config_key="host_image",
    schema=SCHEMA,
    executor=execute_host_image,
    channel_aware=True,
)
