"""Redis storage layer for channel history scans.

A scan is a long-running job: it pages backwards through a channel's history
applying a natural-language instruction, accumulating results. It can run for
hours, has to survive a bot restart, and has to be cancellable, so everything
about it lives here rather than in the runner's memory.

Key schema:
    scan:{guild_id}:job:{job_id}          # JSON: the job record, fields below
    scan:{guild_id}:job:{job_id}:results  # Hash: dedup key -> JSON item
    scan:{guild_id}:job:{job_id}:cancel   # "1" once a cancel has been asked for
    scan:{guild_id}:channel:{channel_id}  # job_id of that channel's active scan
    scan:running                          # Set of "{guild_id}:{job_id}" to resume

Job record fields:
    job_id, guild_id, channel_id, requester_id, instruction,
    status       -- running | done | cancelled | failed
    cursor       -- oldest message id reached; a resumed scan starts below it
    before/after -- optional message-id bounds the scan was given (or None)
    started_at, updated_at, finished_at   -- ISO-8601 UTC
    scanned, matched, pages, failed_pages -- counts
    last_error   -- why a failed job failed

A result item is {key, text, source_url, image_urls, found_at}, stored under
its normalized dedup key so the same restaurant found on three pages is one
hash field. `add_results` uses HSETNX and reports how many fields were new,
which is what the job's `matched` count counts.

The cancel flag is a key of its own rather than a field of the JSON record on
purpose: the record is read-modify-written once per page to save progress, and
a cancel landing inside that window would be silently overwritten. The scan
loop re-reads the flag between pages (`is_cancel_requested`), so a cancel takes
effect after at most one more page. `get_job` merges it back in as
`cancel_requested` so callers still see one record.

`scan:running` is deliberately not guild-scoped: startup resume needs every
running job across every guild in one read, and there are only ever a handful.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from bot.app.redis.client import get_redis_client
from bot.app.redis.serialization import channel_id_to_str, guild_id_to_str
from bot.app.utils.logger import get_logger

logger = get_logger()

RUNNING_KEY = "scan:running"

# Finished jobs stick around for a week. Long enough that "what did that scan
# find yesterday?" still works and a scan finishing overnight can be reported
# the next morning; short enough that a channel full of one-off scans can't
# grow without bound in a 2gb Redis where eviction would hit live state too.
FINISHED_JOB_TTL_SECONDS = 7 * 24 * 60 * 60

STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_CANCELLED = "cancelled"
STATUS_FAILED = "failed"
FINISHED_STATUSES = (STATUS_DONE, STATUS_CANCELLED, STATUS_FAILED)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _loads(data: Optional[str], what: str) -> Optional[Any]:
    if not data:
        return None
    try:
        return json.loads(data)
    except json.JSONDecodeError as e:
        logger.error(f"Bad scan {what} JSON: {e}")
        return None


class ScanRedisStore:
    def __init__(self) -> None:
        self.redis_client = get_redis_client()
        self.redis = self.redis_client.redis

    # --- Keys ---

    def _job_key(self, guild_id: str, job_id: str) -> str:
        return f"scan:{guild_id}:job:{job_id}"

    def _results_key(self, guild_id: str, job_id: str) -> str:
        return f"scan:{guild_id}:job:{job_id}:results"

    def _cancel_key(self, guild_id: str, job_id: str) -> str:
        return f"scan:{guild_id}:job:{job_id}:cancel"

    def _channel_key(self, guild_id: str, channel_id: str) -> str:
        return f"scan:{guild_id}:channel:{channel_id}"

    # --- Jobs ---

    async def create_job(
        self,
        guild_id: Any,
        channel_id: Any,
        requester_id: Any,
        instruction: str,
        before: Optional[int] = None,
        after: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Start a job for a channel, or None if that channel already has one.

        The channel pointer is claimed with SET NX, so two scans started at the
        same moment in one channel can't both win.
        """
        guild = guild_id_to_str(guild_id)
        channel = channel_id_to_str(channel_id)
        if await self.get_active_job_for_channel(guild, channel) is not None:
            return None

        job_id = uuid.uuid4().hex[:12]
        claimed = await self.redis.set(self._channel_key(guild, channel), job_id, nx=True)
        if not claimed:
            return None

        now = _now()
        record: Dict[str, Any] = {
            "job_id": job_id,
            "guild_id": guild,
            "channel_id": channel,
            "requester_id": str(requester_id),
            "instruction": instruction,
            "status": STATUS_RUNNING,
            "cursor": None,
            "before": int(before) if before else None,
            "after": int(after) if after else None,
            "started_at": now,
            "updated_at": now,
            "finished_at": None,
            "scanned": 0,
            "matched": 0,
            "pages": 0,
            "failed_pages": 0,
            "last_error": None,
        }
        await self.redis.set(self._job_key(guild, job_id), json.dumps(record))
        await self.redis.sadd(RUNNING_KEY, f"{guild}:{job_id}")
        logger.info(f"scan job {job_id} created for guild {guild} channel {channel}")
        return dict(record)

    async def get_job(self, guild_id: Any, job_id: str) -> Optional[Dict[str, Any]]:
        """The job record, with the live cancel flag merged in."""
        guild = guild_id_to_str(guild_id)
        record = _loads(await self.redis.get(self._job_key(guild, job_id)), "job")
        if not isinstance(record, dict):
            return None
        record["cancel_requested"] = await self.is_cancel_requested(guild, job_id)
        return record

    async def update_progress(
        self,
        guild_id: Any,
        job_id: str,
        cursor: Optional[int] = None,
        scanned: Optional[int] = None,
        matched: Optional[int] = None,
        pages: Optional[int] = None,
        failed_pages: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Save how far the scan has got. Only the values given are changed."""
        guild = guild_id_to_str(guild_id)
        key = self._job_key(guild, job_id)
        record = _loads(await self.redis.get(key), "job")
        if not isinstance(record, dict):
            logger.warning(f"scan job {job_id} vanished mid-scan (guild {guild})")
            return None
        changes = {
            "cursor": cursor,
            "scanned": scanned,
            "matched": matched,
            "pages": pages,
            "failed_pages": failed_pages,
        }
        for field, value in changes.items():
            if value is not None:
                record[field] = value
        record["updated_at"] = _now()
        await self.redis.set(key, json.dumps(record))
        return record

    async def finish(
        self, guild_id: Any, job_id: str, status: str, error: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Mark a job done/cancelled/failed, free its channel, and set the TTL."""
        guild = guild_id_to_str(guild_id)
        key = self._job_key(guild, job_id)
        record = _loads(await self.redis.get(key), "job")
        if not isinstance(record, dict):
            # The record is gone (evicted, or expired); still clean up the rest.
            await self._release(guild, job_id, None)
            return None
        record["status"] = status
        record["finished_at"] = _now()
        record["updated_at"] = record["finished_at"]
        if error is not None:
            record["last_error"] = str(error)[:500]
        await self.redis.set(key, json.dumps(record))
        await self._release(guild, job_id, record.get("channel_id"))
        # Results outlive the run by the same week, so a finished scan can still
        # be reported or re-published without re-reading the channel.
        await self.redis.expire(key, FINISHED_JOB_TTL_SECONDS)
        await self.redis.expire(self._results_key(guild, job_id), FINISHED_JOB_TTL_SECONDS)
        logger.info(f"scan job {job_id} finished as {status} (guild {guild})")
        return record

    async def _release(
        self, guild: str, job_id: str, channel_id: Optional[str]
    ) -> None:
        await self.redis.srem(RUNNING_KEY, f"{guild}:{job_id}")
        await self.redis.delete(self._cancel_key(guild, job_id))
        if not channel_id:
            return
        channel_key = self._channel_key(guild, channel_id)
        # Only clear the pointer if it is still ours: a job that finished after
        # a newer scan started in the same channel must not free the new one.
        if await self.redis.get(channel_key) == job_id:
            await self.redis.delete(channel_key)

    async def get_active_job_for_channel(
        self, guild_id: Any, channel_id: Any
    ) -> Optional[Dict[str, Any]]:
        """The channel's running job, clearing the pointer if it is stale."""
        guild = guild_id_to_str(guild_id)
        channel = channel_id_to_str(channel_id)
        channel_key = self._channel_key(guild, channel)
        job_id = await self.redis.get(channel_key)
        if not job_id:
            return None
        record = await self.get_job(guild, job_id)
        if record is None or record.get("status") != STATUS_RUNNING:
            await self.redis.delete(channel_key)
            return None
        return record

    async def list_running_jobs(self) -> List[Tuple[str, str]]:
        """(guild_id, job_id) for every job that was running, for startup resume."""
        members = await self.redis.smembers(RUNNING_KEY)
        jobs: List[Tuple[str, str]] = []
        for member in members:
            guild, _, job_id = str(member).partition(":")
            if guild and job_id:
                jobs.append((guild, job_id))
        return sorted(jobs)

    async def forget_running(self, guild_id: Any, job_id: str) -> None:
        """Drop a job from the resume set without touching its record."""
        await self.redis.srem(RUNNING_KEY, f"{guild_id_to_str(guild_id)}:{job_id}")

    # --- Cancelling ---

    async def request_cancel(self, guild_id: Any, job_id: str) -> bool:
        """Ask a running job to stop. The loop notices between pages."""
        guild = guild_id_to_str(guild_id)
        record = await self.get_job(guild, job_id)
        if record is None or record.get("status") != STATUS_RUNNING:
            return False
        await self.redis.set(self._cancel_key(guild, job_id), "1")
        return True

    async def is_cancel_requested(self, guild_id: Any, job_id: str) -> bool:
        guild = guild_id_to_str(guild_id)
        return bool(await self.redis.get(self._cancel_key(guild, job_id)))

    # --- Results ---

    async def add_results(
        self, guild_id: Any, job_id: str, items: List[Dict[str, Any]]
    ) -> int:
        """Merge items into the job's results. Returns how many were new.

        Dedup is by the item's `key`: the first sighting wins, so the earliest
        (newest in the channel) message stays as the source link.
        """
        guild = guild_id_to_str(guild_id)
        if not items:
            return 0
        key = self._results_key(guild, job_id)
        found_at = _now()
        async with self.redis.pipeline(transaction=False) as pipe:
            for item in items:
                dedup_key = str(item.get("key") or "").strip()
                if not dedup_key:
                    continue
                record = dict(item)
                record.setdefault("found_at", found_at)
                pipe.hsetnx(key, dedup_key, json.dumps(record, ensure_ascii=False))
            added = await pipe.execute()
        return sum(1 for was_new in added if was_new)

    async def get_results(self, guild_id: Any, job_id: str) -> List[Dict[str, Any]]:
        """Every item found so far, oldest find first."""
        guild = guild_id_to_str(guild_id)
        raw = await self.redis.hgetall(self._results_key(guild, job_id))
        items: List[Dict[str, Any]] = []
        for dedup_key, data in (raw or {}).items():
            record = _loads(data, "result")
            if isinstance(record, dict):
                record.setdefault("key", dedup_key)
                items.append(record)
        items.sort(key=lambda i: (str(i.get("found_at") or ""), str(i.get("key") or "")))
        return items

    async def result_count(self, guild_id: Any, job_id: str) -> int:
        guild = guild_id_to_str(guild_id)
        return int(await self.redis.hlen(self._results_key(guild, job_id)) or 0)
