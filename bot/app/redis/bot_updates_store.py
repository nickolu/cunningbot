"""Redis storage layer for bot update notifications.

This module provides storage operations for managing channels that receive
bot restart notifications. Uses a global Redis Set for storing channel IDs.
"""

import logging
from typing import List

from bot.app.redis.client import get_redis_client

logger = logging.getLogger("BotUpdatesRedisStore")


class BotUpdatesRedisStore:
    """Redis storage operations for bot update notifications."""

    def __init__(self):
        self.redis_client = get_redis_client()
        self.redis = self.redis_client.redis
        self.channels_key = "bot_updates:channels"

    async def register_channel(self, channel_id: int) -> bool:
        """Register a channel for restart notifications.

        Args:
            channel_id: Discord channel ID

        Returns:
            True if channel was newly added, False if already registered
        """
        channel_str = str(channel_id)
        added = await self.redis.sadd(self.channels_key, channel_str)

        if added:
            logger.info(f"Registered channel {channel_id} for bot updates")
        else:
            logger.debug(f"Channel {channel_id} already registered")

        return added > 0

    async def unregister_channel(self, channel_id: int) -> bool:
        """Unregister a channel from restart notifications.

        Args:
            channel_id: Discord channel ID

        Returns:
            True if channel was removed, False if it wasn't registered
        """
        channel_str = str(channel_id)
        removed = await self.redis.srem(self.channels_key, channel_str)

        if removed:
            logger.info(f"Unregistered channel {channel_id} from bot updates")
        else:
            logger.debug(f"Channel {channel_id} was not registered")

        return removed > 0

    async def is_channel_registered(self, channel_id: int) -> bool:
        """Check if a channel is registered for notifications.

        Args:
            channel_id: Discord channel ID

        Returns:
            True if channel is registered, False otherwise
        """
        channel_str = str(channel_id)
        return await self.redis.sismember(self.channels_key, channel_str)

    async def get_all_registered_channels(self) -> List[int]:
        """Get all registered channel IDs.

        Returns:
            List of channel IDs as integers
        """
        channel_strings = await self.redis.smembers(self.channels_key)

        channel_ids = []
        for channel_str in channel_strings:
            try:
                channel_ids.append(int(channel_str))
            except ValueError as e:
                logger.error(f"Invalid channel ID in Redis: {channel_str} - {e}")

        return channel_ids

    async def get_registered_count(self) -> int:
        """Get count of registered channels.

        Returns:
            Number of registered channels
        """
        return await self.redis.scard(self.channels_key)
