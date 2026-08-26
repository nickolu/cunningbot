"""HTTP client for the CunningBot Pages service (the Vercel app in ``web/``)."""

from __future__ import annotations

import os
from typing import Any, Optional

import aiohttp


class PagesClient:
    """Async client that publishes rendered HTML and returns its public URL."""

    def __init__(self, timeout_seconds: int = 15) -> None:
        base_url = os.getenv("PAGES_BASE_URL")
        token = os.getenv("PAGES_PUBLISH_TOKEN")
        if not base_url:
            raise EnvironmentError("PAGES_BASE_URL environment variable is not set")
        if not token:
            raise EnvironmentError("PAGES_PUBLISH_TOKEN environment variable is not set")

        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds

    async def publish(
        self,
        page_id: str,
        guild_id: str,
        title: str,
        html: str,
        ttl_days: Optional[int] = None,
    ) -> dict[str, Any]:
        """Publish ``html`` at ``page_id``. Returns the service's JSON response.

        Republishing the same ``page_id`` overwrites the page in place and
        resets its expiry, which is how a list page keeps one stable URL.
        """
        payload: dict[str, Any] = {
            "id": page_id,
            "guild_id": guild_id,
            "title": title,
            "html": html,
        }
        if ttl_days is not None:
            payload["ttl_days"] = ttl_days

        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{self.base_url}/api/publish",
                    json=payload,
                    headers=headers,
                ) as response:
                    if response.status == 409:
                        raise RuntimeError(
                            "That page id already belongs to another server."
                        )
                    response.raise_for_status()
                    data = await response.json()
        except TimeoutError as exc:
            raise RuntimeError("Publishing the page timed out.") from exc
        except aiohttp.ClientResponseError as exc:
            raise RuntimeError(f"Page service returned HTTP {exc.status}.") from exc
        except aiohttp.ClientError as exc:
            raise RuntimeError("Could not reach the page service.") from exc

        if not isinstance(data, dict) or not data.get("url"):
            raise RuntimeError("Page service returned an unexpected response.")

        return data
