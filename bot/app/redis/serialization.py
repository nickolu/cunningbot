"""Type conversion and serialization utilities for Redis."""

import json
import logging
from typing import Any, Optional
from datetime import datetime

logger = logging.getLogger("RedisSerialization")


def serialize_to_redis(value: Any) -> str:
    """Convert Python object to Redis string.

    Args:
        value: Python object to serialize

    Returns:
        JSON string representation

    Raises:
        TypeError: If value cannot be serialized
    """
    if isinstance(value, (str, int, float, bool)):
        return json.dumps(value)
    elif isinstance(value, datetime):
        return value.isoformat()
    elif isinstance(value, dict) or isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    else:
        raise TypeError(f"Cannot serialize type {type(value)} to Redis")


def deserialize_from_redis(
    value: Optional[str], expected_type: type = dict
) -> Any:
    """Convert Redis string to Python object.

    Args:
        value: JSON string from Redis
        expected_type: Expected Python type for validation

    Returns:
        Deserialized Python object or None
    """
    if value is None:
        return None

    try:
        data = json.loads(value)

        # Type validation
        if expected_type and not isinstance(data, expected_type):
            logger.warning(f"Expected {expected_type}, got {type(data)}")

        return data
    except json.JSONDecodeError as e:
        logger.error(f"Failed to deserialize Redis value: {e}")
        return None


def guild_id_to_str(guild_id: Optional[int]) -> str:
    """Convert Discord guild ID to string for Redis keys.

    Args:
        guild_id: Discord guild ID (integer) or None for global

    Returns:
        String representation ("global" if None)
    """
    if guild_id is None:
        return "global"
    return str(guild_id)


def channel_id_to_str(channel_id: int) -> str:
    """Convert Discord channel ID to string.

    Args:
        channel_id: Discord channel ID (integer)

    Returns:
        String representation
    """
    return str(channel_id)
