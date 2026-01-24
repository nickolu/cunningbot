"""Redis storage layer for RSS feeds.

This module provides atomic operations for RSS feed management,
eliminating race conditions present in the JSON file-based approach.
"""

import json
import logging
from typing import Any, Dict, List, Optional
from datetime import datetime

from bot.app.redis.client import get_redis_client
from bot.app.redis.serialization import guild_id_to_str

logger = logging.getLogger("RSSRedisStore")


class RSSRedisStore:
    """Redis storage operations for RSS feeds."""

    def __init__(self):
        self.redis_client = get_redis_client()
        self.redis = self.redis_client.redis

    # --- Feed Configuration ---

    async def get_feeds(self, guild_id: str) -> Dict[str, Dict[str, Any]]:
        """Get all RSS feed configurations for a guild.

        Args:
            guild_id: Guild ID as string

        Returns:
            Dictionary of feed_name -> feed_config
        """
        key = f"rss:{guild_id}:feeds"
        feeds_hash = await self.redis.hgetall(key)

        result = {}
        for feed_name, feed_json in feeds_hash.items():
            try:
                result[feed_name] = json.loads(feed_json)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to decode feed {feed_name}: {e}")

        return result

    async def get_feed(self, guild_id: str, feed_name: str) -> Optional[Dict[str, Any]]:
        """Get a specific RSS feed configuration.

        Args:
            guild_id: Guild ID as string
            feed_name: Feed name

        Returns:
            Feed configuration dictionary or None if not found
        """
        key = f"rss:{guild_id}:feeds"
        feed_json = await self.redis.hget(key, feed_name)

        if not feed_json:
            return None

        try:
            return json.loads(feed_json)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode feed {feed_name}: {e}")
            return None

    async def save_feed(
        self, guild_id: str, feed_name: str, feed_config: Dict[str, Any]
    ) -> None:
        """Save (create or update) an RSS feed configuration.

        Args:
            guild_id: Guild ID as string
            feed_name: Feed name
            feed_config: Feed configuration (url, channel_id, enabled, etc.)
        """
        key = f"rss:{guild_id}:feeds"
        await self.redis.hset(key, feed_name, json.dumps(feed_config))
        logger.info(f"Saved feed '{feed_name}' for guild {guild_id}")

    async def delete_feed(self, guild_id: str, feed_name: str) -> bool:
        """Delete an RSS feed configuration.

        Args:
            guild_id: Guild ID as string
            feed_name: Feed name

        Returns:
            True if feed was deleted, False if it didn't exist
        """
        key = f"rss:{guild_id}:feeds"
        deleted = await self.redis.hdel(key, feed_name)

        # Also clean up associated data
        if deleted > 0:
            seen_key = f"rss:{guild_id}:feed:{feed_name}:seen"
            await self.redis.delete(seen_key)
            logger.info(f"Deleted feed '{feed_name}' for guild {guild_id}")

        return deleted > 0

    # --- Seen Items (Redis Set for O(1) lookups) ---

    async def is_seen(self, guild_id: str, feed_name: str, item_id: str) -> bool:
        """Check if an RSS item has been seen before.

        Args:
            guild_id: Guild ID as string
            feed_name: Feed name
            item_id: Unique item identifier

        Returns:
            True if item was seen before, False otherwise
        """
        key = f"rss:{guild_id}:feed:{feed_name}:seen"
        return await self.redis.sismember(key, item_id)

    async def mark_seen(
        self, guild_id: str, feed_name: str, item_ids: List[str]
    ) -> int:
        """Mark RSS items as seen.

        Args:
            guild_id: Guild ID as string
            feed_name: Feed name
            item_ids: List of item identifiers to mark as seen

        Returns:
            Number of new items added (excludes duplicates)
        """
        if not item_ids:
            return 0

        key = f"rss:{guild_id}:feed:{feed_name}:seen"
        added = await self.redis.sadd(key, *item_ids)
        return added

    async def get_seen_count(self, guild_id: str, feed_name: str) -> int:
        """Get count of seen items for a feed.

        Args:
            guild_id: Guild ID as string
            feed_name: Feed name

        Returns:
            Number of seen items
        """
        key = f"rss:{guild_id}:feed:{feed_name}:seen"
        return await self.redis.scard(key)

    async def trim_seen(
        self, guild_id: str, feed_name: str, keep_count: int = 500
    ) -> int:
        """Trim seen items to keep only recent ones.

        Note: Sets don't have natural ordering, so this converts to a list,
        keeps the first N items, and recreates the set. This is a best-effort
        trimming operation.

        Args:
            guild_id: Guild ID as string
            feed_name: Feed name
            keep_count: Number of items to keep

        Returns:
            Number of items removed
        """
        key = f"rss:{guild_id}:feed:{feed_name}:seen"

        # Get current count
        current_count = await self.redis.scard(key)

        if current_count <= keep_count:
            return 0

        # Get all members, keep first keep_count, delete the rest
        # Note: This is not perfect as Sets are unordered, but prevents unbounded growth
        all_items = await self.redis.smembers(key)
        items_to_remove = list(all_items)[keep_count:]

        if items_to_remove:
            removed = await self.redis.srem(key, *items_to_remove)
            logger.info(f"Trimmed {removed} seen items for feed '{feed_name}'")
            return removed

        return 0

    async def clear_seen(self, guild_id: str, feed_name: str) -> int:
        """Clear all seen items for a feed (used for reset).

        Args:
            guild_id: Guild ID as string
            feed_name: Feed name

        Returns:
            Number of items cleared
        """
        key = f"rss:{guild_id}:feed:{feed_name}:seen"
        count = await self.redis.scard(key)

        if count > 0:
            await self.redis.delete(key)
            logger.info(f"Cleared {count} seen items for feed '{feed_name}'")
            return count

        return 0

    # --- Pending Articles (Redis List for queue-like behavior) ---

    async def add_pending(
        self,
        guild_id: str,
        channel_id: int,
        feed_name: str,
        articles: List[Dict[str, Any]]
    ) -> int:
        """Add articles to pending queue for summarization.

        Args:
            guild_id: Guild ID as string
            channel_id: Channel ID
            feed_name: Feed name
            articles: List of article dictionaries

        Returns:
            New length of pending queue
        """
        if not articles:
            return 0

        key = f"rss:{guild_id}:pending:{channel_id}:{feed_name}"

        # Convert articles to JSON strings
        article_jsons = [json.dumps(article) for article in articles]

        # Push to list (RPUSH appends to end)
        length = await self.redis.rpush(key, *article_jsons)
        logger.info(f"Added {len(articles)} pending articles to {feed_name} for channel {channel_id}")

        return length

    async def get_pending(
        self, guild_id: str, channel_id: int
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Get all pending articles for a channel, grouped by feed.

        Args:
            guild_id: Guild ID as string
            channel_id: Channel ID

        Returns:
            Dictionary mapping feed_name -> list of articles
        """
        pattern = f"rss:{guild_id}:pending:{channel_id}:*"
        keys = await self.redis.keys(pattern)

        result = {}
        for key in keys:
            # Extract feed_name from key
            feed_name = key.split(":")[-1]

            # Get all articles from list
            article_jsons = await self.redis.lrange(key, 0, -1)

            articles = []
            for article_json in article_jsons:
                try:
                    articles.append(json.loads(article_json))
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to decode pending article: {e}")

            if articles:
                result[feed_name] = articles

        return result

    async def clear_pending(self, guild_id: str, channel_id: int) -> int:
        """Clear all pending articles for a channel.

        Args:
            guild_id: Guild ID as string
            channel_id: Channel ID

        Returns:
            Number of article lists cleared
        """
        pattern = f"rss:{guild_id}:pending:{channel_id}:*"
        keys = await self.redis.keys(pattern)

        if keys:
            deleted = await self.redis.delete(*keys)
            logger.info(f"Cleared {deleted} pending article lists for channel {channel_id}")
            return deleted

        return 0

    async def clear_pending_for_feed(
        self, guild_id: str, channel_id: int, feed_name: str
    ) -> int:
        """Clear pending articles for a specific feed.

        Args:
            guild_id: Guild ID as string
            channel_id: Channel ID
            feed_name: Feed name

        Returns:
            Number of articles cleared (length of list before deletion)
        """
        key = f"rss:{guild_id}:pending:{channel_id}:{feed_name}"

        # Get count before deleting
        count = await self.redis.llen(key)

        if count > 0:
            await self.redis.delete(key)
            logger.info(f"Cleared {count} pending articles for feed '{feed_name}' in channel {channel_id}")
            return count

        return 0

    # --- Summary Tracking ---

    async def get_last_summary(
        self, guild_id: str, channel_id: int, edition: str
    ) -> Optional[str]:
        """Get the last summary time for a channel and edition.

        Args:
            guild_id: Guild ID as string
            channel_id: Channel ID
            edition: Edition name ("Morning", "Afternoon", "Evening")

        Returns:
            ISO timestamp string or None
        """
        key = f"rss:{guild_id}:summary:{channel_id}:last"
        return await self.redis.hget(key, edition)

    async def set_last_summary(
        self, guild_id: str, channel_id: int, edition: str, timestamp: str
    ) -> None:
        """Record the last summary time for a channel and edition.

        Args:
            guild_id: Guild ID as string
            channel_id: Channel ID
            edition: Edition name ("Morning", "Afternoon", "Evening")
            timestamp: ISO timestamp string
        """
        key = f"rss:{guild_id}:summary:{channel_id}:last"
        await self.redis.hset(key, edition, timestamp)
        logger.info(f"Set last summary for channel {channel_id} edition {edition}: {timestamp}")

    async def get_all_last_summaries(
        self, guild_id: str, channel_id: int
    ) -> Dict[str, str]:
        """Get all last summary times for a channel.

        Args:
            guild_id: Guild ID as string
            channel_id: Channel ID

        Returns:
            Dictionary mapping edition -> timestamp
        """
        key = f"rss:{guild_id}:summary:{channel_id}:last"
        return await self.redis.hgetall(key)

    # --- Story History (Sorted Set for time-based queries) ---

    async def add_stories_to_history(
        self,
        guild_id: str,
        channel_id: int,
        stories: list[dict[str, Any]]
    ) -> int:
        """Add stories to history for deduplication (atomic operation).

        Uses Redis Sorted Set with timestamp as score for efficient time-based queries.

        Args:
            guild_id: Guild ID as string
            channel_id: Channel ID
            stories: List of story dictionaries with title, summary, article_urls, etc.

        Returns:
            Number of stories added
        """
        if not stories:
            return 0

        key = f"rss:{guild_id}:story_history:{channel_id}"

        # Build mapping of JSON-serialized story -> timestamp score
        # Use posted_at if available, otherwise current time
        from datetime import datetime
        now = datetime.now().timestamp()

        story_mapping = {}
        for story in stories:
            # Parse posted_at timestamp if present
            posted_at_str = story.get("posted_at")
            if posted_at_str:
                try:
                    posted_at = datetime.fromisoformat(posted_at_str)
                    score = posted_at.timestamp()
                except (ValueError, TypeError):
                    score = now
            else:
                score = now

            story_json = json.dumps(story, ensure_ascii=False)
            story_mapping[story_json] = score

        # Atomic add to sorted set (ZADD)
        added = await self.redis.zadd(key, story_mapping)
        logger.info(f"Added {len(stories)} stories to history for channel {channel_id}")

        return added

    async def get_stories_within_window(
        self,
        guild_id: str,
        channel_id: int,
        window_hours: int
    ) -> list[dict[str, Any]]:
        """Get story history within the specified time window.

        Args:
            guild_id: Guild ID as string
            channel_id: Channel ID
            window_hours: How many hours back to look

        Returns:
            List of story dictionaries within the time window
        """
        from datetime import datetime, timedelta

        key = f"rss:{guild_id}:story_history:{channel_id}"

        # Calculate cutoff timestamp
        now = datetime.now()
        cutoff = now - timedelta(hours=window_hours)
        cutoff_timestamp = cutoff.timestamp()

        # Get stories with score (timestamp) >= cutoff_timestamp
        story_jsons = await self.redis.zrangebyscore(
            key,
            min=cutoff_timestamp,
            max="+inf"
        )

        stories = []
        for story_json in story_jsons:
            try:
                stories.append(json.loads(story_json))
            except json.JSONDecodeError as e:
                logger.error(f"Failed to decode story from history: {e}")

        logger.info(f"Retrieved {len(stories)} stories within {window_hours}h window for channel {channel_id}")
        return stories

    async def cleanup_old_story_history(
        self,
        guild_id: str,
        channel_id: int,
        max_age_hours: int = 168  # 7 days default
    ) -> int:
        """Remove stories older than max_age_hours from history.

        Args:
            guild_id: Guild ID as string
            channel_id: Channel ID
            max_age_hours: Maximum age in hours (default 168 = 7 days)

        Returns:
            Number of stories removed
        """
        from datetime import datetime, timedelta

        key = f"rss:{guild_id}:story_history:{channel_id}"

        # Calculate cutoff timestamp
        now = datetime.now()
        cutoff = now - timedelta(hours=max_age_hours)
        cutoff_timestamp = cutoff.timestamp()

        # Remove all stories with score (timestamp) < cutoff_timestamp
        removed = await self.redis.zremrangebyscore(key, "-inf", cutoff_timestamp)

        if removed > 0:
            logger.info(f"Removed {removed} old stories from channel {channel_id} history")

        return removed

    async def get_story_history_count(
        self,
        guild_id: str,
        channel_id: int
    ) -> int:
        """Get count of stories in history for a channel.

        Args:
            guild_id: Guild ID as string
            channel_id: Channel ID

        Returns:
            Number of stories in history
        """
        key = f"rss:{guild_id}:story_history:{channel_id}"
        return await self.redis.zcard(key)

    # --- Breaking News Pending Items ---

    async def add_pending_breaking_news(
        self,
        guild_id: str,
        article: Dict[str, Any],
        matched_topic: str,
        feed_name: str
    ) -> None:
        """Add a pending breaking news item for a guild.

        Args:
            guild_id: Guild ID as string
            article: Article data dictionary
            matched_topic: The topic keyword that triggered the match
            feed_name: Name of the RSS feed
        """
        from datetime import datetime
        from zoneinfo import ZoneInfo

        key = f"rss:{guild_id}:breaking_news:pending"

        pending_item = {
            "article": article,
            "matched_topic": matched_topic,
            "collected_at": datetime.now(ZoneInfo("UTC")).isoformat(),
            "feed_name": feed_name,
            "retry_count": 0
        }

        # Add to end of list (RPUSH)
        await self.redis.rpush(key, json.dumps(pending_item))
        logger.info(f"Added breaking news item for guild {guild_id}: '{article.get('title', 'Unknown')}' (topic: {matched_topic})")

    async def get_pending_breaking_news(self, guild_id: str) -> List[Dict[str, Any]]:
        """Get all pending breaking news items for a guild.

        Args:
            guild_id: Guild ID as string

        Returns:
            List of pending item dictionaries
        """
        key = f"rss:{guild_id}:breaking_news:pending"

        # Get all items from list
        items_json = await self.redis.lrange(key, 0, -1)

        items = []
        for item_json in items_json:
            try:
                items.append(json.loads(item_json))
            except json.JSONDecodeError as e:
                logger.error(f"Failed to decode pending breaking news item: {e}")

        return items

    async def clear_pending_breaking_news_item(self, guild_id: str, index: int) -> bool:
        """Remove a specific pending breaking news item by index.

        Args:
            guild_id: Guild ID as string
            index: Index of item to remove

        Returns:
            True if item was removed, False if not found
        """
        key = f"rss:{guild_id}:breaking_news:pending"

        # Get the item first for logging
        items_json = await self.redis.lrange(key, index, index)
        if not items_json:
            return False

        try:
            removed_item = json.loads(items_json[0])
            logger.info(f"Removing breaking news item from guild {guild_id}: '{removed_item.get('article', {}).get('title', 'Unknown')}'")
        except json.JSONDecodeError:
            pass

        # Mark item for deletion by setting it to a special value
        placeholder = json.dumps({"__deleted__": True})
        await self.redis.lset(key, index, placeholder)

        # Remove all placeholders
        await self.redis.lrem(key, 0, placeholder)

        return True

    async def increment_breaking_news_retry_count(self, guild_id: str, index: int) -> int:
        """Increment retry count for a pending breaking news item.

        Args:
            guild_id: Guild ID as string
            index: Index of item to update

        Returns:
            New retry count, or -1 if not found
        """
        key = f"rss:{guild_id}:breaking_news:pending"

        # Get the item
        items_json = await self.redis.lrange(key, index, index)
        if not items_json:
            return -1

        try:
            item = json.loads(items_json[0])
            item["retry_count"] = item.get("retry_count", 0) + 1
            new_count = item["retry_count"]

            # Update the item
            await self.redis.lset(key, index, json.dumps(item))

            return new_count
        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode pending breaking news item: {e}")
            return -1

    async def get_guilds_with_pending_breaking_news(self) -> List[str]:
        """Get list of all guild IDs that have pending breaking news items.

        Returns:
            List of guild ID strings
        """
        # Scan for all breaking news pending keys
        pattern = "rss:*:breaking_news:pending"
        guilds = []

        cursor = 0
        while True:
            cursor, keys = await self.redis.scan(cursor, match=pattern, count=100)

            for key in keys:
                # Extract guild_id from key pattern "rss:{guild_id}:breaking_news:pending"
                parts = key.split(":")
                if len(parts) >= 2:
                    guild_id = parts[1]

                    # Check if list is non-empty
                    list_len = await self.redis.llen(key)
                    if list_len > 0:
                        guilds.append(guild_id)

            if cursor == 0:
                break

        return guilds
