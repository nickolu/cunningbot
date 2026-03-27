"""Redis storage layer for channel agent registrations.

Stores per-channel agent configuration so the on_message listener
knows which channels have an active agent and how it's configured.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from bot.app.redis.client import get_redis_client

logger = logging.getLogger("AgentRedisStore")

# Default agent configuration
DEFAULT_AGENT_CONFIG = {
    "enabled": True,
    "persona": None,  # None = use guild default
    "model": "gpt-4o",
    "tools": ["weather", "image", "dice", "search_gifs", "edit_image", "web_search", "read_channel"],
    "context_window": 30,
    "cooldown_seconds": 5,
    "max_responses_per_minute": 10,
    "response_mode": "smart",  # "smart", "strict", or "always"
}


class AgentRedisStore:
    """Redis storage operations for channel agent registrations."""

    KEY_PREFIX = "agent"

    def __init__(self):
        self.redis_client = get_redis_client()
        self.redis = self.redis_client.redis

    def _key(self, guild_id: str, channel_id: str) -> str:
        return f"{self.KEY_PREFIX}:{guild_id}:{channel_id}"

    async def register_agent(
        self,
        guild_id: str,
        channel_id: str,
        config: Dict[str, Any],
        registered_by: str,
    ) -> None:
        """Register an agent in a channel."""
        data = {**DEFAULT_AGENT_CONFIG, **config}
        data["registered_by"] = registered_by
        key = self._key(guild_id, channel_id)
        await self.redis.set(key, json.dumps(data))
        logger.info(f"Agent registered in channel {channel_id} (guild {guild_id})")

    async def unregister_agent(self, guild_id: str, channel_id: str) -> bool:
        """Remove agent registration. Returns True if it existed."""
        key = self._key(guild_id, channel_id)
        removed = await self.redis.delete(key)
        if removed:
            logger.info(f"Agent unregistered from channel {channel_id} (guild {guild_id})")
        return removed > 0

    async def get_agent_config(self, guild_id: str, channel_id: str) -> Optional[Dict[str, Any]]:
        """Get agent config for a channel, or None if not registered."""
        key = self._key(guild_id, channel_id)
        raw = await self.redis.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.error(f"Invalid JSON in agent config for {key}")
            return None

    async def update_agent_config(
        self, guild_id: str, channel_id: str, updates: Dict[str, Any]
    ) -> bool:
        """Update specific fields of an existing agent config. Returns False if not registered."""
        config = await self.get_agent_config(guild_id, channel_id)
        if config is None:
            return False
        config.update(updates)
        key = self._key(guild_id, channel_id)
        await self.redis.set(key, json.dumps(config))
        return True

    async def list_agents_for_guild(self, guild_id: str) -> List[Dict[str, Any]]:
        """List all agent registrations for a guild."""
        pattern = f"{self.KEY_PREFIX}:{guild_id}:*"
        results = []
        async for key in self.redis.scan_iter(match=pattern):
            raw = await self.redis.get(key)
            if raw:
                try:
                    config = json.loads(raw)
                    # Extract channel_id from key
                    channel_id = key.split(":")[-1]
                    config["channel_id"] = channel_id
                    results.append(config)
                except json.JSONDecodeError:
                    pass
        return results
