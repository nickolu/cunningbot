"""The Discord side of a scan: the status message, progress, and the report.

`bot/app/scan_runtime.py` runs the scan and calls back; this module decides what
the channel sees. It lives outside `bot/app/commands/` for the same reason the
runtime does: a module inside a command directory is loaded as an extension and
re-executed on load, and the registry below has to exist exactly once.

The status message is held in memory rather than in Redis on purpose. A scan
only ever runs in the gateway process, and a process that restarts posts a
*fresh* status message for the job it resumes — the old one belongs to a task
that no longer exists — so there is nothing worth persisting across a restart.

The reverse map exists for the 🛑 reaction: `on_raw_reaction_add` gives a message
id and nothing else, so the listener needs to get from that id back to a job.
"""

from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

import discord

from bot.app.redis.scan_store import (
    STATUS_CANCELLED, STATUS_FAILED, ScanRedisStore,
)
from bot.app.utils.logger import get_logger
from bot.domain.scan import report
from bot.domain.scan.scan_service import ScanProgress, ScanSummary
from bot.utils import split_message

logger = get_logger()

# "{guild_id}:{job_id}" -> the status message being edited with progress.
STATUS_MESSAGES: Dict[str, discord.Message] = {}
# status message id -> ("{guild_id}", "{job_id}"), for the 🛑 reaction.
STATUS_MESSAGE_JOBS: Dict[int, Tuple[str, str]] = {}

# The report pings whoever asked for the scan: it can land hours later.
REPORT_MENTIONS = discord.AllowedMentions(everyone=False, roles=False, users=True)


def _key(job: Dict[str, Any]) -> str:
    return "{}:{}".format(job.get("guild_id"), job.get("job_id"))


def channel_name(channel: Any) -> str:
    return str(getattr(channel, "name", None) or "channel")


def remember_status_message(job: Dict[str, Any], message: discord.Message) -> None:
    STATUS_MESSAGES[_key(job)] = message
    STATUS_MESSAGE_JOBS[message.id] = (str(job.get("guild_id")), str(job.get("job_id")))


def forget_status_message(job: Dict[str, Any]) -> Optional[discord.Message]:
    message = STATUS_MESSAGES.pop(_key(job), None)
    if message is not None:
        STATUS_MESSAGE_JOBS.pop(message.id, None)
    return message


def job_for_status_message(message_id: int) -> Optional[Tuple[str, str]]:
    """(guild_id, job_id) for a status message, or None if it isn't one."""
    return STATUS_MESSAGE_JOBS.get(message_id)


async def post_status(
    channel: Any, job: Dict[str, Any], resumed: bool = False
) -> Optional[discord.Message]:
    """Post the status message for a job and remember it for progress edits."""
    instruction = job.get("instruction") or ""
    if resumed:
        text = report.resumed_text(
            channel_name(channel),
            instruction,
            int(job.get("scanned") or 0),
            int(job.get("matched") or 0),
        )
    else:
        text = report.starting_text(channel_name(channel), instruction)
    try:
        message = await channel.send(text)
    except Exception as e:
        # A scan with no status message still runs; it just can't be watched.
        logger.warning(f"scan {job.get('job_id')}: could not post the status message: {e}")
        return None
    remember_status_message(job, message)
    return message


async def _edit_status(job: Dict[str, Any], text: str) -> None:
    """Edit the status message, quietly giving up if it has been deleted."""
    message = STATUS_MESSAGES.get(_key(job))
    if message is None:
        return
    try:
        await message.edit(content=text)
    except discord.NotFound:
        logger.info(
            f"scan {job.get('job_id')}: status message was deleted; still scanning"
        )
        forget_status_message(job)
    except Exception as e:
        logger.warning(f"scan {job.get('job_id')}: could not edit the status message: {e}")


def make_progress_callback(
    channel: Any,
) -> Callable[[Dict[str, Any], ScanProgress], Awaitable[None]]:
    """A callback for `start_scan(on_progress=...)`, already throttled by the runtime."""

    async def on_progress(job: Dict[str, Any], progress: ScanProgress) -> None:
        await _edit_status(
            job,
            report.progress_text(
                channel_name(channel),
                job.get("instruction") or "",
                progress.scanned,
                progress.matched,
            ),
        )

    return on_progress


async def _publish(
    guild: Any, instruction: str, name: str, status: str, scanned: int, items: list
) -> Optional[str]:
    """Publish the results as a page. None when publishing isn't available."""
    from bot.domain.pages.page_service import publish_page

    try:
        return await publish_page(
            guild_id=str(guild.id),
            title=report.page_title(name, instruction),
            markdown=report.page_markdown(name, instruction, status, scanned, items),
            slug=report.scan_slug(instruction),
            guild_name=getattr(guild, "name", None),
        )
    except EnvironmentError:
        logger.warning("scan report: page publishing is not configured")
    except Exception as e:
        logger.error(f"scan report: could not publish the page: {e}")
    return None


async def build_report(
    channel: Any, record: Dict[str, Any], summary: ScanSummary, items: list
) -> str:
    """The final message: the items inline, or a published page and a link."""
    name = channel_name(channel)
    instruction = record.get("instruction") or ""
    status = summary.status
    scanned = summary.scanned

    if report.fits_inline(items):
        return report.inline_report(
            name, instruction, status, scanned, items, summary.error
        )

    guild = getattr(channel, "guild", None)
    url = None
    if guild is not None:
        url = await _publish(guild, instruction, name, status, scanned, items)
    if url:
        return report.page_report(
            name, instruction, status, scanned, items, url, summary.error
        )
    # No page: the list still gets posted, split across messages if it has to be.
    return report.inline_report(
        name, instruction, status, scanned, items, summary.error
    )


def make_finish_callback(
    channel: Any, store: Optional[ScanRedisStore] = None
) -> Callable[[Dict[str, Any], ScanSummary], Awaitable[None]]:
    """A callback for `start_scan(on_finish=...)` that posts what was found."""

    async def on_finish(record: Dict[str, Any], summary: ScanSummary) -> None:
        results_store = store or ScanRedisStore()
        try:
            items = await results_store.get_results(
                record.get("guild_id"), record.get("job_id")
            )
        except Exception as e:
            logger.error(f"scan {record.get('job_id')}: could not read results: {e}")
            items = []

        text = await build_report(channel, record, summary, items)

        # The status message stops claiming to be live even if nobody scrolls
        # down to the report.
        await _edit_status(
            record,
            report.headline(
                channel_name(channel),
                record.get("instruction") or "",
                summary.status,
                summary.scanned,
                len(items),
                summary.error,
            ),
        )
        forget_status_message(record)

        requester = record.get("requester_id")
        if requester:
            text = "<@{}> {}".format(requester, text)
        for chunk in split_message(text):
            try:
                await channel.send(chunk, allowed_mentions=REPORT_MENTIONS)
            except Exception as e:
                logger.error(f"scan {record.get('job_id')}: could not post the report: {e}")
                return
        logger.info({
            "event": "scan_report_posted",
            "job": record.get("job_id"),
            "status": summary.status,
            "scanned": summary.scanned,
            "items": len(items),
            "cancelled": summary.status == STATUS_CANCELLED,
            "failed": summary.status == STATUS_FAILED,
        })

    return on_finish


def make_callbacks(channel: Any, store: Optional[ScanRedisStore] = None) -> Tuple[
    Callable[[Dict[str, Any], ScanProgress], Awaitable[None]],
    Callable[[Dict[str, Any], ScanSummary], Awaitable[None]],
]:
    return make_progress_callback(channel), make_finish_callback(channel, store)


async def resume_callbacks(job: Dict[str, Any], channel: Any) -> Tuple[
    Callable[[Dict[str, Any], ScanProgress], Awaitable[None]],
    Callable[[Dict[str, Any], ScanSummary], Awaitable[None]],
]:
    """Callbacks for a scan resumed after a restart, with a fresh status message.

    Passed to `resume_running_jobs` from `bot/main.py`. The status message from
    before the restart is not edited again: it belongs to a task that died, and
    a new one says the scan picked up where it left off.
    """
    await post_status(channel, job, resumed=True)
    return make_callbacks(channel)
