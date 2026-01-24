#!/usr/bin/env python3
"""Test Redis connection and basic operations.

This script verifies that Redis is properly configured and accessible.

Usage:
    python scripts/test_redis_connection.py
"""
import asyncio
import sys
from pathlib import Path

# Add bot module to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.app.redis.client import initialize_redis, close_redis, get_redis_client


async def test_redis_connection():
    """Test Redis connection and basic operations."""
    print("🔍 Testing Redis connection...")

    try:
        # Initialize
        await initialize_redis()
        print("✓ Redis client initialized")

        # Get client
        redis_client = get_redis_client()
        redis = redis_client.redis

        # Test PING
        pong = await redis.ping()
        print(f"✓ PING: {pong}")

        # Test SET/GET
        await redis.set("test:connection", "success")
        value = await redis.get("test:connection")
        print(f"✓ SET/GET: {value}")

        # Test DELETE
        deleted = await redis.delete("test:connection")
        print(f"✓ DELETE: {deleted} key(s) deleted")

        # Test INFO
        info = await redis.info()
        print(f"✓ Redis version: {info.get('redis_version')}")
        print(f"✓ Connected clients: {info.get('connected_clients')}")
        print(f"✓ Used memory: {info.get('used_memory_human')}")

        print("\n✅ All tests passed!")

    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
    finally:
        await close_redis()


if __name__ == "__main__":
    asyncio.run(test_redis_connection())
