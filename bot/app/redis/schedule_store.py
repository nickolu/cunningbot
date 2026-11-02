"""Redis storage for scheduled prompts.

A scheduled prompt is an agent prompt that runs in a channel on a cron
schedule ("post a summary of this channel every day at 9am PT").

Key schema:
    schedule:{guild_id}:job:{job_id}   # JSON: the job record, fields below
    schedule:{guild_id}:jobs           # Set of the guild's job ids
    schedule:due                       # Sorted set: "{guild_id}:{job_id}" -> next run, epoch seconds
    schedule:{guild_id}:draft:{channel_id}  # JSON: a schedule waiting for Confirm (TTL)

Job record fields:
    job_id, guild_id, channel_id, creator_id, prompt,
    cron, tz            -- five-field cron expression, IANA zone name
    status              -- active | paused (a cancelled job is deleted)
    next_run            -- ISO-8601 UTC; None while paused
    created_at, updated_at, last_run_at   -- ISO-8601 UTC
    last_result         -- ok | failed | skipped, or None before the first run
    last_error          -- why the last run failed or was skipped
    consecutive_failures, run_count
    paused_reason       -- why it was paused, if it paused itself

Only active jobs are in `schedule:due`. The runner claims a due job with ZREM
(`claim_due`), so if two processes ever ticked at once only one would run it,
and puts it back with its next time once it has worked that out.

A draft is what `schedule_prompt` read back and is waiting for someone to
confirm: {prompt, cron, tz, requester_id, created_at}. One per channel, the
latest wins, and it expires on its own after `DRAFT_TTL_SECONDS`.

`schedule:due` is not guild-scoped for the same reason `scan:running` isn't:
the tick needs every guild's due jobs in one read.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from bot.app.redis.client import get_redis_client
from bot.app.redis.serialization import channel_id_to_str, guild_id_to_str
from bot.app.utils.logger import get_logger

logger = get_logger()

DUE_KEY = "schedule:due"

STATUS_ACTIVE = "active"
STATUS_PAUSED = "paused"

RESULT_OK = "ok"
RESULT_FAILED = "failed"
RESULT_SKIPPED = "skipped"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def due_member(guild_id: str, job_id: str) -> str:
    return f"{guild_id}:{job_id}"


def parse_due_member(member: str) -> Tuple[str, str]:
    guild_id, _, job_id = member.partition(":")
    return guild_id, job_id


class ScheduleRedisStore:
    def __init__(self) -> None:
        self.redis_client = get_redis_client()
        self.redis = self.redis_client.redis

    def _job_key(self, guild_id: str, job_id: str) -> str:
        return f"schedule:{guild_id}:job:{job_id}"

    def _jobs_key(self, guild_id: str) -> str:
        return f"schedule:{guild_id}:jobs"

    async def _save(self, record: Dict[str, Any]) -> None:
        record["updated_at"] = _now()
        await self.redis.set(
            self._job_key(record["guild_id"], record["job_id"]), json.dumps(record)
        )

    async def _set_due(self, record: Dict[str, Any], next_run: Optional[datetime]) -> None:
        member = due_member(record["guild_id"], record["job_id"])
        if next_run is None or record["status"] != STATUS_ACTIVE:
            await self.redis.zrem(DUE_KEY, member)
        else:
            await self.redis.zadd(DUE_KEY, {member: next_run.timestamp()})

    def _draft_key(self, guild_id: Any, channel_id: Any) -> str:
        return f"schedule:{guild_id_to_str(guild_id)}:draft:{channel_id_to_str(channel_id)}"

    async def save_draft(
        self, guild_id: Any, channel_id: Any, draft: Dict[str, Any], ttl_seconds: int
    ) -> None:
        await self.redis.set(
            self._draft_key(guild_id, channel_id), json.dumps(draft), ex=ttl_seconds
        )

    async def get_draft(self, guild_id: Any, channel_id: Any) -> Optional[Dict[str, Any]]:
        data = await self.redis.get(self._draft_key(guild_id, channel_id))
        if not data:
            return None
        try:
            draft = json.loads(data)
        except json.JSONDecodeError:
            return None
        return draft if isinstance(draft, dict) else None

    async def clear_draft(self, guild_id: Any, channel_id: Any) -> None:
        await self.redis.delete(self._draft_key(guild_id, channel_id))

    async def create_job(
        self,
        guild_id: Any,
        channel_id: Any,
        creator_id: Any,
        prompt: str,
        cron: str,
        tz: str,
        next_run: datetime,
    ) -> Dict[str, Any]:
        """Store a new active job. Callers check the caps first."""
        guild = guild_id_to_str(guild_id)
        now = _now()
        record: Dict[str, Any] = {
            "job_id": uuid.uuid4().hex[:12],
            "guild_id": guild,
            "channel_id": channel_id_to_str(channel_id),
            "creator_id": str(creator_id),
            "prompt": prompt,
            "cron": cron,
            "tz": tz,
            "status": STATUS_ACTIVE,
            "next_run": next_run.isoformat(),
            "created_at": now,
            "updated_at": now,
            "last_run_at": None,
            "last_result": None,
            "last_error": None,
            "consecutive_failures": 0,
            "run_count": 0,
            "paused_reason": None,
        }
        await self._save(record)
        await self.redis.sadd(self._jobs_key(guild), record["job_id"])
        await self._set_due(record, next_run)
        logger.info(f"schedule job {record['job_id']} created in guild {guild}")
        return dict(record)

    async def get_job(self, guild_id: Any, job_id: str) -> Optional[Dict[str, Any]]:
        data = await self.redis.get(self._job_key(guild_id_to_str(guild_id), job_id))
        if not data:
            return None
        try:
            record = json.loads(data)
        except json.JSONDecodeError as e:
            logger.error(f"Bad schedule job JSON for {job_id}: {e}")
            return None
        return record if isinstance(record, dict) else None

    async def list_jobs(self, guild_id: Any) -> List[Dict[str, Any]]:
        """Every job in the guild, oldest first. Drops ids whose record is gone."""
        guild = guild_id_to_str(guild_id)
        jobs = []
        for job_id in await self.redis.smembers(self._jobs_key(guild)):
            record = await self.get_job(guild, job_id)
            if record is None:
                # Evicted or half-deleted; don't let it hold a slot in the caps.
                await self.redis.srem(self._jobs_key(guild), job_id)
                await self.redis.zrem(DUE_KEY, due_member(guild, job_id))
                continue
            jobs.append(record)
        jobs.sort(key=lambda j: j.get("created_at") or "")
        return jobs

    async def delete_job(self, guild_id: Any, job_id: str) -> bool:
        """Cancel a job. True if there was one."""
        guild = guild_id_to_str(guild_id)
        await self.redis.zrem(DUE_KEY, due_member(guild, job_id))
        await self.redis.srem(self._jobs_key(guild), job_id)
        return bool(await self.redis.delete(self._job_key(guild, job_id)))

    async def set_status(
        self,
        guild_id: Any,
        job_id: str,
        status: str,
        next_run: Optional[datetime],
        reason: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Pause a job (next_run None) or make it active again with its next run."""
        record = await self.get_job(guild_id, job_id)
        if record is None:
            return None
        record["status"] = status
        record["next_run"] = next_run.isoformat() if next_run and status == STATUS_ACTIVE else None
        record["paused_reason"] = reason if status == STATUS_PAUSED else None
        if status == STATUS_ACTIVE:
            record["consecutive_failures"] = 0
        await self._save(record)
        await self._set_due(record, next_run)
        return record

    async def due_members(self, now: datetime) -> List[str]:
        """Members of `schedule:due` whose run time has come."""
        return list(await self.redis.zrangebyscore(DUE_KEY, "-inf", now.timestamp()))

    async def claim_due(self, member: str) -> bool:
        """Take a due job off the queue. False if something else already did."""
        return bool(await self.redis.zrem(DUE_KEY, member))

    async def reschedule(self, record: Dict[str, Any], next_run: datetime) -> None:
        """Set a claimed job's next run and put it back on the queue."""
        record["next_run"] = next_run.isoformat()
        await self._save(record)
        await self._set_due(record, next_run)

    async def record_run(
        self,
        guild_id: Any,
        job_id: str,
        result: str,
        error: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Note how a run went. Re-reads the record, so a pause made meanwhile stands."""
        record = await self.get_job(guild_id, job_id)
        if record is None:
            return None
        record["last_run_at"] = _now()
        record["last_result"] = result
        record["last_error"] = str(error)[:500] if error else None
        if result == RESULT_OK:
            record["run_count"] = int(record.get("run_count") or 0) + 1
            record["consecutive_failures"] = 0
        elif result == RESULT_FAILED:
            record["consecutive_failures"] = int(record.get("consecutive_failures") or 0) + 1
        await self._save(record)
        return record
