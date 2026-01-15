"""
story_history.py
Manages story history for deduplication across daily summaries.
"""

import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Dict, List, Any
from bot.app.utils.logger import get_logger

logger = get_logger()

STORY_HISTORY_FILE = os.path.join(
    os.path.dirname(__file__), "story_history.json"
)

def load_story_history() -> Dict[str, Any]:
    """Load story history from JSON file."""
    if not os.path.exists(STORY_HISTORY_FILE):
        return {}

    try:
        with open(STORY_HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading story history: {e}")
        return {}

def save_story_history(data: Dict[str, Any]) -> None:
    """Save story history to JSON file."""
    try:
        with open(STORY_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Error saving story history: {e}")

def get_todays_story_history(guild_id: str, channel_id: int) -> List[Dict[str, Any]]:
    """
    Get today's story history for a specific channel.

    Returns list of stories posted today (Pacific timezone).
    """
    data = load_story_history()

    # Get today's date in Pacific timezone
    pacific_tz = ZoneInfo("America/Los_Angeles")
    today = datetime.now(pacific_tz).date().isoformat()

    # Navigate to channel data
    if guild_id not in data:
        return []

    if str(channel_id) not in data[guild_id]:
        return []

    channel_data = data[guild_id][str(channel_id)]

    # Check if date matches today
    if channel_data.get("date") != today:
        # Old data from previous day, treat as no history
        return []

    return channel_data.get("stories", [])

def add_stories_to_history(
    guild_id: str,
    channel_id: int,
    stories: List[Dict[str, Any]]
) -> None:
    """
    Add new stories to today's history for a channel.

    Args:
        guild_id: Guild ID string
        channel_id: Channel ID int
        stories: List of story dicts with title, summary, article_urls, posted_at, edition
    """
    data = load_story_history()

    # Get today's date in Pacific timezone
    pacific_tz = ZoneInfo("America/Los_Angeles")
    today = datetime.now(pacific_tz).date().isoformat()

    # Ensure guild exists
    if guild_id not in data:
        data[guild_id] = {}

    # Ensure channel exists
    if str(channel_id) not in data[guild_id]:
        data[guild_id][str(channel_id)] = {
            "date": today,
            "stories": []
        }

    channel_data = data[guild_id][str(channel_id)]

    # Check if date has changed (past midnight)
    if channel_data.get("date") != today:
        # New day, reset history
        logger.info(f"New day detected for channel {channel_id}, resetting history")
        channel_data["date"] = today
        channel_data["stories"] = []

    # Append new stories
    channel_data["stories"].extend(stories)

    logger.info(f"Added {len(stories)} stories to history for channel {channel_id}")

    save_story_history(data)

def cleanup_old_history() -> None:
    """
    Remove story history from previous days across all channels.
    Called at the start of each summary run to keep file size manageable.
    """
    data = load_story_history()

    # Get today's date in Pacific timezone
    pacific_tz = ZoneInfo("America/Los_Angeles")
    today = datetime.now(pacific_tz).date().isoformat()

    cleaned = False

    for guild_id in list(data.keys()):
        for channel_id in list(data[guild_id].keys()):
            channel_data = data[guild_id][channel_id]

            if channel_data.get("date") != today:
                # Old data, remove it
                del data[guild_id][channel_id]
                cleaned = True
                logger.info(f"Cleaned old history for guild {guild_id}, channel {channel_id}")

        # Remove empty guilds
        if not data[guild_id]:
            del data[guild_id]

    if cleaned:
        save_story_history(data)
        logger.info("Story history cleanup completed")
