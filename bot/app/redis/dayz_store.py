"""Redis storage layer for DayZ server monitoring.

Key schema:
    dayz:{guild_id}:servers   # Hash: server_key -> JSON server record
                              #   {name, host, game_port, query_port, added_by, added_at}
    dayz:{guild_id}:watches   # Hash: channel_id_str -> JSON watch record
                              #   {server, template, timezone, notify_channel_id,
                              #    state, failures, last_name, last_players,
                              #    last_checked_at, last_changed_at}

One watch per channel is enforced by the hash field being the channel id.
"""

import json
from typing import Any, Dict, List, Optional

from bot.app.redis.client import get_redis_client
from bot.app.utils.logger import get_logger

logger = get_logger()


class DayzRedisStore:
    def __init__(self):
        self.redis_client = get_redis_client()
        self.redis = self.redis_client.redis

    @staticmethod
    def _servers_key(guild_id: str) -> str:
        return f"dayz:{guild_id}:servers"

    @staticmethod
    def _watches_key(guild_id: str) -> str:
        return f"dayz:{guild_id}:watches"

    @staticmethod
    def _decode(raw: Optional[str], what: str) -> Optional[Dict[str, Any]]:
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.error(f"Bad JSON for {what}: {exc}")
            return None

    # --- Servers ---

    async def get_server(self, guild_id: str, key: str) -> Optional[Dict[str, Any]]:
        raw = await self.redis.hget(self._servers_key(guild_id), key)
        return self._decode(raw, f"dayz server {guild_id}/{key}")

    async def save_server(self, guild_id: str, key: str, record: Dict[str, Any]) -> None:
        await self.redis.hset(self._servers_key(guild_id), key, json.dumps(record))

    async def delete_server(self, guild_id: str, key: str) -> bool:
        return (await self.redis.hdel(self._servers_key(guild_id), key)) > 0

    async def get_all_servers(self, guild_id: str) -> Dict[str, Dict[str, Any]]:
        raw = await self.redis.hgetall(self._servers_key(guild_id))
        out: Dict[str, Dict[str, Any]] = {}
        for key, value in raw.items():
            rec = self._decode(value, f"dayz server {guild_id}/{key}")
            if rec:
                out[key] = rec
        return out

    # --- Watches ---

    async def get_watch(self, guild_id: str, channel_id: str) -> Optional[Dict[str, Any]]:
        raw = await self.redis.hget(self._watches_key(guild_id), channel_id)
        return self._decode(raw, f"dayz watch {guild_id}/{channel_id}")

    async def save_watch(self, guild_id: str, channel_id: str, record: Dict[str, Any]) -> None:
        await self.redis.hset(self._watches_key(guild_id), channel_id, json.dumps(record))

    async def delete_watch(self, guild_id: str, channel_id: str) -> bool:
        return (await self.redis.hdel(self._watches_key(guild_id), channel_id)) > 0

    async def get_all_watches(self, guild_id: str) -> Dict[str, Dict[str, Any]]:
        raw = await self.redis.hgetall(self._watches_key(guild_id))
        out: Dict[str, Dict[str, Any]] = {}
        for channel_id, value in raw.items():
            rec = self._decode(value, f"dayz watch {guild_id}/{channel_id}")
            if rec:
                out[channel_id] = rec
        return out

    async def delete_watches_for_server(self, guild_id: str, server_key: str) -> List[str]:
        """Remove every watch bound to `server_key`; returns the channel ids removed."""
        removed: List[str] = []
        for channel_id, watch in (await self.get_all_watches(guild_id)).items():
            if watch.get("server") == server_key:
                await self.delete_watch(guild_id, channel_id)
                removed.append(channel_id)
        return removed

    async def get_all_guilds_with_watches(self) -> List[str]:
        """Every guild id that has at least one watch (SCAN, never KEYS)."""
        guilds: List[str] = []
        cursor = 0
        while True:
            cursor, keys = await self.redis.scan(cursor, match="dayz:*:watches", count=100)
            for key in keys:
                parts = key.split(":")
                if len(parts) >= 3 and await self.redis.hlen(key) > 0:
                    guilds.append(parts[1])
            if cursor == 0:
                break
        return guilds
