"""HTTP client for the CunningBot Pages service (the Vercel app in ``web/``)."""

from __future__ import annotations

import os
from typing import Any, Optional

import aiohttp


class PagesNotDeployed(RuntimeError):
    """The pages service is reachable but predates the discovery endpoints.

    `web/` deploys separately from the bot, so the bot can ship first. When it
    does, /api/pages does not exist and Vercel answers with its own HTML 404
    rather than the API's JSON one -- which is how the two are told apart.
    """


class UploadsNotConfigured(RuntimeError):
    """The pages service has the upload routes but no storage bucket behind them.

    Setting the bucket up is a one-time job on Vercel (``web/README.md``); until
    then the service answers 503 and the bot says uploads aren't available.
    """


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

    async def upload_image(
        self,
        image_bytes: bytes,
        content_type: str,
        guild_id: str,
        guild_prefix: str,
        filename: Optional[str] = None,
    ) -> dict[str, Any]:
        """Upload image bytes to blob storage. Returns the service's JSON response.

        Unlike pages, uploads are never overwritten -- each call stores a new
        object with a random suffix, so a returned URL is stable forever.
        """
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": content_type,
            "X-Guild-Id": guild_id,
            "X-Guild-Prefix": guild_prefix,
        }
        if filename:
            headers["X-Filename"] = filename

        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds * 2)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{self.base_url}/api/upload",
                    data=image_bytes,
                    headers=headers,
                ) as response:
                    if response.status == 413:
                        raise RuntimeError("That image is too large to host.")
                    if response.status == 415:
                        raise RuntimeError(f"Unsupported image type: {content_type}.")
                    response.raise_for_status()
                    data = await response.json()
        except TimeoutError as exc:
            raise RuntimeError("Uploading the image timed out.") from exc
        except aiohttp.ClientResponseError as exc:
            raise RuntimeError(f"Image service returned HTTP {exc.status}.") from exc
        except aiohttp.ClientError as exc:
            raise RuntimeError("Could not reach the image service.") from exc

        if not isinstance(data, dict) or not data.get("url"):
            raise RuntimeError("Image service returned an unexpected response.")

        return data

    async def publish(
        self,
        page_id: str,
        guild_id: str,
        title: str,
        html: str,
        markdown: Optional[str] = None,
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
        if markdown is not None:
            payload["markdown"] = markdown
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

    def page_url(self, page_id: str) -> str:
        """The public URL a page id resolves to."""
        return f"{self.base_url}/p/{page_id}"

    async def _get_json(self, params: dict[str, str]) -> Optional[dict[str, Any]]:
        """GET /api/pages. Returns None when the service reports no such page."""
        headers = {"Authorization": f"Bearer {self.token}"}
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    f"{self.base_url}/api/pages",
                    params=params,
                    headers=headers,
                ) as response:
                    if response.status == 404:
                        # The API answers 404 as JSON; Vercel answers for a route
                        # it does not have with HTML. Only the former means the
                        # page is missing -- the latter means the feature is.
                        if response.content_type == "application/json":
                            return None
                        raise PagesNotDeployed(
                            "The pages service has no /api/pages route yet."
                        )
                    response.raise_for_status()
                    data = await response.json()
        except TimeoutError as exc:
            raise RuntimeError("The page service timed out.") from exc
        except aiohttp.ClientResponseError as exc:
            raise RuntimeError(f"Page service returned HTTP {exc.status}.") from exc
        except aiohttp.ClientError as exc:
            raise RuntimeError("Could not reach the page service.") from exc

        if not isinstance(data, dict):
            raise RuntimeError("Page service returned an unexpected response.")
        return data

    async def list_pages(self, guild_id: str) -> list[dict[str, Any]]:
        """Every page this guild has published, newest first."""
        data = await self._get_json({"guild_id": guild_id})
        if data is None:
            return []
        pages = data.get("pages")
        return pages if isinstance(pages, list) else []

    async def fetch_source(
        self, page_id: str, guild_id: str
    ) -> Optional[dict[str, Any]]:
        """One page's stored Markdown, or None if there is no such page."""
        return await self._get_json({"guild_id": guild_id, "id": page_id})

    # ------------------------------------------------------------------
    # User uploads (web/api/upload-sessions.js, web/api/files.js)
    # ------------------------------------------------------------------

    def upload_url(self, session_id: str) -> str:
        """The public URL an upload session's page lives at."""
        return f"{self.base_url}/u/{session_id}"

    def file_url(self, file_id: str) -> str:
        """The stable download URL for an uploaded file."""
        return f"{self.base_url}/f/{file_id}"

    async def _call(
        self,
        method: str,
        path: str,
        params: Optional[dict[str, str]] = None,
        json: Optional[dict[str, Any]] = None,
        what: str = "page service",
    ) -> tuple[int, Any]:
        """One request to the service. Returns ``(status, parsed json or None)``.

        A 404 answered as HTML is Vercel saying the route does not exist, which
        means ``web/`` has not been deployed with it yet -- reported as
        ``PagesNotDeployed`` so callers can say so instead of erroring. A JSON
        404 is the API's own answer and is returned to the caller.
        """
        headers = {"Authorization": f"Bearer {self.token}"}
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.request(
                    method,
                    f"{self.base_url}{path}",
                    params=params,
                    json=json,
                    headers=headers,
                ) as response:
                    if response.status == 404 and response.content_type != "application/json":
                        raise PagesNotDeployed(
                            f"The pages service has no {path} route yet."
                        )
                    data: Any = None
                    if response.content_type == "application/json":
                        data = await response.json()
                    return response.status, data
        except TimeoutError as exc:
            raise RuntimeError(f"The {what} timed out.") from exc
        except aiohttp.ClientError as exc:
            raise RuntimeError(f"Could not reach the {what}.") from exc

    async def create_upload_session(
        self,
        guild_id: str,
        channel_id: Optional[str] = None,
        channel_name: Optional[str] = None,
        requester_id: Optional[str] = None,
        requester_name: Optional[str] = None,
        label: Optional[str] = None,
        kind: str = "any",
        max_files: Optional[int] = None,
        max_bytes: Optional[int] = None,
        ttl_days: Optional[int] = None,
    ) -> dict[str, Any]:
        """Open an upload session. Returns ``{session_id, url, expires_at, ...}``."""
        payload: dict[str, Any] = {"guild_id": guild_id, "kind": kind}
        for key, value in (
            ("channel_id", channel_id),
            ("channel_name", channel_name),
            ("requester_id", requester_id),
            ("requester_name", requester_name),
            ("label", label),
            ("max_files", max_files),
            ("max_bytes", max_bytes),
            ("ttl_days", ttl_days),
        ):
            if value is not None:
                payload[key] = value

        status, data = await self._call(
            "POST", "/api/upload-sessions", json=payload, what="upload service"
        )
        if status == 503:
            raise UploadsNotConfigured("The upload service has no storage bucket configured.")
        if status == 400:
            error = data.get("error") if isinstance(data, dict) else None
            raise RuntimeError(f"The upload service refused the request ({error or 'bad request'}).")
        if status != 200:
            raise RuntimeError(f"Upload service returned HTTP {status}.")
        if not isinstance(data, dict) or not data.get("url") or not data.get("session_id"):
            raise RuntimeError("Upload service returned an unexpected response.")
        return data

    async def get_upload_session(
        self, session_id: str, guild_id: str
    ) -> Optional[dict[str, Any]]:
        """``{session, files}`` for a session, or None once it is forgotten."""
        status, data = await self._call(
            "GET",
            "/api/upload-sessions",
            params={"id": session_id, "guild_id": guild_id},
            what="upload service",
        )
        if status == 404:
            return None
        if status != 200 or not isinstance(data, dict):
            raise RuntimeError(f"Upload service returned HTTP {status}.")
        return data

    async def list_files(
        self, guild_id: str, query: str = "", limit: int = 25
    ) -> dict[str, Any]:
        """``{files: [...], total: n}`` for the guild, newest first."""
        params = {"guild_id": guild_id, "limit": str(limit)}
        if query:
            params["q"] = query
        status, data = await self._call("GET", "/api/files", params=params, what="file catalog")
        if status != 200 or not isinstance(data, dict):
            raise RuntimeError(f"File catalog returned HTTP {status}.")
        return data

    async def delete_file(self, guild_id: str, file_id: str) -> bool:
        """Delete one file. False when there is no such file in this guild."""
        status, data = await self._call(
            "DELETE",
            "/api/files",
            params={"guild_id": guild_id, "id": file_id},
            what="file catalog",
        )
        if status == 404:
            return False
        if status != 200:
            raise RuntimeError(f"File catalog returned HTTP {status}.")
        return bool(isinstance(data, dict) and data.get("deleted"))
