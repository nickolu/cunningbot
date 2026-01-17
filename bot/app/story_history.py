"""
story_history.py
Manages story history for deduplication across daily summaries.
"""

import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Dict, List, Any
from bot.app.utils.logger import get_logger

logger = get_logger()

# Deduplication window configuration
DEFAULT_DEDUP_WINDOW_HOURS = 24
MIN_DEDUP_WINDOW_HOURS = 6
MAX_DEDUP_WINDOW_HOURS = 168

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

def get_channel_dedup_window(guild_id: int, channel_id: int) -> int:
    """Get deduplication window (in hours) for a specific channel."""
    from bot.app.app_state import get_state_value

    guild_id_str = str(guild_id)
    all_windows = get_state_value("channel_dedup_windows", guild_id_str) or {}
    return all_windows.get(str(channel_id), DEFAULT_DEDUP_WINDOW_HOURS)

def get_stories_within_window(
    guild_id: str,
    channel_id: int,
    window_hours: int = DEFAULT_DEDUP_WINDOW_HOURS
) -> List[Dict[str, Any]]:
    """Get story history within the specified time window."""
    data = load_story_history()

    if guild_id not in data or str(channel_id) not in data[guild_id]:
        return []

    channel_data = data[guild_id][str(channel_id)]
    all_stories = channel_data.get("stories", [])

    # Calculate cutoff time
    pacific_tz = ZoneInfo("America/Los_Angeles")
    now = datetime.now(pacific_tz)
    cutoff = now - timedelta(hours=window_hours)

    # Filter stories within window
    stories_in_window = []
    for story in all_stories:
        try:
            posted_at = datetime.fromisoformat(story["posted_at"])
            if posted_at > cutoff:
                stories_in_window.append(story)
        except (KeyError, ValueError) as e:
            logger.warning(f"Invalid posted_at in story: {e}")
            continue

    logger.info(f"Found {len(stories_in_window)}/{len(all_stories)} stories within {window_hours}h window")
    return stories_in_window

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
    """Add new stories to history for a channel."""
    data = load_story_history()

    pacific_tz = ZoneInfo("America/Los_Angeles")
    today = datetime.now(pacific_tz).date().isoformat()

    # Ensure guild and channel exist
    if guild_id not in data:
        data[guild_id] = {}
    if str(channel_id) not in data[guild_id]:
        data[guild_id][str(channel_id)] = {
            "date": today,  # Keep for backward compatibility
            "stories": []
        }

    channel_data = data[guild_id][str(channel_id)]

    # Simply append (no date reset)
    channel_data["stories"].extend(stories)
    channel_data["date"] = today  # Update for compatibility

    logger.info(f"Added {len(stories)} stories to history")
    save_story_history(data)

def cleanup_old_history(max_age_hours: int = MAX_DEDUP_WINDOW_HOURS) -> None:
    """Remove stories older than max_age_hours across all channels."""
    data = load_story_history()

    pacific_tz = ZoneInfo("America/Los_Angeles")
    now = datetime.now(pacific_tz)
    cutoff = now - timedelta(hours=max_age_hours)

    cleaned = False

    for guild_id in list(data.keys()):
        for channel_id in list(data[guild_id].keys()):
            channel_data = data[guild_id][channel_id]
            all_stories = channel_data.get("stories", [])

            # Filter stories within cutoff
            stories_to_keep = []
            for story in all_stories:
                try:
                    posted_at = datetime.fromisoformat(story["posted_at"])
                    if posted_at > cutoff:
                        stories_to_keep.append(story)
                    else:
                        cleaned = True
                except (KeyError, ValueError):
                    cleaned = True
                    continue

            # Update or remove channel
            if stories_to_keep:
                channel_data["stories"] = stories_to_keep
            else:
                del data[guild_id][channel_id]
                cleaned = True

        # Remove empty guilds
        if not data[guild_id]:
            del data[guild_id]

    if cleaned:
        save_story_history(data)
        logger.info(f"Story history cleanup completed (removed stories older than {max_age_hours}h)")
