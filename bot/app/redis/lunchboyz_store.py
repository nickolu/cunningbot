"""Redis storage layer for Lunch Boyz rotation.

Key schema:
    lunchboyz:{guild_id}:config    # JSON: {channel_id, frequency_days, timezone}
    lunchboyz:{guild_id}:rotation  # JSON: [user_id_str, ...]
    lunchboyz:{guild_id}:state     # JSON: {current_index, last_advanced, event, reminders_sent}
"""

import json
import logging
from typing import Any, Dict, List, Optional

from bot.app.redis.client import get_redis_client

logger = logging.getLogger("LunchboyzRedisStore")


class LunchboyzRedisStore:
    """Redis storage for Lunch Boyz rotation data."""

    def __init__(self):
        self.redis_client = get_redis_client()
        self.redis = self.redis_client.redis

    # --- Config ---

    async def get_config(self, guild_id: str) -> Optional[Dict[str, Any]]:
        key = f"lunchboyz:{guild_id}:config"
        data = await self.redis.get(key)
        if not data:
            return None
        try:
            return json.loads(data)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode config for guild {guild_id}: {e}")
            return None

    async def save_config(self, guild_id: str, config: Dict[str, Any]) -> None:
        key = f"lunchboyz:{guild_id}:config"
        await self.redis.set(key, json.dumps(config))
        logger.info(f"Saved lunchboyz config for guild {guild_id}")

    # --- Rotation ---

    async def get_rotation(self, guild_id: str) -> Optional[List[str]]:
        key = f"lunchboyz:{guild_id}:rotation"
        data = await self.redis.get(key)
        if not data:
            return None
        try:
            return json.loads(data)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode rotation for guild {guild_id}: {e}")
            return None

    async def save_rotation(self, guild_id: str, rotation: List[str]) -> None:
        key = f"lunchboyz:{guild_id}:rotation"
        await self.redis.set(key, json.dumps(rotation))
        logger.info(f"Saved lunchboyz rotation for guild {guild_id}")

    # --- State ---

    async def get_state(self, guild_id: str) -> Optional[Dict[str, Any]]:
        key = f"lunchboyz:{guild_id}:state"
        data = await self.redis.get(key)
        if not data:
            return None
        try:
            return json.loads(data)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode state for guild {guild_id}: {e}")
            return None

    async def save_state(self, guild_id: str, state: Dict[str, Any]) -> None:
        key = f"lunchboyz:{guild_id}:state"
        await self.redis.set(key, json.dumps(state))
        logger.info(f"Saved lunchboyz state for guild {guild_id}")

    # --- Guild Discovery ---

    async def get_all_guilds_with_config(self) -> List[str]:
        """Get all guild IDs that have lunchboyz configured."""
        pattern = "lunchboyz:*:config"
        guilds = []
        cursor = 0
        while True:
            cursor, keys = await self.redis.scan(cursor, match=pattern, count=100)
            for key in keys:
                parts = key.split(":")
                if len(parts) >= 2:
                    guilds.append(parts[1])
            if cursor == 0:
                break
        return guilds
