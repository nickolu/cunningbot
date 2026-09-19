"""The scan loop: fetch a page, extract, merge, save, repeat.

    page = fetch_page(cursor)        one history request, older than the cursor
    items = extract(page.messages)   one model call, this page only
    new = save_results(items)        merged and deduped in the store
    save_progress(...)               cursor and counts, so a restart resumes

The loop is deliberately ignorant of Discord, Redis and the clock. It is given
callables for all three and it reports progress through another, which the
runner throttles — throttling here would make the tests wait.

Bounds (`before`/`after`, a date, the start of the channel) belong to
`fetch_page`: it stops handing over messages and the loop is done. The loop's
own stopping conditions are an empty page, a cancel, and too many model
failures in a row.
"""

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

from bot.app.utils.logger import get_logger
from bot.domain.scan.extractor import ExtractError
from bot.domain.scan.models import ScanItem, ScanMessage, ScanPage

logger = get_logger()

# A page the model can't read is logged and skipped: one bad page should not
# throw away an hour of scanning. Five in a row is something systemic — a bad
# API key, a model that stopped answering in JSON, an instruction it refuses —
# and every further page would cost money to fail the same way.
MAX_CONSECUTIVE_FAILURES = 5

STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_CANCELLED = "cancelled"
STATUS_FAILED = "failed"

# cursor (None on the first page) -> the next page, oldest message id included
PageFetcher = Callable[[Optional[int]], Awaitable[ScanPage]]
# one page of messages -> the items on it
Extractor = Callable[[List[ScanMessage]], Awaitable[List[ScanItem]]]
# items -> how many were new after dedup
ResultSaver = Callable[[List[ScanItem]], Awaitable[int]]


@dataclass
class ScanProgress:
    scanned: int = 0
    matched: int = 0
    pages: int = 0
    failed_pages: int = 0
    cursor: Optional[int] = None


@dataclass
class ScanSummary:
    # Stays "running" only when `max_pages` stopped the loop early: the scan is
    # unfinished and the caller should leave the job alone.
    status: str = STATUS_RUNNING
    scanned: int = 0
    matched: int = 0
    pages: int = 0
    failed_pages: int = 0
    cursor: Optional[int] = None
    error: Optional[str] = None

    @property
    def progress(self) -> ScanProgress:
        return ScanProgress(
            scanned=self.scanned, matched=self.matched, pages=self.pages,
            failed_pages=self.failed_pages, cursor=self.cursor,
        )


async def _noop_progress(progress: ScanProgress) -> None:
    return None


async def _never_cancelled() -> bool:
    return False


async def run_scan(
    job: Dict[str, Any],
    fetch_page: PageFetcher,
    extract: Extractor,
    save_results: ResultSaver,
    save_progress: Callable[[ScanProgress], Awaitable[None]],
    is_cancelled: Callable[[], Awaitable[bool]] = _never_cancelled,
    on_progress: Optional[Callable[[ScanProgress], Awaitable[None]]] = None,
    max_pages: Optional[int] = None,
) -> ScanSummary:
    """Page backwards through a channel until it runs out, or is stopped.

    `job` is the stored record: a resumed job carries the cursor and counts it
    reached before the restart, so the scan picks up where it left off instead
    of re-reading (and re-paying for) what it already did.
    """
    job_id = job.get("job_id")
    cursor = job.get("cursor")
    cursor = int(cursor) if cursor else None
    summary = ScanSummary(
        scanned=int(job.get("scanned") or 0),
        matched=int(job.get("matched") or 0),
        pages=int(job.get("pages") or 0),
        failed_pages=int(job.get("failed_pages") or 0),
        cursor=cursor,
    )
    report = on_progress or _noop_progress
    consecutive_failures = 0
    pages_this_run = 0

    while True:
        # Between pages is the only place a cancel can land: the flag lives in
        # Redis, so it survives the restart of a job that was cancelled while
        # the bot was down.
        if await is_cancelled():
            summary.status = STATUS_CANCELLED
            break
        if max_pages is not None and pages_this_run >= max_pages:
            break

        page = await fetch_page(summary.cursor)
        pages_this_run += 1
        if page.exhausted:
            summary.status = STATUS_DONE
            break

        summary.scanned += page.read
        summary.pages += 1
        if page.oldest_id is not None:
            summary.cursor = int(page.oldest_id)

        if page.messages:
            try:
                items = await extract(page.messages)
                consecutive_failures = 0
            except ExtractError as e:
                consecutive_failures += 1
                summary.failed_pages += 1
                logger.warning(
                    f"scan {job_id}: page ending at {summary.cursor} could not be read "
                    f"({consecutive_failures} in a row): {e}"
                )
                items = []
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    summary.status = STATUS_FAILED
                    summary.error = (
                        "%d pages in a row could not be read: %s"
                        % (consecutive_failures, e)
                    )
            if items:
                summary.matched += await save_results(items)

        await save_progress(summary.progress)
        if summary.status == STATUS_FAILED:
            break
        await report(summary.progress)

    await save_progress(summary.progress)
    logger.info(
        f"scan {job_id} {summary.status}: {summary.scanned} messages, "
        f"{summary.matched} items, {summary.pages} pages, "
        f"{summary.failed_pages} unreadable"
    )
    return summary
