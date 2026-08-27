"""Publishing pages to the public Pages service.

The single entry point used by both the agent tool and any slash command that
wants to hand someone a link. Everything guild-scoped happens here: the caller
supplies a guild id and a human name for the page, and gets back a URL that no
other guild can derive.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from bot.api.pages.client import PagesClient
from bot.domain.pages.page_ids import (
    derive_page_id,
    looks_like_page_id,
    slug_from_page_id,
    slugify,
)
from bot.domain.pages.page_renderer import render_page
from bot.app.utils.logger import get_logger

logger = get_logger()

#: A one-off snapshot -- a reasoning trace, a weekly summary -- has served its
#: purpose long before this.
SNAPSHOT_TTL_DAYS = 30
#: A page with a slug is a living document someone may have linked to. Dying a
#: month after its last edit is the opposite of what it is for, so it gets the
#: longest the service allows.
STABLE_TTL_DAYS = 365
DEFAULT_TTL_DAYS = SNAPSHOT_TTL_DAYS


async def publish_page(
    guild_id: str,
    title: str,
    markdown: str,
    slug: Optional[str] = None,
    guild_name: Optional[str] = None,
    ttl_days: Optional[int] = None,
    one_off: bool = False,
) -> str:
    """Render ``markdown`` and publish it. Returns the public URL.

    ``slug`` makes the URL stable and reusable — pass one (e.g. ``"restaurants"``)
    for a living document that should be republished in place, and omit it for a
    one-off snapshot that should get its own URL.

    Raises ``EnvironmentError`` if publishing isn't configured and ``RuntimeError``
    if the service rejects or can't be reached.
    """
    # Without this, an unslugged page got a random id nobody -- including the
    # bot -- could ever derive again, so "add that to the list" could only
    # publish a second page. A title makes a perfectly good slug.
    if not slug and not one_off:
        slug = slugify(title)

    page_id = derive_page_id(guild_id, slug)
    if ttl_days is None:
        ttl_days = STABLE_TTL_DAYS if slug else SNAPSHOT_TTL_DAYS
    footer = f"Published by CunningBot for {guild_name}" if guild_name else None
    html = render_page(title, markdown, footer=footer)

    client = PagesClient()
    result = await client.publish(
        page_id=page_id,
        guild_id=guild_id,
        title=title,
        html=html,
        markdown=markdown,
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


async def list_pages(guild_id: str) -> List[Dict[str, Any]]:
    """Every page this guild has published, newest first.

    Each entry carries the readable slug as well as the id, because the slug is
    what a person says out loud ("the wishlist page") and what `read_page`
    takes.
    """
    client = PagesClient()
    pages = await client.list_pages(guild_id)

    summaries: List[Dict[str, Any]] = []
    for page in pages:
        page_id = str(page.get("id") or "")
        if not page_id:
            continue
        summaries.append({
            "slug": slug_from_page_id(page_id),
            "title": page.get("title") or slug_from_page_id(page_id),
            "url": client.page_url(page_id),
            "updated_at": page.get("updated_at"),
            "has_source": bool(page.get("has_source")),
        })
    return summaries


async def read_page(guild_id: str, reference: str) -> Optional[Dict[str, Any]]:
    """Read back a page by slug, page id, or its URL.

    Returns None when there is no such page. ``markdown`` is None for a page
    published before source was stored -- the caller must not treat that as an
    empty page, or it will replace content it never saw.
    """
    reference = (reference or "").strip()
    if not reference:
        return None

    # Accept the URL the agent shared earlier as readily as a slug.
    if "/" in reference:
        reference = reference.rstrip("/").rsplit("/", 1)[-1]

    page_id = reference if looks_like_page_id(reference) else derive_page_id(
        guild_id, reference
    )

    client = PagesClient()
    record = await client.fetch_source(page_id, guild_id)
    if record is None:
        return None

    return {
        "slug": slug_from_page_id(page_id),
        "title": record.get("title") or "",
        "markdown": record.get("markdown"),
        "url": client.page_url(page_id),
        "updated_at": record.get("updated_at"),
    }
