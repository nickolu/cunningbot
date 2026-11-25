"""Redis storage layer for the per-server calendar.

Key schema:
    calendar:{guild_id}:config              # JSON: {enabled, timezone, enabled_by, enabled_at}
    calendar:{guild_id}:events              # Hash: event_id -> JSON CalendarEvent record
    calendar:{guild_id}:by_start            # ZSET: event_id scored by start epoch (UTC seconds)
    calendar:{guild_id}:message:{msg_id}    # Set: event ids captured from that message (90-day TTL)
    calendar:{guild_id}:budget:{YYYYMMDD}   # Counter: model calls made that local day (2-day TTL)

The hash is the record of truth; the sorted set is the index that makes
"what's coming up" one range query. `add_event` writes both, and the readers
tolerate an id that is in the index but gone from the hash (eviction is
possible: Redis here is allkeys-lru).
"""

import datetime as dt
import json
from typing import Any, Dict, List, Optional

from bot.app.redis.client import get_redis_client
from bot.app.utils.logger import get_logger
from bot.domain.calendar.models import DEFAULT_TIMEZONE, CalendarEvent

logger = get_logger()

MESSAGE_LINK_TTL_SECONDS = 90 * 24 * 3600
BUDGET_TTL_SECONDS = 2 * 24 * 3600


class CalendarRedisStore:
    def __init__(self):
        self.redis_client = get_redis_client()
        self.redis = self.redis_client.redis

    # ------------------------------------------------------------------ keys

    @staticmethod
    def _config_key(guild_id: str) -> str:
        return f"calendar:{guild_id}:config"

    @staticmethod
    def _events_key(guild_id: str) -> str:
        return f"calendar:{guild_id}:events"

    @staticmethod
    def _index_key(guild_id: str) -> str:
        return f"calendar:{guild_id}:by_start"

    @staticmethod
    def _message_key(guild_id: str, message_id: str) -> str:
        return f"calendar:{guild_id}:message:{message_id}"

    @staticmethod
    def _budget_key(guild_id: str, day: str) -> str:
        return f"calendar:{guild_id}:budget:{day}"

    # ---------------------------------------------------------------- config

    async def get_config(self, guild_id: str) -> Dict[str, Any]:
        raw = await self.redis.get(self._config_key(guild_id))
        if not raw:
            return {"enabled": False, "timezone": DEFAULT_TIMEZONE}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error(f"Bad calendar config JSON for guild {guild_id}: {e}")
            return {"enabled": False, "timezone": DEFAULT_TIMEZONE}
        data.setdefault("enabled", False)
        data.setdefault("timezone", DEFAULT_TIMEZONE)
        return data

    async def save_config(self, guild_id: str, config: Dict[str, Any]) -> None:
        await self.redis.set(self._config_key(guild_id), json.dumps(config))

    async def is_enabled(self, guild_id: str) -> bool:
        return bool((await self.get_config(guild_id)).get("enabled"))

    # ---------------------------------------------------------------- events

    def _decode(self, raw: Optional[str], what: str) -> Optional[CalendarEvent]:
        if not raw:
            return None
        try:
            record = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error(f"Bad calendar event JSON for {what}: {e}")
            return None
        if not isinstance(record, dict):
            return None
        return CalendarEvent.from_record(record)

    async def event_id_exists(self, guild_id: str, event_id: str) -> bool:
        return bool(await self.redis.hexists(self._events_key(guild_id), event_id))

    async def add_event(self, guild_id: str, event: CalendarEvent) -> None:
        pipe = self.redis.pipeline()
        pipe.hset(self._events_key(guild_id), event.id, json.dumps(event.to_record()))
        pipe.zadd(self._index_key(guild_id), {event.id: event.start_epoch()})
        if event.message_id:
            key = self._message_key(guild_id, event.message_id)
            pipe.sadd(key, event.id)
            pipe.expire(key, MESSAGE_LINK_TTL_SECONDS)
        await pipe.execute()

    async def get_event(self, guild_id: str, event_id: str) -> Optional[CalendarEvent]:
        raw = await self.redis.hget(self._events_key(guild_id), event_id)
        return self._decode(raw, f"{guild_id}/{event_id}")

    async def delete_event(self, guild_id: str, event_id: str) -> Optional[CalendarEvent]:
        """Remove one event; returns it, or None if it was not there."""
        event = await self.get_event(guild_id, event_id)
        pipe = self.redis.pipeline()
        pipe.hdel(self._events_key(guild_id), event_id)
        pipe.zrem(self._index_key(guild_id), event_id)
        if event is not None and event.message_id:
            pipe.srem(self._message_key(guild_id, event.message_id), event_id)
        await pipe.execute()
        return event

    async def get_events_for_message(self, guild_id: str, message_id: str) -> List[CalendarEvent]:
        ids = await self.redis.smembers(self._message_key(guild_id, message_id))
        return await self._events_by_ids(guild_id, sorted(ids))

    async def delete_events_for_message(self, guild_id: str, message_id: str) -> List[CalendarEvent]:
        events = await self.get_events_for_message(guild_id, message_id)
        for event in events:
            await self.delete_event(guild_id, event.id)
        await self.redis.delete(self._message_key(guild_id, message_id))
        return events

    async def _events_by_ids(self, guild_id: str, ids: List[str]) -> List[CalendarEvent]:
        if not ids:
            return []
        raws = await self.redis.hmget(self._events_key(guild_id), ids)
        events: List[CalendarEvent] = []
        stale: List[str] = []
        for event_id, raw in zip(ids, raws):
            event = self._decode(raw, f"{guild_id}/{event_id}")
            if event is None:
                stale.append(event_id)
            else:
                events.append(event)
        if stale:
            await self.redis.zrem(self._index_key(guild_id), *stale)
        return events

    async def get_events_between(self, guild_id: str, start_epoch: float, end_epoch: float) -> List[CalendarEvent]:
        """Events starting in [start_epoch, end_epoch), soonest first."""
        ids = await self.redis.zrangebyscore(self._index_key(guild_id), start_epoch, "(%r" % end_epoch)
        return await self._events_by_ids(guild_id, list(ids))

    async def get_events_on(self, guild_id: str, date_iso: str, tz_name: str) -> List[CalendarEvent]:
        """Events whose start falls on that local day, for dedup."""
        from bot.domain.calendar.models import zone
        day = dt.date.fromisoformat(date_iso)
        tz = zone(tz_name)
        start = dt.datetime(day.year, day.month, day.day, tzinfo=tz)
        end = start + dt.timedelta(days=1)
        # A stored event may carry a different zone; widen by a day each side
        # and let the caller compare the date strings.
        found = await self.get_events_between(
            guild_id, (start - dt.timedelta(days=1)).timestamp(), (end + dt.timedelta(days=1)).timestamp()
        )
        return [e for e in found if e.date == date_iso]

    async def count_upcoming(self, guild_id: str, from_epoch: float) -> int:
        return int(await self.redis.zcount(self._index_key(guild_id), from_epoch, "+inf"))

    async def trim_past(self, guild_id: str, before_epoch: float) -> int:
        """Drop events that started before `before_epoch`. Returns how many."""
        ids = await self.redis.zrangebyscore(self._index_key(guild_id), "-inf", "(%r" % before_epoch)
        for event_id in ids:
            await self.delete_event(guild_id, event_id)
        return len(ids)

    # ---------------------------------------------------------------- budget

    async def spend_budget(self, guild_id: str, day: str) -> int:
        """Count one model call for that local day; returns the day's total so far."""
        key = self._budget_key(guild_id, day)
        pipe = self.redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, BUDGET_TTL_SECONDS)
        count, _ = await pipe.execute()
        return int(count)
