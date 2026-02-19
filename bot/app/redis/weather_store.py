"""Redis storage layer for weather schedules and post deduplication.

Key schema:
    weather:{guild_id}:schedules             # Hash: channel_id_str -> JSON config
    weather:{guild_id}:posted:{channel_id}   # Hash: "YYYY-MM-DD:HH:MM" -> ISO timestamp (48h TTL)
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from bot.app.redis.client import get_redis_client

logger = logging.getLogger("WeatherRedisStore")


class WeatherRedisStore:
    """Redis storage for weather schedules and post deduplication."""

    def __init__(self):
        self.redis_client = get_redis_client()
        self.redis = self.redis_client.redis

    # --- Schedule Management ---

    async def get_schedule(
        self, guild_id: str, channel_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get weather schedule config for a specific channel."""
        key = f"weather:{guild_id}:schedules"
        data = await self.redis.hget(key, channel_id)
        if not data:
            return None
        try:
            return json.loads(data)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode schedule for channel {channel_id}: {e}")
            return None

    async def save_schedule(
        self, guild_id: str, channel_id: str, config: Dict[str, Any]
    ) -> None:
        """Save (create or update) a weather schedule config for a channel."""
        key = f"weather:{guild_id}:schedules"
        await self.redis.hset(key, channel_id, json.dumps(config))
        logger.info(f"Saved weather schedule for guild {guild_id} channel {channel_id}")

    async def delete_schedule(self, guild_id: str, channel_id: str) -> bool:
        """Delete the weather schedule for a channel.

        Returns:
            True if a schedule was deleted, False if none existed.
        """
        key = f"weather:{guild_id}:schedules"
        deleted = await self.redis.hdel(key, channel_id)
        return deleted > 0

    async def disable_schedule(self, guild_id: str, channel_id: str) -> bool:
        """Disable a schedule without deleting it (e.g. channel not found).

        Returns:
            True if schedule was found and disabled, False otherwise.
        """
        schedule = await self.get_schedule(guild_id, channel_id)
        if not schedule:
            return False
        schedule["enabled"] = False
        await self.save_schedule(guild_id, channel_id, schedule)
        logger.info(f"Disabled weather schedule for guild {guild_id} channel {channel_id}")
        return True

    async def get_all_schedules(self, guild_id: str) -> Dict[str, Dict[str, Any]]:
        """Get all weather schedule configs for a guild.

        Returns:
            Dict mapping channel_id_str -> config dict
        """
        key = f"weather:{guild_id}:schedules"
        raw = await self.redis.hgetall(key)
        result = {}
        for channel_id_str, data in raw.items():
            try:
                result[channel_id_str] = json.loads(data)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to decode schedule for channel {channel_id_str}: {e}")
        return result

    async def get_all_guilds_with_schedules(self) -> List[str]:
        """Get all guild IDs that have at least one weather schedule configured."""
        pattern = "weather:*:schedules"
        guilds = []

        cursor = 0
        while True:
            cursor, keys = await self.redis.scan(cursor, match=pattern, count=100)
            for key in keys:
                parts = key.split(":")
                if len(parts) >= 2:
                    guild_id = parts[1]
                    hash_len = await self.redis.hlen(key)
                    if hash_len > 0:
                        guilds.append(guild_id)
            if cursor == 0:
                break

        return guilds

    # --- Post Deduplication ---

    async def has_posted(
        self, guild_id: str, channel_id: str, slot_key: str
    ) -> bool:
        """Check if a weather post has already been sent for this slot.

        Args:
            guild_id: Guild ID string
            channel_id: Channel ID string
            slot_key: Dedup key in format "YYYY-MM-DD:HH:MM"
        """
        key = f"weather:{guild_id}:posted:{channel_id}"
        return bool(await self.redis.hexists(key, slot_key))

    async def mark_posted(
        self, guild_id: str, channel_id: str, slot_key: str
    ) -> None:
        """Record that a weather post was sent for this slot.

        Sets a 48-hour TTL on the dedup hash to auto-clean old entries.

        Args:
            guild_id: Guild ID string
            channel_id: Channel ID string
            slot_key: Dedup key in format "YYYY-MM-DD:HH:MM"
        """
        key = f"weather:{guild_id}:posted:{channel_id}"
        timestamp = datetime.utcnow().isoformat()
        await self.redis.hset(key, slot_key, timestamp)
        await self.redis.expire(key, 48 * 3600)  # 48h TTL
        logger.info(
            f"Marked posted for guild {guild_id} channel {channel_id} slot {slot_key}"
        )
