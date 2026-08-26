"""Publishing pages to the public Pages service.

The single entry point used by both the agent tool and any slash command that
wants to hand someone a link. Everything guild-scoped happens here: the caller
supplies a guild id and a human name for the page, and gets back a URL that no
other guild can derive.
"""

from __future__ import annotations

from typing import Optional

from bot.api.pages.client import PagesClient
from bot.domain.pages.page_ids import derive_page_id
from bot.domain.pages.page_renderer import render_page
from bot.app.utils.logger import get_logger

logger = get_logger()

DEFAULT_TTL_DAYS = 30


async def publish_page(
    guild_id: str,
    title: str,
    markdown: str,
    slug: Optional[str] = None,
    guild_name: Optional[str] = None,
    ttl_days: int = DEFAULT_TTL_DAYS,
) -> str:
    """Render ``markdown`` and publish it. Returns the public URL.

    ``slug`` makes the URL stable and reusable — pass one (e.g. ``"restaurants"``)
    for a living document that should be republished in place, and omit it for a
    one-off snapshot that should get its own URL.

    Raises ``EnvironmentError`` if publishing isn't configured and ``RuntimeError``
    if the service rejects or can't be reached.
    """
    page_id = derive_page_id(guild_id, slug)
    footer = f"Published by CunningBot for {guild_name}" if guild_name else None
    html = render_page(title, markdown, footer=footer)

    client = PagesClient()
    result = await client.publish(
        page_id=page_id,
        guild_id=guild_id,
        title=title,
        html=html,
        ttl_days=ttl_days,
    )

    logger.info({
        "event": "page_published",
        "guild_id": guild_id,
        "page_id": page_id,
        "slug": slug,
        "updated": result.get("updated"),
        "bytes": len(html),
    })

    return str(result["url"])
