"""The `request_upload` agent tool.

Opens an upload link so someone can send the bot files. The link goes in the
reply; the bot then watches the session (`bot/app/upload_watch.py`) and posts
what arrived when the link closes or fills up.

Not offered to scheduled runs: an unattended job has nobody to upload.
"""

from typing import Any, Dict

import discord

from bot.app.utils.logger import get_logger
from bot.domain.agent.tools.base import AgentTool
from bot.domain.files.file_service import (
    DEFAULT_MAX_FILES,
    DEFAULT_TTL_DAYS,
    KIND_ANY,
    KIND_IMAGES,
    LINK_OPEN_MINUTES,
    MAX_FILES_CAP,
    TTL_CHOICES,
    expires_in_words,
    format_bytes,
)

logger = get_logger()


SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "request_upload",
        "description": (
            "Open a link where people can upload files (photos, PDFs, anything) "
            "for the bot to keep and find later. Use it when someone wants to send "
            "files, share photos for a page, or asks for 'a place to put' things. "
            "Share the returned link verbatim; it closes after "
            f"{LINK_OPEN_MINUTES} minutes and the bot posts what was uploaded."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "label": {
                    "type": "string",
                    "description": (
                        "What the files are for, in a few words, e.g. 'Tahoe trip photos'. "
                        "Shown on the upload page and saved with each file."
                    ),
                },
                "kind": {
                    "type": "string",
                    "enum": [KIND_IMAGES, KIND_ANY],
                    "description": "Accept only images, or any file. Default any.",
                },
                "max_files": {
                    "type": "integer",
                    "description": f"How many files the link accepts, 1-{MAX_FILES_CAP}. Default {DEFAULT_MAX_FILES}.",
                },
                "keep_days": {
                    "type": "integer",
                    "enum": list(TTL_CHOICES),
                    "description": f"How long to keep the files. Default {DEFAULT_TTL_DAYS}.",
                },
            },
            "required": [],
        },
    },
}


async def execute_request_upload(
    arguments: Dict[str, Any], channel: discord.TextChannel, user: Any
) -> str:
    """Execute the request_upload tool."""
    guild = getattr(channel, "guild", None)
    if guild is None:
        return "Upload links can only be opened from inside a server."

    try:
        from bot.api.pages.client import PagesNotDeployed, UploadsNotConfigured
        from bot.domain.files.file_service import open_upload_link

        link = await open_upload_link(
            guild_id=str(guild.id),
            channel_id=str(getattr(channel, "id", "") or "") or None,
            channel_name=getattr(channel, "name", None),
            requester_id=str(user.id) if user is not None else None,
            requester_name=getattr(user, "display_name", None) if user is not None else None,
            label=arguments.get("label"),
            kind=arguments.get("kind"),
            max_files=arguments.get("max_files"),
            ttl_days=arguments.get("keep_days"),
        )
    except EnvironmentError:
        return "Uploads are not available (PAGES_BASE_URL / PAGES_PUBLISH_TOKEN not configured)."
    except UploadsNotConfigured:
        return (
            "Uploads are not set up yet: the page service has no storage bucket "
            "configured. Tell the user it needs a one-time setup by the bot's owner."
        )
    except PagesNotDeployed:
        return (
            "This server's page service is running an older version without "
            "uploads. Tell the user it needs a redeploy."
        )
    except RuntimeError as e:
        return f"Could not open an upload link: {e}"

    try:
        from bot.app.upload_watch import start_upload_watch

        start_upload_watch(
            channel,
            str(guild.id),
            link.session_id,
            link.expires_at,
            link.max_files,
            requester_mention=getattr(user, "mention", None) if user is not None else None,
            label=(arguments.get("label") or "").strip() or None,
        )
    except RuntimeError as e:  # no running loop, in a test
        logger.warning({"event": "upload_watch_not_started", "error": str(e)})

    what = "images" if link.kind == KIND_IMAGES else "files"
    return (
        f"Upload link opened: {link.url}\n"
        f"It takes up to {link.max_files} {what} of {format_bytes(link.max_bytes)} each, "
        f"keeps them for {link.ttl_days} day{'s' if link.ttl_days != 1 else ''}, and closes "
        f"{expires_in_words(link.expires_at)}. Include the link exactly as written. The "
        "bot will post what was uploaded when the link closes; do not claim anything "
        "has been uploaded yet."
    )


TOOL = AgentTool(
    config_key="request_upload",
    schema=SCHEMA,
    executor=execute_request_upload,
    channel_aware=True,
    user_aware=True,
    scheduled_ok=False,
)
