"""The `scan_channel_history` agent tool.

`read_channel` reads the last few dozen messages. This starts a job that pages
through a channel's *whole* history — minutes to hours of work — applying a
natural-language instruction to every page and collecting what it finds. The
tool does not wait for it: it starts the scan, posts a status message in the
channel being scanned, and returns immediately. The scan posts its own report
when it is done (`bot/app/scan_ux.py`).

It is owner-only (`bot/app/scan_access.py`), `default_enabled=False`, and not in
`DEFAULT_TOOLS_TO_ADD`: a scan costs a model call per hundred messages, so it is
turned on per channel with `/agent tool scan_channel_history enable`.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import discord

from bot.app.scan_access import REFUSAL, can_start_scan
from bot.app.scan_runtime import ScanAlreadyRunning, start_scan
from bot.app.scan_ux import make_callbacks, post_status
from bot.app.utils.logger import get_logger
from bot.domain.agent.tools.base import AgentTool
from bot.domain.scan import report

logger = get_logger()


SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "scan_channel_history",
        "description": (
            "Start a background scan of a channel's entire message history, "
            "collecting whatever the instruction asks for (restaurants someone "
            "mentioned, images a person posted, decisions that were made). Use "
            "this when someone wants the whole history searched, or much more "
            "than the last couple hundred messages; use read_channel for small "
            "look-backs. The scan runs for minutes to hours and posts its own "
            "results in the channel when it finishes -- this returns as soon as "
            "it has started, so never claim to have the results yet."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "instruction": {
                    "type": "string",
                    "description": (
                        "What to look for, in a sentence, as an instruction applied "
                        "to every page of history, e.g. 'collect every restaurant "
                        "anyone recommended, with who recommended it'."
                    ),
                },
                "channel_name": {
                    "type": "string",
                    "description": (
                        "Name or ID of the channel to scan. Defaults to the current channel."
                    ),
                },
                "after": {
                    "type": "string",
                    "description": (
                        "Only scan messages newer than this: a message ID, a message "
                        "link, or an ISO date such as 2026-01-01."
                    ),
                },
                "before": {
                    "type": "string",
                    "description": (
                        "Only scan messages older than this: a message ID, a message "
                        "link, or an ISO date such as 2026-06-30."
                    ),
                },
            },
            "required": ["instruction"],
        },
    },
}


def parse_bound(value: Any) -> Tuple[Optional[int], Optional[str]]:
    """A message id, message link, or ISO date as a snowflake.

    Returns (snowflake, error). A date becomes the snowflake for midnight UTC
    that day, which is what Discord's own before/after take.
    """
    text = str(value or "").strip()
    if not text:
        return None, None

    if "discord.com/channels/" in text or "discordapp.com/channels/" in text:
        tail = text.rstrip("/").rsplit("/", 1)[-1]
        if tail.isdigit():
            return int(tail), None
        return None, "'{}' is not a message link I can read.".format(text)

    if text.isdigit():
        return int(text), None

    iso = text.rstrip("Zz") + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        moment = datetime.fromisoformat(iso)
    except ValueError:
        return None, (
            "'{}' isn't a message ID, a message link, or an ISO date "
            "(like 2026-01-01).".format(text)
        )
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return discord.utils.time_snowflake(moment), None


def resolve_channel(
    guild: discord.Guild, name: str, current: Any
) -> Tuple[Optional[Any], Optional[str]]:
    """The channel to scan: the named one, or the current one when unnamed."""
    wanted = str(name or "").strip().lstrip("#")
    if not wanted:
        return current, None

    if wanted.isdigit():
        found = guild.get_channel(int(wanted))
        if isinstance(found, (discord.TextChannel, discord.Thread)):
            return found, None

    channels = list(guild.text_channels) + list(getattr(guild, "threads", []))
    for match in (
        lambda c: c.name == wanted,
        lambda c: c.name.lower() == wanted.lower(),
        lambda c: wanted.lower() in c.name.lower(),
    ):
        for candidate in channels:
            if match(candidate):
                return candidate, None

    available = ", ".join(c.name for c in channels[:20])
    return None, "Could not find a channel matching '{}'. Channels here: {}".format(
        wanted, available
    )


async def execute_scan_channel_history(
    arguments: Dict[str, Any],
    channel: discord.TextChannel,
    user: Any,
) -> str:
    """Start a history scan and return straight away — never wait for it."""
    instruction = str(arguments.get("instruction") or "").strip()
    if not instruction:
        return "No instruction was given, so there is nothing to look for."

    if not await can_start_scan(user):
        logger.info({
            "event": "scan_refused",
            "user": str(getattr(user, "id", None)),
            "channel": str(getattr(channel, "id", None)),
        })
        return REFUSAL

    guild = getattr(channel, "guild", None)
    if guild is None:
        return "A channel scan only works inside a server."

    target, error = resolve_channel(guild, arguments.get("channel_name"), channel)
    if target is None:
        return error or "Could not find that channel."

    me = getattr(guild, "me", None)
    if me is not None:
        perms = target.permissions_for(me)
        if not perms.read_message_history:
            return "I can't read the history of #{}.".format(target.name)

    after, error = parse_bound(arguments.get("after"))
    if error:
        return error
    before, error = parse_bound(arguments.get("before"))
    if error:
        return error

    on_progress, on_finish = make_callbacks(target)
    try:
        job = await start_scan(
            target,
            int(getattr(user, "id", 0)),
            instruction,
            before=before,
            after=after,
            on_progress=on_progress,
            on_finish=on_finish,
        )
    except ScanAlreadyRunning as e:
        return report.already_running_text(e.job)
    except Exception as e:
        logger.error(f"scan_channel_history could not start a scan: {e}")
        return "Could not start the scan: {}".format(e)

    await post_status(target, job)
    logger.info({
        "event": "scan_started",
        "job": job.get("job_id"),
        "channel": str(target.id),
        "requester": str(getattr(user, "id", None)),
        "instruction": report.short_instruction(instruction),
    })
    return (
        "Started scanning #{} for {}. It runs in the background and can take a "
        "while; I'll post the results in #{} when it's done. Tell them it has "
        "started and that they can stop it by saying \"stop\" or reacting 🛑 — do "
        "not pretend to have any results yet.".format(
            target.name, report.short_instruction(instruction), target.name
        )
    )


TOOL = AgentTool(
    config_key="scan_channel_history",
    schema=SCHEMA,
    executor=execute_scan_channel_history,
    channel_aware=True,
    user_aware=True,
    default_enabled=False,
    # A scan starts hours of background work; a scheduled run shouldn't.
    scheduled_ok=False,
)
