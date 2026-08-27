"""HTTP client for the GitHub Issues API."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import aiohttp

API_ROOT = "https://api.github.com"

#: GitHub's own cap on an issue title.
MAX_TITLE_CHARS = 256
#: Well under GitHub's 65536, and far past anything a Discord message produces.
MAX_BODY_CHARS = 60000


class GitHubConfigError(EnvironmentError):
    """The bot is not configured to file issues."""


class GitHubClient:
    """Async client for creating issues in one pinned repository.

    The repository is read from the environment, never from a caller. An agent
    tool takes its arguments from a language model reading a public Discord
    channel; letting that choose the target repo would let anyone in the server
    file issues anywhere this token can write.
    """

    def __init__(self, timeout_seconds: int = 15) -> None:
        token = os.getenv("GITHUB_TOKEN")
        if not token:
            raise GitHubConfigError("GITHUB_TOKEN environment variable is not set")
        repo = (os.getenv("GITHUB_ISSUE_REPO") or "").strip()
        if not repo or repo.count("/") != 1 or repo.startswith("/") or repo.endswith("/"):
            raise GitHubConfigError(
                "GITHUB_ISSUE_REPO must be set to 'owner/repo' (got %r)" % repo
            )
        self.token = token
        self.repo = repo
        self.timeout_seconds = timeout_seconds

    @property
    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": "Bearer %s" % self.token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "CunningBot",
        }

    async def create_issue(
        self,
        title: str,
        body: str,
        labels: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Create an issue. Returns the created issue as GitHub reports it.

        Raises RuntimeError with a readable message on any non-success.
        """
        payload: Dict[str, Any] = {
            "title": title[:MAX_TITLE_CHARS],
            "body": body[:MAX_BODY_CHARS],
        }
        if labels:
            payload["labels"] = labels

        url = "%s/repos/%s/issues" % (API_ROOT, self.repo)
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload, headers=self._headers) as response:
                    data = await response.json(content_type=None)
                    if response.status == 201:
                        return data or {}
                    raise RuntimeError(self._describe_error(response.status, data))
        except aiohttp.ClientError as exc:
            raise RuntimeError("could not reach GitHub (%s)" % exc)

    def _describe_error(self, status: int, data: Any) -> str:
        """Turn a GitHub error into something worth showing a Discord user."""
        message = ""
        if isinstance(data, dict):
            message = str(data.get("message") or "")
        if status == 401:
            return "GitHub rejected the token (401). It may be expired."
        if status == 403:
            return "GitHub refused the request (403). The token may lack issue write access, or the rate limit is exhausted."
        if status == 404:
            return (
                "GitHub returned 404 for %s. Either the repo name is wrong or the "
                "token cannot see it." % self.repo
            )
        if status == 410:
            return "Issues are disabled for %s." % self.repo
        if status == 422:
            return "GitHub rejected the issue as invalid: %s" % (message or "unprocessable")
        return "GitHub returned %d%s" % (status, ": %s" % message if message else "")
