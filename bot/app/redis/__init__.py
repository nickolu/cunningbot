"""Redis client module for CunningBot.

This module provides Redis-based state management to replace JSON file storage,
eliminating race conditions and improving performance across multiple containers.
"""

from bot.app.redis.client import (
    RedisClient,
    get_redis_client,
    initialize_redis,
    close_redis,
)
from bot.app.redis.exceptions import (
    RedisOperationError,
    LockAcquisitionError,
    RetryableRedisError,
)
from bot.app.redis.locks import redis_lock

__all__ = [
    "RedisClient",
    "get_redis_client",
    "initialize_redis",
    "close_redis",
    "RedisOperationError",
    "LockAcquisitionError",
    "RetryableRedisError",
    "redis_lock",
]
