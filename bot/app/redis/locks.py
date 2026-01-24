"""Distributed lock utilities for Redis."""

import uuid
from contextlib import asynccontextmanager

from bot.app.redis.exceptions import LockAcquisitionError


@asynccontextmanager
async def redis_lock(redis_client, resource: str, timeout: int = 10):
    """
    Distributed lock with automatic release.

    Args:
        redis_client: Redis client instance
        resource: Resource identifier (e.g., "trivia:game:123")
        timeout: Lock TTL in seconds (auto-releases if holder dies)

    Raises:
        LockAcquisitionError: If lock cannot be acquired

    Usage:
        async with redis_lock(redis_client, "trivia:game:abc", timeout=30):
            # Critical section - only one container executes
            pass
    """
    lock_key = f"lock:{resource}"
    lock_value = str(uuid.uuid4())
    acquired = False

    try:
        # Acquire lock with SET NX EX
        acquired = await redis_client.redis.set(
            lock_key, lock_value, nx=True, ex=timeout  # Only set if not exists
        )

        if not acquired:
            raise LockAcquisitionError(f"Could not acquire lock: {resource}")

        yield

    finally:
        if acquired:
            # Lua script to ensure we only delete our own lock
            lua_script = """
            if redis.call("get", KEYS[1]) == ARGV[1] then
                return redis.call("del", KEYS[1])
            else
                return 0
            end
            """
            await redis_client.redis.eval(lua_script, 1, lock_key, lock_value)
