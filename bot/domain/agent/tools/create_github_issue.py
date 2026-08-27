"""The `create_github_issue` agent tool."""

from typing import Any, Dict

import discord

from bot.app.utils.logger import get_logger
from bot.domain.agent.tools.base import AgentTool

logger = get_logger()


SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "create_github_issue",
        "description": (
            "File an issue in this bot's own GitHub repository. Use it when "
            "someone reports a bug in the bot, asks for a feature, or says "
            "something like 'open an issue for that'. Do not use it to take "
            "notes or track things unrelated to the bot -- an issue is public "
            "and permanent. Ask for confirmation before filing if the request "
            "was not explicit."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": (
                        "One-line summary, specific enough to be useful in a list "
                        "(e.g. 'read_channel cannot page past 50 messages')"
                    ),
                },
                "body": {
                    "type": "string",
                    "description": (
                        "The issue in Markdown: what happened, what was expected, "
                        "and how to reproduce it if known. Include the relevant "
                        "detail from the conversation -- whoever reads this will "
                        "not have the Discord thread."
                    ),
                },
            },
            "required": ["title", "body"],
        },
    },
}


async def execute_create_github_issue(
    arguments: Dict[str, Any], channel: discord.TextChannel
) -> str:
    """Execute the create_github_issue tool."""
    title = (arguments.get("title") or "").strip()
    body = (arguments.get("body") or "").strip()

    guild = getattr(channel, "guild", None)
    if guild is None:
        return "Issues can only be filed from inside a server."

    try:
        from bot.domain.github.issue_service import (
            GitHubConfigError,
            IssueRejected,
            create_issue,
        )

        url = await create_issue(
            title=title,
            body=body,
            guild_id=str(guild.id),
            guild_name=getattr(guild, "name", "a Discord server"),
            channel_name=getattr(channel, "name", "unknown"),
            channel_url=getattr(channel, "jump_url", None),
        )
    except GitHubConfigError:
        return (
            "Filing GitHub issues is not available "
            "(GITHUB_TOKEN / GITHUB_ISSUE_REPO not configured)."
        )
    except IssueRejected as e:
        return "I did not file that: %s." % e
    except RuntimeError as e:
        return "Could not file the issue: %s" % e

    return "Filed the issue: %s" % url


TOOL = AgentTool(
    config_key="create_github_issue",
    schema=SCHEMA,
    executor=execute_create_github_issue,
    channel_aware=True,
    # Opt-in. This writes to a public repository under the token owner's name,
    # so it should be a deliberate per-channel choice rather than something
    # every registered agent -- and every unregistered channel -- can do.
    default_enabled=False,
)
