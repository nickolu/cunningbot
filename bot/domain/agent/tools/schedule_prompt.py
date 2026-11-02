"""The `schedule_prompt` agent tool: set up a prompt that runs on a schedule.

Two steps, so a wrong parse is caught before anything runs:

1. `step="draft"` checks the prompt, the schedule, and the caps, saves a draft
   for the channel, and returns a read-back in plain words ("weekdays at 9:00 AM
   (Pacific time)", plus the next three runs). It also puts Confirm / Change
   time / Cancel buttons under the reply itself, rather than trusting the model
   to call `suggest_replies`.
2. `step="confirm"` -- after someone clicks Confirm or says yes -- creates the
   job from the channel's draft. The model passes nothing else, so it can't
   re-derive the schedule differently the second time. Whoever confirms becomes
   the creator, and the caps count against them (decided 2026-09-20).

`step="discard"` drops the draft (the Cancel button).

Scheduled runs can't use it (`scheduled_ok=False`): a job must not create jobs.
"""

from datetime import datetime, timezone
from typing import Any, Dict

import discord

from bot.app.redis.schedule_store import ScheduleRedisStore
from bot.app.schedule_jobs import check_caps, check_prompt, create_scheduled_prompt
from bot.app.utils.logger import get_logger
from bot.domain.agent.suggestions import offer_suggestions
from bot.domain.agent.tools.base import AgentTool
from bot.domain.schedule.cron import ScheduleError, parse_iso, validate_schedule
from bot.domain.schedule.describe import (
    describe_schedule,
    format_run,
    normalize_zone,
    read_back,
)
from bot.domain.schedule.policy import DEFAULT_SCHEDULE_TZ, DRAFT_TTL_SECONDS

logger = get_logger()

CONFIRM_OPTIONS = ["Confirm", "Change time", "Cancel"]

SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "schedule_prompt",
        "description": (
            "Set up a prompt that runs in THIS channel on a recurring schedule, "
            "e.g. 'post a summary of this channel every day at 9am'. Two steps: "
            "call step='draft' with prompt, cron, and timezone; it returns a "
            "read-back and shows Confirm / Change time / Cancel buttons. Relay the "
            "read-back and ask them to confirm. When someone confirms (clicks "
            "Confirm or says yes), call step='confirm' with nothing else. On "
            "Cancel, call step='discard'. At most once an hour."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "step": {"type": "string", "enum": ["draft", "confirm", "discard"]},
                "prompt": {
                    "type": "string",
                    "description": (
                        "draft only. What to do on every run, as a self-contained "
                        "instruction: it runs later with no conversation context. "
                        "E.g. 'Read the last 24 hours of this channel with "
                        "read_channel and post a short summary of what was discussed.'"
                    ),
                },
                "cron": {
                    "type": "string",
                    "description": (
                        "draft only. Five-field cron: minute hour day-of-month month "
                        "day-of-week, in the given timezone. '0 9 * * *' daily 9am; "
                        "'30 8 * * 1-5' weekdays 8:30am; '0 17 * * 5' Fridays 5pm."
                    ),
                },
                "timezone": {
                    "type": "string",
                    "description": (
                        "draft only. IANA zone like America/New_York. Leave it out "
                        "if they didn't say one; Pacific is assumed."
                    ),
                },
            },
            "required": ["step"],
        },
    },
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _draft(
    store: ScheduleRedisStore, arguments: Dict[str, Any], channel: Any, user: Any
) -> str:
    now = _now()
    try:
        prompt = check_prompt(arguments.get("prompt") or "")
        cron = " ".join(str(arguments.get("cron") or "").split())
        tz = normalize_zone(arguments.get("timezone"), DEFAULT_SCHEDULE_TZ)
        validate_schedule(cron, tz, now)
        # Checked now so nobody confirms something that can't be created. The
        # confirm step checks again, against whoever confirms.
        await check_caps(store, channel.guild.id, user.id)
    except ScheduleError as e:
        return f"Can't schedule that: {e}"

    await store.save_draft(
        channel.guild.id,
        channel.id,
        {
            "prompt": prompt,
            "cron": cron,
            "tz": tz,
            "requester_id": str(user.id),
            "created_at": now.isoformat(),
        },
        DRAFT_TTL_SECONDS,
    )
    buttons = offer_suggestions(CONFIRM_OPTIONS)
    ask = (
        "Confirm / Change time / Cancel buttons will appear under your reply; "
        "don't call suggest_replies or list the options."
        if buttons
        else "Ask them to reply 'confirm' to schedule it."
    )
    return (
        "Drafted, NOT scheduled yet. Read this back to them in your own words and "
        "ask them to confirm:\n"
        f"- What it will do each time: {prompt}\n"
        f"- When: {read_back(cron, tz, now)}\n"
        "- Where: this channel\n"
        f"{ask}"
    )


async def _confirm(store: ScheduleRedisStore, channel: Any, user: Any) -> str:
    draft = await store.get_draft(channel.guild.id, channel.id)
    if draft is None:
        return (
            "There's no schedule waiting to be confirmed in this channel (a draft "
            "is forgotten after 15 minutes). Draft it again."
        )
    try:
        job = await create_scheduled_prompt(
            channel.guild.id, channel.id, user.id,
            draft["prompt"], draft["cron"], draft["tz"],
        )
    except ScheduleError as e:
        return f"Couldn't schedule it: {e}"
    await store.clear_draft(channel.guild.id, channel.id)
    logger.info({
        "event": "schedule_created",
        "guild": str(channel.guild.id),
        "channel": str(channel.id),
        "job": job["job_id"],
    })
    first = format_run(parse_iso(job["next_run"]), job["tz"])
    return (
        f"Scheduled (id {job['job_id']}): {describe_schedule(job['cron'], job['tz'])}, "
        f"first run {first}. Tell them it's set, and that `/schedule list` shows it "
        f"and `/schedule cancel` stops it."
    )


async def execute_schedule_prompt(
    arguments: Dict[str, Any], channel: discord.TextChannel, user: Any
) -> str:
    if user is None or getattr(channel, "guild", None) is None:
        return "Scheduled prompts can only be set up by someone in a server channel."
    store = ScheduleRedisStore()
    step = arguments.get("step")
    if step == "confirm":
        return await _confirm(store, channel, user)
    if step == "discard":
        await store.clear_draft(channel.guild.id, channel.id)
        return "Draft discarded; nothing was scheduled."
    return await _draft(store, arguments, channel, user)


TOOL = AgentTool(
    config_key="schedule_prompt",
    schema=SCHEMA,
    executor=execute_schedule_prompt,
    channel_aware=True,
    user_aware=True,
    # A job must not create jobs.
    scheduled_ok=False,
)
