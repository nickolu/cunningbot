"""pending_breaking_news.py
Manages pending breaking news items separate from app_state.

Items are stored in pending_breaking_news.json with structure:
{
    "guild_id": {
        "pending_items": [
            {
                "article": {...},
                "matched_topic": "hurricane",
                "collected_at": "2026-01-22T10:15:00Z",
                "feed_name": "CNN Breaking News",
                "retry_count": 0
            }
        ]
    }
}
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

from bot.app.utils.logger import get_logger

logger = get_logger()

# Path to pending breaking news JSON file
PENDING_BREAKING_NEWS_FILE = Path(__file__).parent / "pending_breaking_news.json"


def load_pending_breaking_news() -> Dict[str, Any]:
    """Load pending breaking news from JSON file."""
    if not PENDING_BREAKING_NEWS_FILE.exists():
        return {}

    try:
        with open(PENDING_BREAKING_NEWS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Error loading pending_breaking_news.json: {e}")
        return {}


def save_pending_breaking_news(data: Dict[str, Any]) -> None:
    """Save pending breaking news to JSON file."""
    try:
        with open(PENDING_BREAKING_NEWS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except IOError as e:
        logger.error(f"Error saving pending_breaking_news.json: {e}")
        raise


def add_pending_breaking_news_item(
    guild_id: str,
    article: Dict[str, Any],
    matched_topic: str,
    feed_name: str
) -> None:
    """
    Add a pending breaking news item for a guild.

    Args:
        guild_id: Guild ID as string
        article: Article data dictionary
        matched_topic: The topic keyword that triggered the match
        feed_name: Name of the RSS feed
    """
    data = load_pending_breaking_news()

    # Initialize guild if needed
    if guild_id not in data:
        data[guild_id] = {"pending_items": []}

    # Create pending item
    pending_item = {
        "article": article,
        "matched_topic": matched_topic,
        "collected_at": datetime.now(ZoneInfo("UTC")).isoformat(),
        "feed_name": feed_name,
        "retry_count": 0
    }

    # Add to pending items
    data[guild_id]["pending_items"].append(pending_item)

    save_pending_breaking_news(data)
    logger.info(f"Added breaking news item for guild {guild_id}: '{article.get('title', 'Unknown')}' (topic: {matched_topic})")


def get_pending_breaking_news_items(guild_id: str) -> List[Dict[str, Any]]:
    """
    Get all pending breaking news items for a guild.

    Args:
        guild_id: Guild ID as string

    Returns:
        List of pending item dictionaries
    """
    data = load_pending_breaking_news()
    return data.get(guild_id, {}).get("pending_items", [])


def clear_pending_breaking_news_item(guild_id: str, index: int) -> bool:
    """
    Remove a specific pending breaking news item by index.

    Args:
        guild_id: Guild ID as string
        index: Index of item to remove

    Returns:
        True if item was removed, False if not found
    """
    data = load_pending_breaking_news()

    if guild_id not in data or "pending_items" not in data[guild_id]:
        return False

    pending_items = data[guild_id]["pending_items"]

    if index < 0 or index >= len(pending_items):
        return False

    # Remove item
    removed_item = pending_items.pop(index)
    logger.info(f"Removed breaking news item from guild {guild_id}: '{removed_item.get('article', {}).get('title', 'Unknown')}'")

    # Clean up empty guild
    if not pending_items:
        del data[guild_id]

    save_pending_breaking_news(data)
    return True


def increment_retry_count(guild_id: str, index: int) -> int:
    """
    Increment retry count for a pending item.

    Args:
        guild_id: Guild ID as string
        index: Index of item to update

    Returns:
        New retry count, or -1 if not found
    """
    data = load_pending_breaking_news()

    if guild_id not in data or "pending_items" not in data[guild_id]:
        return -1

    pending_items = data[guild_id]["pending_items"]

    if index < 0 or index >= len(pending_items):
        return -1

    # Increment retry count
    pending_items[index]["retry_count"] = pending_items[index].get("retry_count", 0) + 1
    new_count = pending_items[index]["retry_count"]

    save_pending_breaking_news(data)
    return new_count


def clear_all_pending_for_guild(guild_id: str) -> int:
    """
    Clear all pending breaking news items for a guild.

    Args:
        guild_id: Guild ID as string

    Returns:
        Number of items cleared
    """
    data = load_pending_breaking_news()

    if guild_id not in data:
        return 0

    count = len(data[guild_id].get("pending_items", []))
    del data[guild_id]

    save_pending_breaking_news(data)
    logger.info(f"Cleared {count} pending breaking news items for guild {guild_id}")
    return count


def get_all_guilds_with_pending() -> List[str]:
    """
    Get list of all guild IDs that have pending breaking news items.

    Returns:
        List of guild ID strings
    """
    data = load_pending_breaking_news()
    return [
        guild_id
        for guild_id, guild_data in data.items()
        if guild_data.get("pending_items")
    ]
