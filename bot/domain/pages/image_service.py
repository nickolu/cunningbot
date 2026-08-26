"""Hosting images at stable URLs.

Discord CDN links carry an expiring signature and stop working roughly a day
after they are issued, which is fine while a conversation is live -- the bot
re-reads history and gets freshly signed URLs each time -- but breaks anything
that *stores* a URL. A published page embedding a Discord attachment looks
correct when it is created and has broken images the next day.

Hosting is therefore on demand: call this when a URL needs to outlive the
conversation, not for every generated image.
"""

from __future__ import annotations

from typing import Optional, Tuple

import aiohttp

from bot.api.pages.client import PagesClient
from bot.domain.pages.page_ids import derive_guild_prefix
from bot.app.utils.logger import get_logger

logger = get_logger()

MAX_IMAGE_BYTES = 4 * 1024 * 1024
SUPPORTED_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}


async def host_image_bytes(
    guild_id: str,
    image_bytes: bytes,
    content_type: str = "image/png",
    filename: Optional[str] = None,
) -> str:
    """Upload image bytes and return a stable public URL."""
    if not image_bytes:
        raise RuntimeError("No image data to host.")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise RuntimeError("That image is too large to host (4 MB limit).")
    if content_type not in SUPPORTED_TYPES:
        raise RuntimeError(f"Unsupported image type: {content_type}.")

    client = PagesClient()
    result = await client.upload_image(
        image_bytes=image_bytes,
        content_type=content_type,
        guild_id=guild_id,
        guild_prefix=derive_guild_prefix(guild_id),
        filename=filename,
    )

    logger.info({
        "event": "image_hosted",
        "guild_id": guild_id,
        "bytes": result.get("bytes"),
        "pathname": result.get("pathname"),
    })

    return str(result["url"])


async def fetch_image(url: str, timeout_seconds: int = 20) -> Tuple[bytes, str]:
    """Fetch an image URL, returning (bytes, content_type).

    Used to pull a Discord attachment before it is re-hosted. Discord's signed
    URLs are valid when freshly read from message history, so this must run
    promptly after the URL is obtained.
    """
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as response:
                if response.status == 404:
                    raise RuntimeError(
                        "That image link has expired or is no longer available."
                    )
                response.raise_for_status()
                content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip()
                data = await response.read()
    except TimeoutError as exc:
        raise RuntimeError("Fetching the image timed out.") from exc
    except aiohttp.ClientResponseError as exc:
        raise RuntimeError(f"Could not fetch the image (HTTP {exc.status}).") from exc
    except aiohttp.ClientError as exc:
        raise RuntimeError("Could not fetch the image.") from exc

    if len(data) > MAX_IMAGE_BYTES:
        raise RuntimeError("That image is too large to host (4 MB limit).")

    return data, content_type or "image/png"


async def host_image_from_url(
    guild_id: str, url: str, filename: Optional[str] = None
) -> str:
    """Fetch an image from ``url`` and re-host it at a stable public URL."""
    data, content_type = await fetch_image(url)
    return await host_image_bytes(
        guild_id, data, content_type=content_type, filename=filename
    )
