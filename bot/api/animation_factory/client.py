"""HTTP client for Animation Factory GIF search API."""

from __future__ import annotations

from typing import Any

import aiohttp

AF_BASE_URL = "https://manchat.men"
AF_SEARCH_PATH = "/af/api/search"


class AnimationFactoryClient:
    """Fetch GIF search results from Animation Factory."""

    def __init__(self, timeout_seconds: int = 10) -> None:
        self.timeout_seconds = timeout_seconds

    async def search(self, query: str, limit: int = 12) -> list[dict[str, Any]]:
        """Search Animation Factory GIFs by query string."""
        clean_query = query.strip()
        if not clean_query:
            return []

        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    f"{AF_BASE_URL}{AF_SEARCH_PATH}",
                    params={"q": clean_query},
                ) as response:
                    response.raise_for_status()
                    payload = await response.json()
        except TimeoutError as exc:
            raise RuntimeError("Animation Factory request timed out.") from exc
        except aiohttp.ClientResponseError as exc:
            raise RuntimeError(
                f"Animation Factory API returned HTTP {exc.status}."
            ) from exc
        except aiohttp.ClientError as exc:
            raise RuntimeError("Animation Factory API request failed.") from exc

        results = payload.get("results", [])
        if not isinstance(results, list):
            return []

        return results[: max(0, limit)]
