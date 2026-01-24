"""Redis client singleton with connection pooling and Lua script management."""

import os
import logging
from typing import Optional
from pathlib import Path

import redis.asyncio as redis
from redis.asyncio.connection import ConnectionPool

logger = logging.getLogger("RedisClient")


class RedisClient:
    """Singleton Redis client with connection pooling."""

    _instance: Optional["RedisClient"] = None
    _redis: Optional[redis.Redis] = None
    _pool: Optional[ConnectionPool] = None
    _scripts: dict[str, str] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def initialize(self):
        """Initialize Redis connection pool."""
        if self._redis is not None:
            return  # Already initialized

        redis_host = os.getenv("REDIS_HOST", "redis")
        redis_port = int(os.getenv("REDIS_PORT", "6379"))
        redis_db = int(os.getenv("REDIS_DB", "0"))
        redis_password = os.getenv("REDIS_PASSWORD")

        # Connection pool configuration
        self._pool = ConnectionPool(
            host=redis_host,
            port=redis_port,
            db=redis_db,
            password=redis_password if redis_password else None,
            encoding="utf-8",
            decode_responses=True,
            max_connections=20,  # Tune based on container count
            socket_timeout=5,
            socket_connect_timeout=5,
            retry_on_timeout=True,
            health_check_interval=30,
        )

        self._redis = redis.Redis(connection_pool=self._pool)

        # Test connection
        try:
            await self._redis.ping()
            logger.info(f"Redis connected: {redis_host}:{redis_port} (DB {redis_db})")
        except Exception as e:
            logger.error(f"Redis connection failed: {e}")
            raise

        # Load Lua scripts
        await self._load_lua_scripts()

    async def _load_lua_scripts(self):
        """Load Lua scripts for atomic operations."""
        self._scripts = {}
        script_dir = Path(__file__).parent / "scripts"

        if not script_dir.exists():
            logger.warning(f"Lua scripts directory not found: {script_dir}")
            return

        for script_file in script_dir.glob("*.lua"):
            script_name = script_file.stem
            script_content = script_file.read_text()

            try:
                script_sha = await self._redis.script_load(script_content)
                self._scripts[script_name] = script_sha
                logger.info(f"Loaded Lua script: {script_name}")
            except Exception as e:
                logger.error(f"Failed to load Lua script {script_name}: {e}")

    async def close(self):
        """Close Redis connection pool."""
        if self._redis:
            await self._redis.close()
            await self._pool.disconnect()
            logger.info("Redis connection closed")

    @property
    def redis(self) -> redis.Redis:
        """Get Redis client instance."""
        if self._redis is None:
            raise RuntimeError("Redis client not initialized. Call initialize() first.")
        return self._redis

    async def execute_script(self, script_name: str, keys: list, args: list):
        """Execute a loaded Lua script.

        Converts Lua array returns to Python dictionaries.
        Scripts should return arrays like ["key", "value"] which get converted to {"key": "value"}.
        """
        if script_name not in self._scripts:
            raise ValueError(f"Lua script not loaded: {script_name}")

        script_sha = self._scripts[script_name]
        result = await self._redis.evalsha(script_sha, len(keys), *keys, *args)

        # Convert 2-element array to dictionary
        if isinstance(result, list) and len(result) == 2:
            return {result[0]: result[1]}

        return result


# Global singleton instance
_redis_client: Optional[RedisClient] = None


def get_redis_client() -> RedisClient:
    """Get the global Redis client instance."""
    global _redis_client
    if _redis_client is None:
        _redis_client = RedisClient()
    return _redis_client


async def initialize_redis():
    """Initialize Redis client (call on bot startup)."""
    client = get_redis_client()
    await client.initialize()


async def close_redis():
    """Close Redis client (call on bot shutdown)."""
    client = get_redis_client()
    await client.close()
