"""The `publish_page` agent tool."""

import re
from typing import Any, Dict, List, Optional

import discord

from bot.app.utils.logger import get_logger
from bot.domain.agent.tools.base import AgentTool

logger = get_logger()


SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "publish_page",
        "description": (
            "Publish content as a web page and get back a shareable link. "
            "Use this when someone asks to see something 'as a page', wants a "
            "link they can share or read later, or when the answer is too long "
            "or too structured for a Discord message (a list, a table, a "
            "write-up, or an explanation of how you worked something out). "
            "Pass a slug to keep one stable URL that updates in place; omit it "
            "for a one-off snapshot."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Page title, shown as the heading (e.g. 'Restaurants to Visit')",
                },
                "markdown": {
                    "type": "string",
                    "description": (
                        "Page body in Markdown. Headings, lists, tables, links, "
                        "and code blocks are supported. Raw HTML is escaped, not rendered."
                    ),
                },
                "slug": {
                    "type": "string",
                    "description": (
                        "Optional short name for a page that should keep one URL "
                        "and be updated in place, e.g. 'restaurants' or 'lunch-rotation'. "
                        "Reuse the same slug to update that page. Omit for a one-off page."
                    ),
                },
            },
            "required": ["title", "markdown"],
        },
    },
}


UNPUBLISHABLE_IMAGE_HOSTS = ("cdn.discordapp.com", "media.discordapp.net")


def _unpublishable_image_refs(markdown: str) -> List[str]:
    """Return image references that cannot survive on a public page.

    ``attachment://`` is a Discord-internal embed scheme; Discord CDN links carry
    an expiring signature. Either one yields a page that is broken on arrival or
    broken by tomorrow.
    """
    found: List[str] = []
    for match in re.finditer(r"\((attachment://[^)\s]*|https?://[^)\s]+)\)", markdown):
        url = match.group(1)
        if url.startswith("attachment://"):
            found.append("attachment://…")
        elif any(h in url for h in UNPUBLISHABLE_IMAGE_HOSTS):
            found.append("a Discord CDN link")
    return sorted(set(found))


async def execute_publish_page(
    arguments: Dict[str, Any], channel: discord.TextChannel
) -> str:
    """Execute the publish_page tool."""
    title = (arguments.get("title") or "").strip()
    markdown = (arguments.get("markdown") or "").strip()
    slug = (arguments.get("slug") or "").strip() or None

    if not markdown:
        return "No page content was provided."

    bad = _unpublishable_image_refs(markdown)
    if bad:
        return (
            "That page references images that won't work on the web: "
            f"{', '.join(bad)}. "
            "attachment:// links only exist inside Discord, and Discord CDN links "
            "expire after about a day. Call host_image on each image first (or "
            "regenerate it with host=true) and use the permanent URLs instead."
        )

    guild = getattr(channel, "guild", None)
    if guild is None:
        return "Pages can only be published from inside a server."

    try:
        from bot.domain.pages.page_service import publish_page

        url = await publish_page(
            guild_id=str(guild.id),
            title=title or "Untitled",
            markdown=markdown,
            slug=slug,
            guild_name=guild.name,
        )
    except EnvironmentError:
        return "Publishing pages is not available (PAGES_BASE_URL / PAGES_PUBLISH_TOKEN not configured)."
    except RuntimeError as e:
        return f"Could not publish the page: {e}"

    return f"Published '{title or 'Untitled'}': {url}"


TOOL = AgentTool(
    config_key="publish_page",
    schema=SCHEMA,
    executor=execute_publish_page,
    channel_aware=True,
)
