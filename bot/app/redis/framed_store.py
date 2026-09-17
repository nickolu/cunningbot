"""Redis storage layer for Framed results.

The results channel's message history is the record; everything here can be
rebuilt from it with /framed backfill except manual fixes (overrides).

Key schema:
    framed:{guild_id}:config          # JSON: {channel_id, timezone, recap, first_day, registered_at}
    framed:{guild_id}:day:{date}      # Hash: user_id -> JSON {score, message_id, posted_at, source}
    framed:{guild_id}:synced          # Hash: date -> JSON {synced_at, players, llm_failed, attempts, recap_posted}
    framed:{guild_id}:overrides       # Hash: "{date}:{user_id}" -> JSON {score (null = removed), by, at}
    framed:{guild_id}:players         # Hash: user_id -> display name
    framed:{guild_id}:status          # JSON: {last_run_at, last_error, last_error_at, ...}

Dates are YYYY-MM-DD in the configured timezone. A date missing from `synced`
has not been read yet and the sync worker will pick it up.
"""

import json
from typing import Any, Dict, Iterable, List, Optional

from bot.app.redis.client import get_redis_client
from bot.app.utils.logger import get_logger

logger = get_logger()


def _loads(data: Optional[str], what: str) -> Optional[Any]:
    if not data:
        return None
    try:
        return json.loads(data)
    except json.JSONDecodeError as e:
        logger.error(f"Bad Framed {what} JSON: {e}")
        return None


class FramedRedisStore:
    def __init__(self):
        self.redis_client = get_redis_client()
        self.redis = self.redis_client.redis

    def _key(self, guild_id: str, suffix: str) -> str:
        return f"framed:{guild_id}:{suffix}"

    # --- Config ---

    async def get_config(self, guild_id: str) -> Optional[Dict[str, Any]]:
        return _loads(await self.redis.get(self._key(guild_id, "config")), "config")

    async def save_config(self, guild_id: str, config: Dict[str, Any]) -> None:
        await self.redis.set(self._key(guild_id, "config"), json.dumps(config))

    async def delete_config(self, guild_id: str) -> bool:
        """Stop tracking. Results stay, so re-registering picks up where it left off."""
        return bool(await self.redis.delete(self._key(guild_id, "config")))

    async def get_all_guilds_with_config(self) -> List[str]:
        guilds: List[str] = []
        cursor = 0
        while True:
            cursor, keys = await self.redis.scan(cursor, match="framed:*:config", count=100)
            for key in keys:
                parts = key.split(":")
                if len(parts) == 3:
                    guilds.append(parts[1])
            if cursor == 0:
                break
        return guilds

    # --- Days ---

    async def get_synced(self, guild_id: str) -> Dict[str, Dict[str, Any]]:
        raw = await self.redis.hgetall(self._key(guild_id, "synced"))
        synced: Dict[str, Dict[str, Any]] = {}
        for day, data in raw.items():
            meta = _loads(data, "synced")
            synced[day] = meta if isinstance(meta, dict) else {}
        return synced

    async def save_day(
        self,
        guild_id: str,
        day: str,
        results: Dict[str, Dict[str, Any]],
        meta: Dict[str, Any],
    ) -> None:
        """Replace one day's results and record it as synced, atomically."""
        day_key = self._key(guild_id, f"day:{day}")
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.delete(day_key)
            if results:
                pipe.hset(
                    day_key,
                    mapping={uid: json.dumps(r) for uid, r in results.items()},
                )
            pipe.hset(self._key(guild_id, "synced"), day, json.dumps(meta))
            await pipe.execute()

    async def unmark_days(self, guild_id: str, days: Iterable[str]) -> int:
        """Forget that these days were synced, so the next sync re-reads them."""
        days = list(days)
        if not days:
            return 0
        return int(await self.redis.hdel(self._key(guild_id, "synced"), *days))

    async def update_synced_meta(
        self, guild_id: str, day: str, changes: Dict[str, Any]
    ) -> None:
        key = self._key(guild_id, "synced")
        meta = _loads(await self.redis.hget(key, day), "synced")
        if not isinstance(meta, dict):
            return
        meta.update(changes)
        await self.redis.hset(key, day, json.dumps(meta))

    async def get_days(
        self, guild_id: str, days: Iterable[str]
    ) -> Dict[str, Dict[str, Dict[str, Any]]]:
        days = list(days)
        if not days:
            return {}
        async with self.redis.pipeline(transaction=False) as pipe:
            for day in days:
                pipe.hgetall(self._key(guild_id, f"day:{day}"))
            raws = await pipe.execute()
        out: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for day, raw in zip(days, raws):
            results: Dict[str, Dict[str, Any]] = {}
            for uid, data in (raw or {}).items():
                record = _loads(data, "result")
                if isinstance(record, dict):
                    results[uid] = record
            out[day] = results
        return out

    # --- Manual fixes ---

    async def get_overrides(self, guild_id: str) -> Dict[str, Dict[str, Any]]:
        raw = await self.redis.hgetall(self._key(guild_id, "overrides"))
        out: Dict[str, Dict[str, Any]] = {}
        for field, data in raw.items():
            record = _loads(data, "override")
            if isinstance(record, dict):
                out[field] = record
        return out

    async def set_override(
        self, guild_id: str, day: str, user_id: str, record: Dict[str, Any]
    ) -> None:
        await self.redis.hset(
            self._key(guild_id, "overrides"), f"{day}:{user_id}", json.dumps(record)
        )

    async def delete_override(self, guild_id: str, day: str, user_id: str) -> bool:
        return bool(
            await self.redis.hdel(self._key(guild_id, "overrides"), f"{day}:{user_id}")
        )

    # --- Players ---

    async def get_players(self, guild_id: str) -> Dict[str, str]:
        return dict(await self.redis.hgetall(self._key(guild_id, "players")))

    async def set_players(self, guild_id: str, names: Dict[str, str]) -> None:
        if names:
            await self.redis.hset(self._key(guild_id, "players"), mapping=names)

    # --- Sync status ---

    async def get_status(self, guild_id: str) -> Dict[str, Any]:
        status = _loads(await self.redis.get(self._key(guild_id, "status")), "status")
        return status if isinstance(status, dict) else {}

    async def update_status(self, guild_id: str, changes: Dict[str, Any]) -> None:
        status = await self.get_status(guild_id)
        status.update(changes)
        await self.redis.set(self._key(guild_id, "status"), json.dumps(status))
