"""HTTP client for the Perplexity Search API."""

from __future__ import annotations

import os
from typing import Any

import aiohttp

BASE_URL = "https://api.perplexity.ai/search"


class PerplexityClient:
    """Async client for the Perplexity Search API."""

    def __init__(self, timeout_seconds: int = 15) -> None:
        api_key = os.getenv("PERPLEXITY_API_KEY")
        if not api_key:
            raise EnvironmentError("PERPLEXITY_API_KEY environment variable is not set")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    async def search(self, query: str, max_results: int = 5) -> list[dict[str, Any]]:
        """Search the Perplexity API by query string."""
        clean_query = query.strip()
        if not clean_query:
            return []

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {"query": clean_query, "max_results": max_results}

        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    BASE_URL,
                    json=payload,
                    headers=headers,
                ) as response:
                    response.raise_for_status()
                    data = await response.json()
        except TimeoutError as exc:
            raise RuntimeError("Perplexity API request timed out.") from exc
        except aiohttp.ClientResponseError as exc:
            raise RuntimeError(
                f"Perplexity API returned HTTP {exc.status}."
            ) from exc
        except aiohttp.ClientError as exc:
            raise RuntimeError("Perplexity API request failed.") from exc

        results = data.get("results", [])
        if not isinstance(results, list):
            return []

        return results[:max_results]
