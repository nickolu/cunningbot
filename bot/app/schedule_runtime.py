"""The runner for scheduled prompts, and the calls that create and manage them.

A scheduled prompt is `/bot` on a timer: a stored prompt that runs through the
channel agent on a cron schedule. The schedule maths is in
bot/domain/schedule/; storage is bot/app/redis/schedule_store.py. This module
is the part that knows about Discord:

* `create_scheduled_prompt` / `pause_...` / `resume_...` / `cancel_...` -- what
  the agent tools and `/schedule` (Phase 5, PR 3) call. Creating one checks the
  schedule and the caps.
* `tick` -- run every minute by `start_schedule_loop`. Claims each due job,
  moves it to its next run *before* running it (so a crash mid-run never
  replays it), and starts the run in the background. A run missed while the
  bot was down happens once, late, if it's within half its interval; otherwise
  it's skipped.
* `run_scheduled_job` -- one run: find the channel and the creator, wait for
  the channel's agent lock (never dropped, unlike a mention), run the agent
  as the creator with the tools a scheduled run may use, and post the reply.
  Three failures in a row pause the job and tell its creator.

It runs in the gateway process (bot/main.py), like the scan runner: only that
container loads agent tools and holds the connection posting needs. It lives
outside bot/app/commands/ so its task registry exists once.
"""

import asyncio
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Optional
from zoneinfo import ZoneInfo

import discord
from discord.ext import tasks

from bot.api.openai.utils import sanitize_name
from bot.app.agent_runtime import (
    UNREGISTERED_AGENT_CONFIG,
    get_agent_channel_lock,
    get_channel_agent_config,
)
from bot.app.redis.agent_store import AgentRedisStore
from bot.app.redis.client import get_redis_client
from bot.app.redis.exceptions import LockAcquisitionError
from bot.app.redis.locks import redis_lock
from bot.app.redis.schedule_store import (
    RESULT_FAILED,
    RESULT_OK,
    RESULT_SKIPPED,
    STATUS_ACTIVE,
    STATUS_PAUSED,
    ScheduleRedisStore,
    parse_due_member,
)
from bot.app.scan_runtime import resolve_channel
from bot.app.suggested_replies import send_agent_reply
from bot.app.utils.logger import get_logger
from bot.domain.agent.agent_service import run_agent
from bot.domain.agent.suggestions import collect_suggestions
from bot.domain.agent.tools.registry import SCHEDULED_OK_TOOLS
from bot.domain.schedule.cron import (
    ScheduleError,
    next_run_after,
    parse_iso,
    should_run_late,
    validate_schedule,
)
from bot.domain.schedule.policy import (
    MAX_CONSECUTIVE_FAILURES,
    MAX_JOBS_PER_GUILD,
    MAX_JOBS_PER_USER,
    MAX_PROMPT_CHARS,
)

logger = get_logger()

TICK_SECONDS = 60
PROMPT_PREVIEW_CHARS = 120

# "{guild_id}:{job_id}" -> the task running it, so a run isn't garbage collected
# and a slow run isn't started a second time on top of itself.
SCHEDULE_TASKS: Dict[str, "asyncio.Task[None]"] = {}

RunJob = Callable[[Any, Dict[str, Any]], Awaitable[None]]


class SkipRun(Exception):
    """This run shouldn't happen, and that isn't the job's fault (a paused channel)."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def preview(prompt: str) -> str:
    line = " ".join(prompt.split())
    return line if len(line) <= PROMPT_PREVIEW_CHARS else line[:PROMPT_PREVIEW_CHARS] + "…"


# --- creating and managing ---------------------------------------------------


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
    prompt = (prompt or "").strip()
    cron = " ".join((cron or "").split())
    if not prompt:
        raise ScheduleError("A scheduled prompt needs something to do.")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise ScheduleError(
            f"That prompt is too long to schedule ({len(prompt)} characters, "
            f"the limit is {MAX_PROMPT_CHARS})."
        )
    validate_schedule(cron, tz, now)

    store = ScheduleRedisStore()
    try:
        # Held across the count and the create, so two jobs made at the same
        # moment can't both squeeze under a cap.
        async with redis_lock(get_redis_client(), f"schedule:{guild_id}:create", timeout=10):
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


# --- running -----------------------------------------------------------------


def scheduled_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """The channel's config with only the tools a scheduled run may use."""
    tools = [t for t in config.get("tools", []) if t in SCHEDULED_OK_TOOLS]
    return {**config, "tools": tools}


def scheduled_message(job: Dict[str, Any], now: datetime) -> str:
    """The prompt as the run's one message, with the context an unattended run lacks."""
    local = now.astimezone(ZoneInfo(job["tz"]))
    return (
        f"[Scheduled prompt, running {local.strftime('%A %Y-%m-%d %H:%M')} "
        f"{job['tz']}. Nobody is waiting to answer questions, so do the task "
        f"and post the result.]\n\n{job['prompt']}"
    )


def reply_header(name: str, prompt: str) -> str:
    return f"> 🗓️ **Scheduled by {discord.utils.escape_markdown(name)}:** {preview(prompt)}"


async def run_scheduled_job(client: Any, job: Dict[str, Any], now: Optional[datetime] = None) -> None:
    """Run one job. Raises SkipRun or an error; `_run_and_record` records either."""
    now = now or _utcnow()
    guild = client.get_guild(int(job["guild_id"]))
    if guild is None:
        raise RuntimeError("The bot is no longer in that server.")
    channel = await resolve_channel(client, int(job["channel_id"]))

    config = await get_channel_agent_config(AgentRedisStore(), guild.id, channel)
    if config is None:
        config = UNREGISTERED_AGENT_CONFIG
    elif not config.get("enabled", False):
        raise SkipRun("The bot is paused in that channel.")

    creator_id = int(job["creator_id"])
    creator = guild.get_member(creator_id)
    if creator is None:
        try:
            creator = await guild.fetch_member(creator_id)
        except discord.NotFound:
            raise RuntimeError("Its creator is no longer in the server.")

    history = [{
        "role": "user",
        "content": scheduled_message(job, now),
        "name": sanitize_name(creator.display_name),
    }]
    # Wait for a run already going in the channel rather than being dropped.
    async with get_agent_channel_lock(channel.id):
        async with channel.typing():
            with collect_suggestions() as suggestions:
                response = await run_agent(
                    channel=channel,
                    history=history,
                    agent_config=scheduled_config(config),
                    guild_id=guild.id,
                    user=creator,
                )
        if response and response.strip():
            text = reply_header(creator.display_name, job["prompt"]) + "\n\n" + response
            await send_agent_reply(channel, guild.id, text, suggestions)


async def notify_paused(client: Any, job: Dict[str, Any], error: Any) -> None:
    """Tell the creator their job paused itself: in its channel, else by DM."""
    message = (
        f"<@{job['creator_id']}> I paused your scheduled prompt "
        f"*{discord.utils.escape_markdown(preview(job['prompt']))}* after "
        f"{MAX_CONSECUTIVE_FAILURES} failed runs in a row. Last error: {error}"
    )
    mentions = discord.AllowedMentions(everyone=False, roles=False, users=True)
    try:
        channel = await resolve_channel(client, int(job["channel_id"]))
        await channel.send(message, allowed_mentions=mentions)
        return
    except Exception as e:
        logger.info({"event": "schedule_pause_notice_channel_failed", "job": job["job_id"], "error": str(e)})
    try:
        user = await client.fetch_user(int(job["creator_id"]))
        await user.send(message)
    except Exception as e:
        logger.warning({"event": "schedule_pause_notice_failed", "job": job["job_id"], "error": str(e)})


async def _run_and_record(client: Any, job: Dict[str, Any], run: RunJob) -> None:
    store = ScheduleRedisStore()
    guild_id, job_id = job["guild_id"], job["job_id"]
    try:
        await run(client, job)
    except SkipRun as e:
        logger.info({"event": "schedule_run_skipped", "job": job_id, "reason": str(e)})
        await store.record_run(guild_id, job_id, RESULT_SKIPPED, str(e))
        return
    except Exception as e:
        logger.error(f"Scheduled prompt {job_id} failed: {e}", exc_info=True)
        record = await store.record_run(guild_id, job_id, RESULT_FAILED, str(e))
        if (
            record is not None
            and record.get("status") == STATUS_ACTIVE
            and int(record.get("consecutive_failures") or 0) >= MAX_CONSECUTIVE_FAILURES
        ):
            await store.set_status(
                guild_id, job_id, STATUS_PAUSED, None,
                reason=f"Paused after {MAX_CONSECUTIVE_FAILURES} failed runs in a row: {e}",
            )
            await notify_paused(client, record, e)
        return
    logger.info({"event": "schedule_run_ok", "job": job_id, "guild": guild_id})
    await store.record_run(guild_id, job_id, RESULT_OK)


async def tick(client: Any, now: Optional[datetime] = None, run: Optional[RunJob] = None) -> int:
    """Start every job that is due. Returns how many runs were started."""
    now = now or _utcnow()
    run = run or run_scheduled_job
    store = ScheduleRedisStore()
    started = 0
    for member in await store.due_members(now):
        if not await store.claim_due(member):
            continue
        guild_id, job_id = parse_due_member(member)
        job = await store.get_job(guild_id, job_id)
        if job is None or job.get("status") != STATUS_ACTIVE:
            continue

        scheduled = parse_iso(job.get("next_run")) or now
        try:
            run_now = should_run_late(job["cron"], job["tz"], scheduled, now)
            next_run = next_run_after(job["cron"], job["tz"], now)
        except (ScheduleError, ValueError, KeyError) as e:
            # A stored schedule that no longer parses -- don't retry it every minute.
            await store.set_status(guild_id, job_id, STATUS_PAUSED, None, reason=f"Bad schedule: {e}")
            continue
        # Moved on before running, so a crash mid-run can't replay it.
        await store.reschedule(job, next_run)

        if not run_now:
            await store.record_run(
                guild_id, job_id, RESULT_SKIPPED,
                f"Missed the {scheduled.isoformat()} run by more than half its interval.",
            )
            continue
        running = SCHEDULE_TASKS.get(member)
        if running is not None and not running.done():
            await store.record_run(guild_id, job_id, RESULT_SKIPPED, "The previous run was still going.")
            continue

        task = asyncio.create_task(_run_and_record(client, job, run))
        SCHEDULE_TASKS[member] = task
        task.add_done_callback(lambda _t, key=member: SCHEDULE_TASKS.pop(key, None))
        started += 1
    return started


# --- the loop ----------------------------------------------------------------

_client: Optional[Any] = None


@tasks.loop(seconds=TICK_SECONDS)
async def _schedule_loop() -> None:
    try:
        await tick(_client)
    except Exception as e:
        logger.error(f"Scheduled prompt tick failed: {e}", exc_info=True)


def start_schedule_loop(client: Any) -> None:
    """Start ticking, once. Called from on_ready, which can fire more than once."""
    global _client
    _client = client
    if not _schedule_loop.is_running():
        _schedule_loop.start()
