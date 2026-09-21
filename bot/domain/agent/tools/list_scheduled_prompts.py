"""The `list_scheduled_prompts` agent tool: this server's scheduled prompts."""

from typing import Any, Dict

import discord

from bot.app.redis.schedule_store import ScheduleRedisStore
from bot.domain.agent.tools.base import AgentTool
from bot.domain.schedule.describe import summarize_job

SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "list_scheduled_prompts",
        "description": (
            "List this server's scheduled prompts (recurring prompts set up with "
            "schedule_prompt): id, schedule, channel, who set it up, next run, "
            "and what it does. Use it before cancelling one, to get its id."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


def job_lines(guild: Any, jobs: list) -> str:
    lines = []
    for job in jobs:
        channel = guild.get_channel(int(job["channel_id"]))
        creator = guild.get_member(int(job["creator_id"]))
        lines.append(summarize_job(
            job,
            channel.name if channel is not None else "deleted-channel",
            creator.display_name if creator is not None else "someone who left",
        ))
    return "\n".join(lines)


async def execute_list_scheduled_prompts(
    arguments: Dict[str, Any], channel: discord.TextChannel
) -> str:
    jobs = await ScheduleRedisStore().list_jobs(channel.guild.id)
    if not jobs:
        return "This server has no scheduled prompts."
    return f"{len(jobs)} scheduled prompt(s):\n" + job_lines(channel.guild, jobs)


TOOL = AgentTool(
    config_key="list_scheduled_prompts",
    schema=SCHEMA,
    executor=execute_list_scheduled_prompts,
    channel_aware=True,
    scheduled_ok=False,
)
