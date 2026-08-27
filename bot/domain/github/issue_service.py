"""Filing GitHub issues on behalf of a Discord channel."""

from __future__ import annotations

from typing import Optional

from bot.api.github.client import (
    MAX_BODY_CHARS,
    GitHubClient,
    GitHubConfigError,
)
from bot.app.redis.github_store import ISSUES_PER_HOUR, GitHubRedisStore
from bot.app.utils.logger import get_logger

logger = get_logger()

MIN_TITLE_CHARS = 8


class IssueRejected(Exception):
    """The request will not be filed, with a reason worth showing the user."""


def _build_body(body: str, guild_name: str, channel_name: str, channel_url: Optional[str]) -> str:
    """Append provenance, so a repo reader knows where an issue came from.

    Issues arrive under whatever account owns GITHUB_TOKEN, which makes every
    one of them look like the repo owner wrote it. The footer is the only thing
    distinguishing "someone in Discord asked for this" from "the maintainer
    filed this".
    """
    footer = "Filed by CunningBot from #%s in %s." % (channel_name, guild_name)
    if channel_url:
        footer += "\n%s" % channel_url
    return "%s\n\n---\n%s" % (body.strip(), footer)


async def create_issue(
    title: str,
    body: str,
    guild_id: str,
    guild_name: str,
    channel_name: str,
    channel_url: Optional[str] = None,
) -> str:
    """File an issue and return its URL.

    Raises GitHubConfigError when filing is not configured, IssueRejected for
    anything the caller could fix, and RuntimeError when GitHub refuses.
    """
    title = (title or "").strip()
    body = (body or "").strip()

    if len(title) < MIN_TITLE_CHARS:
        raise IssueRejected(
            "an issue title needs at least %d characters" % MIN_TITLE_CHARS
        )
    if not body:
        raise IssueRejected("an issue needs a description, not just a title")
    if len(body) > MAX_BODY_CHARS:
        raise IssueRejected("that description is too long to file")

    # Constructed first: a missing token should read as "not configured"
    # rather than silently burning one of the guild's hourly slots.
    client = GitHubClient()

    store = GitHubRedisStore()
    if not await store.claim_issue_slot(guild_id):
        raise IssueRejected(
            "this server has already filed %d issues in the past hour" % ISSUES_PER_HOUR
        )

    issue = await client.create_issue(
        title=title,
        body=_build_body(body, guild_name, channel_name, channel_url),
    )
    url = str(issue.get("html_url") or "")
    number = issue.get("number")
    logger.info(
        "github_issue_created",
        extra={"guild_id": guild_id, "issue_number": number, "url": url},
    )
    return url


__all__ = ["create_issue", "IssueRejected", "GitHubConfigError"]
