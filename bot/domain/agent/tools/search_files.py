"""The `search_files` agent tool: find files people uploaded through the bot."""

from typing import Any, Dict

import discord

from bot.app.utils.logger import get_logger
from bot.domain.agent.tools.base import AgentTool
from bot.domain.files.file_service import describe_file

logger = get_logger()

MAX_RESULTS = 10


SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "search_files",
        "description": (
            "Find files that were uploaded to the bot for this server (through an "
            "upload link), newest first. Matches words against the file name, the "
            "note the uploader wrote, the link's label, the uploader's name, and the "
            "file type. Leave the query empty to list the latest uploads. Share the "
            "download links it returns; they are stable until the file expires."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Words to match, e.g. 'receipt march' or 'photos from Sam'. Optional.",
                },
            },
            "required": [],
        },
    },
}


async def execute_search_files(
    arguments: Dict[str, Any], channel: discord.TextChannel
) -> str:
    """Execute the search_files tool."""
    guild = getattr(channel, "guild", None)
    if guild is None:
        return "Uploaded files belong to a server, and this isn't one."

    query = (arguments.get("query") or "").strip()

    try:
        from bot.api.pages.client import PagesNotDeployed
        from bot.domain.files.file_service import search_files

        files, total = await search_files(str(guild.id), query=query, limit=MAX_RESULTS)
    except EnvironmentError:
        return "Uploads are not available (PAGES_BASE_URL / PAGES_PUBLISH_TOKEN not configured)."
    except PagesNotDeployed:
        return (
            "This server's page service is running an older version without "
            "uploads, so there is nothing to search."
        )
    except RuntimeError as e:
        return f"Could not search uploaded files: {e}"

    if not files:
        if query:
            return (
                f"No uploaded files match '{query}'. Try fewer words, or leave the "
                "query empty to see the latest uploads."
            )
        return "Nothing has been uploaded to the bot for this server yet (or it has all expired)."

    shown = len(files)
    head = (
        f"{total} uploaded file(s) match '{query}'" if query else f"{total} uploaded file(s)"
    )
    if total > shown:
        head += f", newest {shown} shown"
    return head + ":\n" + "\n".join(f"- {describe_file(f)}" for f in files)


TOOL = AgentTool(
    config_key="search_files",
    schema=SCHEMA,
    executor=execute_search_files,
    channel_aware=True,
)
