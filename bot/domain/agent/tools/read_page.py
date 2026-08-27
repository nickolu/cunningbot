"""The `read_page` agent tool."""

from typing import Any, Dict

import discord

from bot.app.utils.logger import get_logger
from bot.domain.agent.tools.base import AgentTool

logger = get_logger()


SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "read_page",
        "description": (
            "Read back the Markdown of a page already published for this "
            "server, so you can add to it and republish it under the same slug "
            "instead of creating a second page. Use it whenever someone asks "
            "to add to, fix, or extend an existing page."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "page": {
                    "type": "string",
                    "description": (
                        "The page's slug (from list_pages), or the URL if you "
                        "have it, e.g. 'restaurants' or "
                        "'https://.../p/restaurants-1a2b3c4d5e6f7890'"
                    ),
                },
            },
            "required": ["page"],
        },
    },
}


async def execute_read_page(
    arguments: Dict[str, Any], channel: discord.TextChannel
) -> str:
    """Execute the read_page tool."""
    reference = (arguments.get("page") or "").strip()
    if not reference:
        return "No page was named."

    guild = getattr(channel, "guild", None)
    if guild is None:
        return "Pages belong to a server, and this isn't one."

    try:
        from bot.api.pages.client import PagesNotDeployed
        from bot.domain.pages.page_service import read_page

        page = await read_page(str(guild.id), reference)
    except EnvironmentError:
        return "Pages are not available (PAGES_BASE_URL / PAGES_PUBLISH_TOKEN not configured)."
    except PagesNotDeployed:
        return (
            "This server's page service is running an older version that can't "
            "read pages back yet."
        )
    except RuntimeError as e:
        return f"Could not read that page: {e}"

    if page is None:
        return (
            f"No page named '{reference}' exists for this server. "
            "Call list_pages to see what does."
        )

    markdown = page.get("markdown")
    if markdown is None:
        # Publishing over this would silently drop whatever it says. Better to
        # say so and let the user decide than to "update" it into a stub.
        return (
            f"'{page['title']}' exists at {page['url']}, but it was published "
            "before pages kept their source, so I can't read its contents. "
            "Tell the user that republishing it will replace what's there "
            "rather than add to it, and ask how they want to proceed."
        )

    return (
        "Page '{title}' (slug: {slug}), last updated {updated}.\n"
        "Republish with publish_page using slug='{slug}' to update it in place.\n"
        "--- current contents ---\n{markdown}"
    ).format(
        title=page["title"],
        slug=page["slug"],
        updated=(page.get("updated_at") or "unknown")[:10],
        markdown=markdown,
    )


TOOL = AgentTool(
    config_key="read_page",
    schema=SCHEMA,
    executor=execute_read_page,
    channel_aware=True,
)
