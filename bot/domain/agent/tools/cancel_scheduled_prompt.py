"""The `cancel_scheduled_prompt` agent tool.

Only a job's creator, or someone who can manage messages in the channel they
asked from, can cancel it -- the same rule as `/schedule cancel`.
"""

from typing import Any, Dict

import discord

from bot.app.redis.schedule_store import ScheduleRedisStore
from bot.app.schedule_jobs import cancel_scheduled_prompt, may_manage
from bot.domain.agent.tools.base import AgentTool
from bot.domain.schedule.describe import describe_schedule, preview

SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "cancel_scheduled_prompt",
        "description": (
            "Cancel one of this server's scheduled prompts by its id, so it "
            "stops running. Get the id from list_scheduled_prompts first."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "The scheduled prompt's id"},
            },
            "required": ["job_id"],
        },
    },
}


def is_moderator(channel: Any, user: Any) -> bool:
    try:
        return bool(channel.permissions_for(user).manage_messages)
    except Exception:
        return False


async def execute_cancel_scheduled_prompt(
    arguments: Dict[str, Any], channel: discord.TextChannel, user: Any
) -> str:
    job_id = str(arguments.get("job_id") or "").strip().strip("`")
    if user is None:
        return "Can't tell who is asking, so nothing was cancelled."
    job = await ScheduleRedisStore().get_job(channel.guild.id, job_id)
    if job is None:
        return f"No scheduled prompt with id {job_id!r} in this server. Check list_scheduled_prompts."
    if not may_manage(job, user.id, is_moderator(channel, user)):
        return (
            "Only the person who set it up, or someone who can manage messages, "
            "can cancel it. Nothing was cancelled."
        )
    await cancel_scheduled_prompt(channel.guild.id, job_id)
    return (
        f"Cancelled {job_id}: “{preview(job['prompt'], 80)}”, "
        f"{describe_schedule(job['cron'], job['tz'])}."
    )


TOOL = AgentTool(
    config_key="cancel_scheduled_prompt",
    schema=SCHEMA,
    executor=execute_cancel_scheduled_prompt,
    channel_aware=True,
    user_aware=True,
    scheduled_ok=False,
)
