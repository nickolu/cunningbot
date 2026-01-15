"""pending_news.py
Manages pending news articles separate from app_state to avoid bloating the main state file.

Articles are stored in pending_news.json with structure:
{
    "guild_id": {
        "channel_id": {
            "feed_name": [
                {article1},
                {article2},
                ...
            ]
        }
    }
}
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional
from pathlib import Path

# Path to pending news JSON file
PENDING_NEWS_FILE = Path(__file__).parent / "pending_news.json"


def _load_pending_news() -> Dict[str, Any]:
    """Load pending news from JSON file."""
    if not PENDING_NEWS_FILE.exists():
        return {}

    try:
        with open(PENDING_NEWS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"Error loading pending_news.json: {e}")
        return {}


def _save_pending_news(data: Dict[str, Any]) -> None:
    """Save pending news to JSON file."""
    try:
        with open(PENDING_NEWS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except IOError as e:
        print(f"Error saving pending_news.json: {e}")
        raise


def get_all_pending_news() -> Dict[str, Any]:
    """Get all pending news data."""
    return _load_pending_news()


def get_pending_articles_for_channel(guild_id: str, channel_id: int) -> Dict[str, List[Dict[str, Any]]]:
    """
    Get all pending articles for a specific channel, grouped by feed name.

    Args:
        guild_id: Guild ID as string
        channel_id: Channel ID as int

    Returns:
        Dictionary mapping feed_name -> list of article dicts
    """
    data = _load_pending_news()
    return data.get(str(guild_id), {}).get(str(channel_id), {})


def add_pending_articles(
    guild_id: str,
    channel_id: int,
    feed_name: str,
    articles: List[Dict[str, Any]]
) -> None:
    """
    Add pending articles for a specific feed.

    Args:
        guild_id: Guild ID as string
        channel_id: Channel ID as int
        feed_name: Name of the RSS feed
        articles: List of article dictionaries to add
    """
    if not articles:
        return

    data = _load_pending_news()

    # Initialize nested structure if needed
    guild_id_str = str(guild_id)
    channel_id_str = str(channel_id)

    if guild_id_str not in data:
        data[guild_id_str] = {}

    if channel_id_str not in data[guild_id_str]:
        data[guild_id_str][channel_id_str] = {}

    if feed_name not in data[guild_id_str][channel_id_str]:
        data[guild_id_str][channel_id_str][feed_name] = []

    # Append new articles
    data[guild_id_str][channel_id_str][feed_name].extend(articles)

    _save_pending_news(data)


def clear_pending_articles_for_channel(guild_id: str, channel_id: int) -> int:
    """
    Clear all pending articles for a specific channel.

    Args:
        guild_id: Guild ID as string
        channel_id: Channel ID as int

    Returns:
        Number of articles cleared
    """
    data = _load_pending_news()

    guild_id_str = str(guild_id)
    channel_id_str = str(channel_id)

    if guild_id_str not in data or channel_id_str not in data[guild_id_str]:
        return 0

    # Count articles before clearing
    total_cleared = sum(
        len(articles)
        for articles in data[guild_id_str][channel_id_str].values()
    )

    # Clear the channel
    del data[guild_id_str][channel_id_str]

    # Clean up empty guild
    if not data[guild_id_str]:
        del data[guild_id_str]

    _save_pending_news(data)

    return total_cleared


def clear_pending_articles_for_feed(
    guild_id: str,
    channel_id: int,
    feed_name: str
) -> int:
    """
    Clear pending articles for a specific feed.

    Args:
        guild_id: Guild ID as string
        channel_id: Channel ID as int
        feed_name: Name of the RSS feed

    Returns:
        Number of articles cleared
    """
    data = _load_pending_news()

    guild_id_str = str(guild_id)
    channel_id_str = str(channel_id)

    if (guild_id_str not in data or
        channel_id_str not in data[guild_id_str] or
        feed_name not in data[guild_id_str][channel_id_str]):
        return 0

    # Count articles before clearing
    total_cleared = len(data[guild_id_str][channel_id_str][feed_name])

    # Clear the feed
    del data[guild_id_str][channel_id_str][feed_name]

    # Clean up empty structures
    if not data[guild_id_str][channel_id_str]:
        del data[guild_id_str][channel_id_str]

    if not data[guild_id_str]:
        del data[guild_id_str]

    _save_pending_news(data)

    return total_cleared


def get_all_pending_by_channel() -> Dict[int, Dict[str, Any]]:
    """
    Get all pending articles grouped by channel_id (for summary generation).

    Returns:
        Dictionary mapping channel_id -> {
            "guild_id": str,
            "articles": [all articles],
            "feed_names": [list of feed names]
        }
    """
    data = _load_pending_news()
    result = {}

    for guild_id, guild_data in data.items():
        for channel_id_str, channel_data in guild_data.items():
            channel_id = int(channel_id_str)

            # Collect all articles and feed names for this channel
            all_articles = []
            feed_names = []

            for feed_name, articles in channel_data.items():
                all_articles.extend(articles)
                if articles:  # Only include feed names that have articles
                    feed_names.append(feed_name)

            if all_articles:
                result[channel_id] = {
                    "guild_id": guild_id,
                    "articles": all_articles,
                    "feed_names": feed_names
                }

    return result


def get_article_count() -> Dict[str, int]:
    """
    Get statistics about pending articles.

    Returns:
        Dictionary with total_articles, total_channels, total_guilds
    """
    data = _load_pending_news()

    total_articles = 0
    channels = set()
    guilds = set(data.keys())

    for guild_id, guild_data in data.items():
        for channel_id, channel_data in guild_data.items():
            channels.add(f"{guild_id}:{channel_id}")
            for articles in channel_data.values():
                total_articles += len(articles)

    return {
        "total_articles": total_articles,
        "total_channels": len(channels),
        "total_guilds": len(guilds)
    }
