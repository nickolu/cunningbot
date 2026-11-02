"""Creating and managing scheduled prompts: what the agent tools and /schedule call.

Kept apart from bot/app/schedule_runtime.py, which runs the jobs, because the
runner imports the agent and the agent's tool registry imports the scheduling
tools, which import this. See schedule_runtime.py for how jobs run.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from bot.app.redis.client import get_redis_client
from bot.app.redis.exceptions import LockAcquisitionError
from bot.app.redis.locks import redis_lock
from bot.app.redis.schedule_store import STATUS_ACTIVE, STATUS_PAUSED, ScheduleRedisStore
from bot.domain.schedule.cron import ScheduleError, next_run_after, validate_schedule
from bot.domain.schedule.policy import (
    MAX_JOBS_PER_GUILD,
    MAX_JOBS_PER_USER,
    MAX_PROMPT_CHARS,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def check_prompt(prompt: str) -> str:
    """The prompt, trimmed. Raises ScheduleError if it can't be scheduled."""
    prompt = (prompt or "").strip()
    if not prompt:
        raise ScheduleError("A scheduled prompt needs something to do.")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise ScheduleError(
            f"That prompt is too long to schedule ({len(prompt)} characters, "
            f"the limit is {MAX_PROMPT_CHARS})."
        )
    return prompt


async def check_caps(store: ScheduleRedisStore, guild_id: Any, creator_id: Any) -> None:
    """Raise ScheduleError if the server or the person has no room for a job."""
    jobs = await store.list_jobs(guild_id)
    if len(jobs) >= MAX_JOBS_PER_GUILD:
        raise ScheduleError(
            f"This server already has {MAX_JOBS_PER_GUILD} scheduled prompts, "
            f"the most it can have. Cancel one to make room "
            f"(`/schedule list`, then `/schedule cancel`)."
        )
    mine = [j for j in jobs if j.get("creator_id") == str(creator_id)]
    if len(mine) >= MAX_JOBS_PER_USER:
        raise ScheduleError(
            f"You already have {MAX_JOBS_PER_USER} scheduled prompts, the most "
            f"one person can have. Cancel one to make room "
            f"(`/schedule list`, then `/schedule cancel`)."
        )


def may_manage(job: Dict[str, Any], user_id: Any, is_moderator: bool) -> bool:
    """Its creator, or anyone who can manage messages in the channel."""
    return is_moderator or str(user_id) == str(job.get("creator_id"))


async def create_scheduled_prompt(
    guild_id: Any,
    channel_id: Any,
    creator_id: Any,
    prompt: str,
    cron: str,
    tz: str,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Store a new job. Raises ScheduleError, with a message fit for the user."""
    now = now or _utcnow()
    prompt = check_prompt(prompt)
    cron = " ".join((cron or "").split())
    validate_schedule(cron, tz, now)

    store = ScheduleRedisStore()
    try:
        # Held across the count and the create, so two jobs made at the same
        # moment can't both squeeze under a cap.
        async with redis_lock(get_redis_client(), f"schedule:{guild_id}:create", timeout=10):
            await check_caps(store, guild_id, creator_id)
            return await store.create_job(
                guild_id, channel_id, creator_id, prompt, cron, tz,
                next_run=next_run_after(cron, tz, now),
            )
    except LockAcquisitionError:
        raise ScheduleError(
            "Another scheduled prompt is being set up in this server. Try again in a moment."
        )


async def pause_scheduled_prompt(
    guild_id: Any, job_id: str, reason: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    return await ScheduleRedisStore().set_status(guild_id, job_id, STATUS_PAUSED, None, reason)


async def resume_scheduled_prompt(
    guild_id: Any, job_id: str, now: Optional[datetime] = None
) -> Optional[Dict[str, Any]]:
    """Make a paused job active again, from its next run after now."""
    store = ScheduleRedisStore()
    job = await store.get_job(guild_id, job_id)
    if job is None:
        return None
    next_run = next_run_after(job["cron"], job["tz"], now or _utcnow())
    return await store.set_status(guild_id, job_id, STATUS_ACTIVE, next_run)


async def cancel_scheduled_prompt(guild_id: Any, job_id: str) -> bool:
    return await ScheduleRedisStore().delete_job(guild_id, job_id)
