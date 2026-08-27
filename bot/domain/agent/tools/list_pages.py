"""The `list_pages` agent tool."""

from typing import Any, Dict

import discord

from bot.app.utils.logger import get_logger
from bot.domain.agent.tools.base import AgentTool

logger = get_logger()


SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "list_pages",
        "description": (
            "List the web pages already published for this server, newest "
            "first. Call this BEFORE publish_page whenever the request sounds "
            "like adding to, correcting, or continuing something that may "
            "already exist ('add this to the list', 'update the wishlist'). "
            "Publishing without checking creates a second page instead of "
            "updating the first."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


async def execute_list_pages(
    arguments: Dict[str, Any], channel: discord.TextChannel
) -> str:
    """Execute the list_pages tool."""
    guild = getattr(channel, "guild", None)
    if guild is None:
        return "Pages belong to a server, and this isn't one."

    try:
        from bot.api.pages.client import PagesNotDeployed
        from bot.domain.pages.page_service import list_pages

        pages = await list_pages(str(guild.id))
    except EnvironmentError:
        return "Pages are not available (PAGES_BASE_URL / PAGES_PUBLISH_TOKEN not configured)."
    except PagesNotDeployed:
        return (
            "This server's page service is running an older version that can't "
            "list pages yet. Ask the user for the page link if you need one."
        )
    except RuntimeError as e:
        return f"Could not list pages: {e}"

    if not pages:
        return "No pages have been published for this server yet."

    lines = []
    for page in pages:
        updated = (page.get("updated_at") or "")[:10]
        when = f" — updated {updated}" if updated else ""
        note = "" if page.get("has_source") else "  [no stored source]"
        lines.append(
            f"- {page['title']} (slug: {page['slug']}){when}{note}\n  {page['url']}"
        )

    return (
        f"{len(pages)} page(s) published for this server, newest first. "
        "Use read_page with a slug before republishing one:\n" + "\n".join(lines)
    )


TOOL = AgentTool(
    config_key="list_pages",
    schema=SCHEMA,
    executor=execute_list_pages,
    channel_aware=True,
)
