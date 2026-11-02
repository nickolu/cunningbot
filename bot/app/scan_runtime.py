"""Discord-side runner for channel history scans.

The loop itself is in bot/domain/scan/; this module is everything that knows
about Discord or Redis: turning a channel into a page-fetching callable, owning
the asyncio.Task per job, enforcing one scan per channel, and restarting jobs
that were running when the bot went down.

A scan has to live in the gateway process (bot/main.py): the worker containers
exit after each tick, and a scan can run for hours.

This lives outside bot/app/commands/ for the same reason agent_runtime.py does:
every module in a command directory is loaded as an extension, and discord.py
re-executes an extension's module on load, so the task registry below could end
up existing twice and a channel could get two scans.

The user-facing half — the status message, the progress edits, the report and
the stop word — is `bot/app/scan_ux.py` and the `scan_channel_history` agent
tool, both built on `start_scan` / `cancel_channel_scan` below.
"""

import asyncio
from datetime import timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import discord

from bot.app.redis.scan_store import STATUS_FAILED, STATUS_RUNNING, ScanRedisStore
from bot.app.utils.logger import get_logger
from bot.domain.scan.extractor import LLMCall, make_extractor
from bot.domain.scan.models import ScanItem, ScanMessage, ScanPage
from bot.domain.scan.scan_service import (
    PageFetcher, ScanProgress, ScanSummary, run_scan,
)

logger = get_logger()

# One Discord history request. 100 is the API's own page size, so a smaller
# number would cost the same number of round trips for less work per model call.
PAGE_SIZE = 100

# How often the progress callback is allowed to fire. It is an edit of the
# bot's status message, and Discord rate-limits edits per channel.
PROGRESS_INTERVAL_SECONDS = 30.0

# "{guild_id}:{job_id}" -> the task running it, so a scan isn't garbage
# collected mid-run and cancel_scan can find it.
SCAN_TASKS: Dict[str, "asyncio.Task[None]"] = {}

ProgressCallback = Callable[[Dict[str, Any], ScanProgress], Awaitable[None]]
FinishCallback = Callable[[Dict[str, Any], ScanSummary], Awaitable[None]]
# (job, channel) -> the callbacks a resumed job should report through.
ResumeCallbacks = Callable[
    [Dict[str, Any], Any],
    Awaitable[Tuple[Optional[ProgressCallback], Optional[FinishCallback]]],
]


class ScanAlreadyRunning(Exception):
    """This channel already has a scan. `job` is the one that is running."""

    def __init__(self, job: Dict[str, Any]) -> None:
        super().__init__("A scan is already running in this channel.")
        self.job = job


def task_key(guild_id: Any, job_id: str) -> str:
    return f"{guild_id}:{job_id}"


def to_scan_message(message: discord.Message) -> ScanMessage:
    """A discord.Message as the plain data the domain layer works with."""
    content = message.content or ""
    # Embed text is invisible to message.content, so a scan of a feed channel
    # would see nothing at all without this.
    for embed in message.embeds:
        parts = [embed.title or "", embed.description or ""]
        text = " ".join(p for p in parts if p).strip()
        if text:
            content = (content + "\n" + text).strip()
    return ScanMessage(
        id=message.id,
        author_name=getattr(message.author, "display_name", str(message.author)),
        author_id=str(message.author.id),
        timestamp=message.created_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        content=content,
        attachment_urls=tuple(a.url for a in message.attachments),
        jump_url=message.jump_url,
    )


def page_fetcher(
    channel: Any,
    after_id: Optional[int] = None,
    before_id: Optional[int] = None,
    page_size: Optional[int] = None,
) -> PageFetcher:
    """A fetch_page callable for `run_scan`, reading `channel` newest first.

    The bot's own messages are dropped from what the model sees, but they still
    count towards the page's `read` and `oldest_id`: a page that happens to be
    all bot posts must advance the cursor, not look like the end of history.
    """

    limit = page_size or PAGE_SIZE

    async def fetch(cursor: Optional[int]) -> ScanPage:
        before = cursor or before_id
        kwargs: Dict[str, Any] = {"limit": limit, "oldest_first": False}
        if before:
            kwargs["before"] = discord.Object(id=int(before))
        if after_id:
            kwargs["after"] = discord.Object(id=int(after_id))

        messages: List[ScanMessage] = []
        read = 0
        oldest: Optional[int] = None
        async for message in channel.history(**kwargs):
            read += 1
            if oldest is None or message.id < oldest:
                oldest = message.id
            if message.author.bot:
                continue
            scan_message = to_scan_message(message)
            if not scan_message.content.strip() and not scan_message.attachment_urls:
                continue
            messages.append(scan_message)
        return ScanPage(messages=messages, oldest_id=oldest, read=read)

    return fetch


async def resolve_channel(client: discord.Client, channel_id: int) -> Any:
    channel = client.get_channel(channel_id)
    if channel is None:
        channel = await client.fetch_channel(channel_id)
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        raise ValueError(f"Channel {channel_id} is not a text channel")
    return channel


def _throttle(
    job: Dict[str, Any], callback: Optional[ProgressCallback]
) -> Callable[[ScanProgress], Awaitable[None]]:
    """Let a progress callback through at most once every PROGRESS_INTERVAL."""
    last = 0.0

    async def report(progress: ScanProgress) -> None:
        nonlocal last
        if callback is None:
            return
        now = asyncio.get_running_loop().time()
        if now - last < PROGRESS_INTERVAL_SECONDS:
            return
        last = now
        try:
            await callback(job, progress)
        except Exception as e:  # a failed status edit must not stop the scan
            logger.warning(f"scan {job.get('job_id')}: progress callback failed: {e}")

    return report


async def _execute(
    job: Dict[str, Any],
    channel: Any,
    store: ScanRedisStore,
    llm: Optional[LLMCall] = None,
    on_progress: Optional[ProgressCallback] = None,
    on_finish: Optional[FinishCallback] = None,
) -> None:
    """Run one job to completion and record how it ended, whatever happens."""
    guild_id = job["guild_id"]
    job_id = job["job_id"]

    async def save_results(items: List[ScanItem]) -> int:
        return await store.add_results(guild_id, job_id, [i.to_dict() for i in items])

    async def save_progress(progress: ScanProgress) -> None:
        await store.update_progress(
            guild_id, job_id,
            cursor=progress.cursor, scanned=progress.scanned,
            matched=progress.matched, pages=progress.pages,
            failed_pages=progress.failed_pages,
        )

    async def is_cancelled() -> bool:
        return await store.is_cancel_requested(guild_id, job_id)

    summary: Optional[ScanSummary] = None
    try:
        summary = await run_scan(
            job,
            page_fetcher(channel, after_id=job.get("after"), before_id=job.get("before")),
            make_extractor(job["instruction"], llm),
            save_results,
            save_progress,
            is_cancelled,
            _throttle(job, on_progress),
        )
        if summary.status != STATUS_RUNNING:
            await store.finish(guild_id, job_id, summary.status, summary.error)
    except asyncio.CancelledError:
        # The process is going down. Leave the job `running` so the cursor is
        # kept and resume_running_jobs picks it up on the next start.
        logger.info(f"scan {job_id} interrupted; it will resume on restart")
        raise
    except Exception as e:
        logger.error(f"scan {job_id} crashed: {e}")
        summary = ScanSummary(status=STATUS_FAILED, error=str(e))
        try:
            await store.finish(guild_id, job_id, STATUS_FAILED, str(e))
        except Exception as store_error:
            logger.error(f"scan {job_id}: could not record the failure: {store_error}")
    finally:
        SCAN_TASKS.pop(task_key(guild_id, job_id), None)

    if on_finish is not None and summary is not None and summary.status != STATUS_RUNNING:
        try:
            await on_finish(await store.get_job(guild_id, job_id) or job, summary)
        except Exception as e:
            logger.error(f"scan {job_id}: finish callback failed: {e}")


def _launch(
    job: Dict[str, Any],
    channel: Any,
    store: ScanRedisStore,
    llm: Optional[LLMCall] = None,
    on_progress: Optional[ProgressCallback] = None,
    on_finish: Optional[FinishCallback] = None,
) -> "asyncio.Task[None]":
    task = asyncio.create_task(
        _execute(job, channel, store, llm, on_progress, on_finish)
    )
    SCAN_TASKS[task_key(job["guild_id"], job["job_id"])] = task
    return task


async def start_scan(
    channel: Any,
    requester_id: int,
    instruction: str,
    before: Optional[int] = None,
    after: Optional[int] = None,
    store: Optional[ScanRedisStore] = None,
    llm: Optional[LLMCall] = None,
    on_progress: Optional[ProgressCallback] = None,
    on_finish: Optional[FinishCallback] = None,
) -> Dict[str, Any]:
    """Start scanning `channel` in the background and return the job record.

    Raises ScanAlreadyRunning if the channel already has a scan: one at a time
    per channel, so a channel's history is never being read twice at once.
    """
    store = store or ScanRedisStore()
    guild = getattr(channel, "guild", None)
    guild_id = getattr(guild, "id", None)
    job = await store.create_job(
        guild_id, channel.id, requester_id, instruction, before=before, after=after
    )
    if job is None:
        active = await store.get_active_job_for_channel(guild_id, channel.id)
        raise ScanAlreadyRunning(active or {})
    _launch(job, channel, store, llm, on_progress, on_finish)
    logger.info(
        f"scan {job['job_id']} started in channel {channel.id} by {requester_id}"
    )
    return job


async def cancel_scan(
    guild_id: Any, job_id: str, store: Optional[ScanRedisStore] = None
) -> bool:
    """Ask a running scan to stop. It ends after the page it is on.

    The flag goes to Redis rather than cancelling the task, so the results and
    the cursor are saved and the job finishes as `cancelled` — and so a cancel
    still lands on a scan being run by a process that restarted since.
    """
    store = store or ScanRedisStore()
    return await store.request_cancel(guild_id, job_id)


async def cancel_channel_scan(
    guild_id: Any, channel_id: Any, store: Optional[ScanRedisStore] = None
) -> Optional[Dict[str, Any]]:
    """Cancel whatever scan is running in a channel. Returns the job, if any."""
    store = store or ScanRedisStore()
    job = await store.get_active_job_for_channel(guild_id, channel_id)
    if job is None:
        return None
    await store.request_cancel(guild_id, job["job_id"])
    return job


async def resume_running_jobs(
    client: discord.Client,
    store: Optional[ScanRedisStore] = None,
    llm: Optional[LLMCall] = None,
    callbacks: Optional[ResumeCallbacks] = None,
) -> int:
    """Restart every scan that was running when the process last stopped.

    Called from on_ready. Each job resumes from its saved cursor, so a scan
    interrupted 40,000 messages in does not start over.

    A resumed job has no callbacks of its own: the status message from the run
    that was interrupted belongs to a task that no longer exists. `callbacks` is
    handed the job and its channel and returns the pair to use, which is where
    `bot/app/scan_ux.py` posts a fresh status message saying the scan picked up
    where it left off. Without it a scan resumes silently.
    """
    store = store or ScanRedisStore()
    resumed = 0
    for guild_id, job_id in await store.list_running_jobs():
        key = task_key(guild_id, job_id)
        if key in SCAN_TASKS:
            continue  # already running in this process
        job = await store.get_job(guild_id, job_id)
        if job is None or job.get("status") != STATUS_RUNNING:
            await store.forget_running(guild_id, job_id)
            continue
        try:
            channel = await resolve_channel(client, int(job["channel_id"]))
        except Exception as e:
            logger.error(f"scan {job_id}: cannot resume, channel unreachable: {e}")
            await store.finish(guild_id, job_id, STATUS_FAILED, f"Channel unreachable: {e}")
            continue
        on_progress: Optional[ProgressCallback] = None
        on_finish: Optional[FinishCallback] = None
        if callbacks is not None:
            try:
                on_progress, on_finish = await callbacks(job, channel)
            except Exception as e:
                # A scan that can't announce itself still has work to finish.
                logger.error(f"scan {job_id}: could not attach reporting on resume: {e}")
        _launch(job, channel, store, llm, on_progress, on_finish)
        resumed += 1
        logger.info(
            f"scan {job_id} resumed from cursor {job.get('cursor')} "
            f"({job.get('scanned')} messages scanned so far)"
        )
    return resumed
