"""rss_feed_poster.py
Script intended to be invoked every 10 minutes (e.g. via cron or Docker loop).
It reads the registered *rss_feeds* in each guild's app state and, if any feed is
enabled, fetches new items and stores them in pending_articles for later summarization.

Usage (inside Docker container hosting the bot):
    python -m bot.app.tasks.rss_feed_poster

You can wire this up in docker-compose.yml:
    command: bash -c "while true; do python -m bot.app.tasks.rss_feed_poster; sleep 600; done"

Articles are collected here and posted as summaries by rss_summary_poster.py.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import logging
from typing import Any, Dict, List
import hashlib
from html.parser import HTMLParser

import discord
import feedparser
from bot.app.app_state import get_all_guild_states, set_state_value
from bot.app.pending_news import add_pending_articles
from bot.app.redis.rss_store import RSSRedisStore
from bot.app.redis.locks import redis_lock
from bot.app.redis.client import get_redis_client, initialize_redis, close_redis
from bot.app.redis.exceptions import LockAcquisitionError

logger = logging.getLogger("RSSFeedPoster")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Feature flag for Redis migration
USE_REDIS = True


def get_item_id(entry) -> str:
    """Get unique identifier for RSS item."""
    # Try standard fields first
    if hasattr(entry, 'id') and entry.id:
        return str(entry.id)
    if hasattr(entry, 'guid') and entry.guid:
        return str(entry.guid)

    # Fallback: hash of title + link + published
    content = f"{entry.get('title', '')}{entry.get('link', '')}{entry.get('published', '')}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def clean_html(html_text: str, max_length: int = 500) -> str:
    """Strip HTML tags and limit length for embed."""
    if not html_text:
        return ""

    import re

    # Remove common "related articles" sections before processing
    # These patterns often appear at the end of articles
    patterns_to_remove = [
        r'<h[23]>Related.*?</h[23]>.*',  # Related heading and everything after
        r'<div[^>]*class="[^"]*related[^"]*"[^>]*>.*?</div>',  # Related divs
        r'<aside[^>]*>.*?</aside>',  # Aside sections (often related content)
        r'Read more:.*',  # "Read more" sections
        r'Related:.*',  # "Related:" sections
        r'See also:.*',  # "See also" sections
    ]

    cleaned = html_text
    for pattern in patterns_to_remove:
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE | re.DOTALL)

    class HTMLStripper(HTMLParser):
        def __init__(self):
            super().__init__()
            self.text = []

        def handle_data(self, data):
            self.text.append(data)

        def get_text(self):
            return ''.join(self.text)

    stripper = HTMLStripper()
    try:
        stripper.feed(cleaned)
        text = stripper.get_text().strip()
    except Exception:
        # If HTML parsing fails, just strip basic tags
        text = re.sub(r'<[^>]+>', '', cleaned).strip()

    # Remove extra whitespace and newlines
    text = ' '.join(text.split())

    # Truncate with ellipsis
    if len(text) > max_length:
        return text[:max_length-3] + "..."
    return text


def get_description(entry) -> str:
    """Extract description, preferring full content over summary."""
    if hasattr(entry, 'content') and entry.content:
        return entry.content[0].value
    if hasattr(entry, 'summary'):
        return entry.summary
    if hasattr(entry, 'description'):
        return entry.description
    return ""


def get_source(entry, feed) -> str:
    """Get source/publication name."""
    # Try source field in entry
    if hasattr(entry, 'source') and hasattr(entry.source, 'title'):
        return entry.source.title
    # Try author
    if hasattr(entry, 'author') and entry.author:
        return entry.author
    # Fallback to feed title
    if hasattr(feed, 'feed') and hasattr(feed.feed, 'title'):
        return feed.feed.title
    return 'Unknown Source'


def get_image_url(entry) -> str:
    """Extract thumbnail/image URL from RSS item."""
    # Try media:content
    if hasattr(entry, 'media_content') and entry.media_content:
        url = entry.media_content[0].get('url', '')
        if url:
            return url

    # Try enclosures
    if hasattr(entry, 'enclosures') and entry.enclosures:
        for enc in entry.enclosures:
            if enc.get('type', '').startswith('image/'):
                href = enc.get('href', '')
                if href:
                    return href

    # Try media:thumbnail
    if hasattr(entry, 'media_thumbnail') and entry.media_thumbnail:
        return entry.media_thumbnail[0].get('url', '')

    return ""


def extract_article_data(entry, feed, feed_name: str) -> Dict[str, Any]:
    """Extract article data from RSS entry for storage."""
    title = entry.get('title', 'No title')[:256]
    link = entry.get('link', '')
    description = clean_html(get_description(entry))
    source = get_source(entry, feed)
    image_url = get_image_url(entry)

    # Parse published date
    published = entry.get('published', '')
    published_iso = None
    if published:
        try:
            from email.utils import parsedate_to_datetime
            published_dt = parsedate_to_datetime(published)
            published_iso = published_dt.isoformat()
        except Exception:
            pass

    return {
        'id': get_item_id(entry),
        'title': title,
        'link': link,
        'description': description if description else "No description available",
        'source': source,
        'published': published_iso,
        'image_url': image_url,
        'collected_at': dt.datetime.utcnow().isoformat(),
        'feed_name': feed_name
    }


async def post_direct_items(
    to_post: Dict[int, List[Dict[str, Any]]],
    token: str,
    all_guild_states: Dict[str, Any]
) -> None:
    """Post items directly to Discord channels."""
    logger.info(f"Posting {sum(len(items) for items in to_post.values())} direct items to {len(to_post)} channels")

    # Create Discord client
    intents = discord.Intents.none()
    client = discord.Client(intents=intents)

    @client.event  # type: ignore[misc]
    async def on_ready():
        logger.info("Discord client logged in as %s", client.user)
        successfully_posted_items = []

        for channel_id, items in to_post.items():
            try:
                channel = client.get_channel(channel_id)
                if channel is None:
                    channel = await client.fetch_channel(channel_id)  # type: ignore[attr-defined]
                if not isinstance(channel, (discord.TextChannel, discord.Thread)):
                    logger.warning("Channel ID %s is not a text channel", channel_id)
                    continue

                for item in items:
                    try:
                        # Format and post the item
                        embed = format_item_embed(item['entry'], item['feed'])
                        await channel.send(embed=embed)
                        logger.info("Posted item from feed '%s' to channel %s", item['feed_name'], channel.id)

                        # Track successful post
                        successfully_posted_items.append(item)

                        # Add small delay between posts
                        await asyncio.sleep(0.5)

                    except discord.HTTPException as exc:
                        logger.error("Failed to post item from feed '%s': %s", item['feed_name'], exc)
                    except Exception as exc:
                        logger.error("Unexpected error posting item from feed '%s': %s", item['feed_name'], exc)

            except discord.Forbidden:
                logger.error("Missing permissions to post in channel %s", channel_id)
            except discord.HTTPException as exc:
                logger.error("HTTP error accessing channel %s: %s", channel_id, exc)
            except Exception as exc:
                logger.error("Unexpected error accessing channel %s: %s", channel_id, exc)

        logger.info(f"Successfully posted {len(successfully_posted_items)} direct items")
        await client.close()

    # Run the client
    await client.start(token)


def format_item_embed(entry, feed) -> discord.Embed:
    """Format an RSS entry as a Discord embed for direct posting."""
    title = entry.get('title', 'No title')[:256]
    link = entry.get('link', '')
    description = clean_html(get_description(entry))
    source = get_source(entry, feed)
    image_url = get_image_url(entry)

    # Parse published date
    published = entry.get('published', '')
    timestamp = None
    if published:
        try:
            from email.utils import parsedate_to_datetime
            timestamp = parsedate_to_datetime(published)
        except Exception:
            pass

    embed = discord.Embed(
        title=title,
        url=link,
        description=description if description else "No description available",
        color=0x00a8ff,
        timestamp=timestamp
    )

    # Add image if available
    if image_url:
        try:
            embed.set_image(url=image_url)
        except Exception:
            # If image URL is invalid, just skip it
            pass

    # Add source attribution in footer
    embed.set_footer(text=f"Source: {source}")

    return embed


async def collect_rss_updates() -> None:
    """Main entry point called once per invocation."""
    if USE_REDIS:
        await _collect_with_redis()
    else:
        await _collect_with_json()


async def _collect_with_redis() -> None:
    """Collect RSS updates using Redis (atomic, with distributed locks)."""
    logger.info("=== RSS Feed Collector Starting (Redis mode) ===")

    # Initialize Redis
    await initialize_redis()
    store = RSSRedisStore()
    redis_client = get_redis_client()

    logger.info("Loading guild states...")
    all_guild_states = get_all_guild_states()
    logger.info("Loaded %d guild states", len(all_guild_states))

    if not all_guild_states:
        logger.info("App state empty – nothing to collect.")
        await close_redis()
        return

    # Track collected articles for logging
    total_collected = 0
    total_posted_direct = 0

    # Build a mapping channel_id -> list[dict] of items to post directly
    to_post_direct: Dict[int, List[Dict[str, Any]]] = {}

    for guild_id_str, guild_state in all_guild_states.items():
        logger.info("Checking guild %s", guild_id_str)

        if guild_id_str == "global":
            logger.info("Skipping global state")
            continue  # skip global state

        if not isinstance(guild_state, dict):
            logger.warning("Guild state for %s is not a dict (got %s) – skipping", guild_id_str, type(guild_state))
            continue

        logger.info("Guild %s state keys: %s", guild_id_str, list(guild_state.keys()))

        feeds = guild_state.get("rss_feeds", {})
        logger.info("Guild %s has %d feeds", guild_id_str, len(feeds) if feeds else 0)

        if not feeds:
            logger.info("No feeds for guild %s, skipping", guild_id_str)
            continue

        # Get feeds from Redis
        feeds = await store.get_feeds(guild_id_str)
        logger.info("Guild %s has %d feeds in Redis", guild_id_str, len(feeds) if feeds else 0)

        if not feeds:
            logger.info("No feeds for guild %s, skipping", guild_id_str)
            continue

        for feed_name, feed_info in feeds.items():
            # Each feed gets its own lock to allow parallel processing
            lock_resource = f"rss:{guild_id_str}:feed:{feed_name}"

            try:
                async with redis_lock(redis_client, lock_resource, timeout=300):
                    logger.info("Acquired lock for feed '%s' in guild %s", feed_name, guild_id_str)

                    if not feed_info.get("enabled", True):
                        logger.info("Feed '%s' in guild %s is disabled, skipping", feed_name, guild_id_str)
                        continue

                    feed_url = feed_info.get("url")
                    channel_id = feed_info.get("channel_id")
                    max_seen_items = feed_info.get("max_seen_items", 500)
                    post_mode = feed_info.get("post_mode", "summary")

                    # Get seen count from Redis
                    seen_count = await store.get_seen_count(guild_id_str, feed_name)

                    logger.info("Feed '%s': url=%s, channel_id=%s, seen=%d, mode=%s",
                               feed_name, feed_url, channel_id, seen_count, post_mode)

                    if not feed_url or not channel_id:
                        logger.warning("Feed '%s' has missing url or channel_id, skipping", feed_name)
                        continue

                    # Add small delay between feed fetches to be polite
                    await asyncio.sleep(1)

                    logger.info("Fetching feed '%s' from %s", feed_name, feed_url)

                    # Fetch and parse the feed
                    feed = feedparser.parse(feed_url)

                    logger.info("Feed '%s' fetched: bozo=%s, entries=%d",
                               feed_name, feed.bozo, len(feed.entries))

                    if feed.bozo:
                        logger.warning("Feed parse warning for '%s': %s", feed_name, feed.get('bozo_exception', 'Unknown error'))

                    if not feed.entries:
                        logger.warning("No entries found in feed '%s'", feed_name)
                        continue

                    # Filter to new items using Redis Set (O(1) lookups!)
                    new_entries = []
                    for entry in feed.entries:
                        item_id = get_item_id(entry)
                        # O(1) check with Redis Set
                        if not await store.is_seen(guild_id_str, feed_name, item_id):
                            new_entries.append(entry)

                    # If this is the first run (empty seen set), only process the 5 most recent items
                    if seen_count == 0 and len(new_entries) > 5:
                        logger.info("First run for feed '%s', limiting to 5 most recent items", feed_name)
                        new_entries = new_entries[:5]

                    if new_entries:
                        logger.info("Found %d new items from feed '%s' (mode: %s)", len(new_entries), feed_name, post_mode)

                        # Check for breaking news matches (only if feed is enabled)
                        if feed_info.get('enabled', True):
                            from bot.domain.news.breaking_news_service import matches_breaking_news_topics
                            from bot.app.pending_breaking_news import add_pending_breaking_news_item
                            from bot.app.app_state import get_state_value

                            breaking_config = get_state_value("breaking_news_config", guild_id_str)
                            if breaking_config and breaking_config.get("enabled"):
                                topics = breaking_config.get("topics", [])
                                if topics:
                                    for entry in new_entries:
                                        matched_topic = matches_breaking_news_topics(entry, topics)
                                        if matched_topic:
                                            # Extract article data
                                            article_data = extract_article_data(entry, feed, feed_name)
                                            add_pending_breaking_news_item(
                                                guild_id_str,
                                                article_data,
                                                matched_topic,
                                                feed_name
                                            )
                                            logger.info(f"Breaking news match: '{matched_topic}' in {feed_name}")

                        # Route based on post_mode
                        if post_mode == "direct":
                            # Direct posting: queue for immediate Discord post
                            for entry in new_entries:
                                to_post_direct.setdefault(channel_id, []).append({
                                    'entry': entry,
                                    'feed': feed,
                                    'feed_name': feed_name,
                                    'guild_id': guild_id_str,
                                    'id': get_item_id(entry)
                                })
                            total_posted_direct += len(new_entries)
                            logger.info("Queued %d items for direct posting from feed '%s'", len(new_entries), feed_name)

                        else:  # post_mode == "summary" (or default)
                            # Summary mode: collect for later summarization in Redis
                            article_data_list = []
                            for entry in new_entries:
                                article_data = extract_article_data(entry, feed, feed_name)
                                article_data_list.append(article_data)

                            # Add to Redis pending queue (atomic)
                            await store.add_pending(guild_id_str, channel_id, feed_name, article_data_list)
                            total_collected += len(article_data_list)
                            logger.info("Collected %d items for summary from feed '%s'", len(article_data_list), feed_name)

                        # Mark items as seen in Redis Set (atomic)
                        item_ids = [get_item_id(entry) for entry in new_entries]
                        await store.mark_seen(guild_id_str, feed_name, item_ids)

                        # Trim seen items to prevent unbounded growth
                        await store.trim_seen(guild_id_str, feed_name, max_seen_items)

                        # Update last_check in feed config
                        feed_info["last_check"] = dt.datetime.utcnow().isoformat()
                        await store.save_feed(guild_id_str, feed_name, feed_info)

            except LockAcquisitionError:
                logger.info("Could not acquire lock for feed '%s' (another container is processing it)", feed_name)
                continue
            except Exception as e:
                logger.error("Error processing feed '%s' in guild %s: %s", feed_name, guild_id_str, str(e))
                continue

    logger.info(f"Collection summary: {total_collected} articles for summary, {total_posted_direct} articles for direct posting")

    # Post direct items if any
    if to_post_direct:
        token = os.getenv("DISCORD_TOKEN")
        if not token:
            logger.error("DISCORD_TOKEN not set, cannot post direct items")
        else:
            await post_direct_items(to_post_direct, token, all_guild_states)

    await close_redis()
    logger.info(f"=== RSS Feed Collector Finished (Redis mode) ===")


async def _collect_with_json() -> None:
    """Collect RSS updates using JSON files (legacy, has race conditions)."""
    logger.info("=== RSS Feed Collector Starting (JSON mode) ===")

    logger.info("Loading guild states...")
    all_guild_states = get_all_guild_states()
    logger.info("Loaded %d guild states", len(all_guild_states))

    if not all_guild_states:
        logger.info("App state empty – nothing to collect.")
        return

    # Track collected articles for logging
    total_collected = 0
    total_posted_direct = 0

    # Build a mapping channel_id -> list[dict] of items to post directly
    to_post_direct: Dict[int, List[Dict[str, Any]]] = {}

    for guild_id_str, guild_state in all_guild_states.items():
        logger.info("Checking guild %s", guild_id_str)

        if guild_id_str == "global":
            logger.info("Skipping global state")
            continue

        if not isinstance(guild_state, dict):
            logger.warning("Guild state for %s is not a dict (got %s) – skipping", guild_id_str, type(guild_state))
            continue

        logger.info("Guild %s state keys: %s", guild_id_str, list(guild_state.keys()))

        feeds = guild_state.get("rss_feeds", {})
        logger.info("Guild %s has %d feeds", guild_id_str, len(feeds) if feeds else 0)

        if not feeds:
            logger.info("No feeds for guild %s, skipping", guild_id_str)
            continue

        for feed_name, feed_info in feeds.items():
            logger.info("Processing feed '%s' for guild %s", feed_name, guild_id_str)

            if not feed_info.get("enabled", True):
                logger.info("Feed '%s' in guild %s is disabled, skipping", feed_name, guild_id_str)
                continue

            feed_url = feed_info.get("url")
            channel_id = feed_info.get("channel_id")
            seen_items = feed_info.get("seen_items", [])
            max_seen_items = feed_info.get("max_seen_items", 500)

            # Initialize last_summary if not present (migration)
            if "last_summary" not in feed_info:
                feed_info["last_summary"] = None

            # Initialize post_mode if not present (migration)
            if "post_mode" not in feed_info:
                feed_info["post_mode"] = "summary"

            post_mode = feed_info.get("post_mode", "summary")

            logger.info("Feed '%s': url=%s, channel_id=%s, seen_items=%d, mode=%s",
                       feed_name, feed_url, channel_id, len(seen_items), post_mode)

            if not feed_url or not channel_id:
                logger.warning("Feed '%s' in guild %s has missing url or channel_id, skipping", feed_name, guild_id_str)
                continue

            try:
                # Add small delay between feed fetches to be polite
                await asyncio.sleep(1)

                logger.info("Fetching feed '%s' from %s", feed_name, feed_url)

                # Fetch and parse the feed
                feed = feedparser.parse(feed_url)

                logger.info("Feed '%s' fetched: bozo=%s, entries=%d",
                           feed_name, feed.bozo, len(feed.entries))

                if feed.bozo:
                    logger.warning("Feed parse warning for '%s': %s", feed_name, feed.get('bozo_exception', 'Unknown error'))

                if not feed.entries:
                    logger.warning("No entries found in feed '%s'", feed_name)
                    continue

                # Filter to new items (not in seen_items)
                new_entries = []
                for entry in feed.entries:
                    item_id = get_item_id(entry)
                    if item_id not in seen_items:
                        new_entries.append(entry)

                # If this is the first run (empty seen_items), only process the 5 most recent items
                if not seen_items and len(new_entries) > 5:
                    logger.info("First run for feed '%s', limiting to 5 most recent items", feed_name)
                    new_entries = new_entries[:5]

                if new_entries:
                    logger.info("Found %d new items from feed '%s' (mode: %s)", len(new_entries), feed_name, post_mode)

                    # Check for breaking news matches
                    if feed_info.get('enabled', True):
                        from bot.domain.news.breaking_news_service import matches_breaking_news_topics
                        from bot.app.pending_breaking_news import add_pending_breaking_news_item
                        from bot.app.app_state import get_state_value

                        breaking_config = get_state_value("breaking_news_config", guild_id_str)
                        if breaking_config and breaking_config.get("enabled"):
                            topics = breaking_config.get("topics", [])
                            if topics:
                                for entry in new_entries:
                                    matched_topic = matches_breaking_news_topics(entry, topics)
                                    if matched_topic:
                                        article_data = extract_article_data(entry, feed, feed_name)
                                        add_pending_breaking_news_item(
                                            guild_id_str,
                                            article_data,
                                            matched_topic,
                                            feed_name
                                        )
                                        logger.info(f"Breaking news match: '{matched_topic}' in {feed_name}")

                    # Route based on post_mode
                    if post_mode == "direct":
                        for entry in new_entries:
                            to_post_direct.setdefault(channel_id, []).append({
                                'entry': entry,
                                'feed': feed,
                                'feed_name': feed_name,
                                'guild_id': guild_id_str,
                                'id': get_item_id(entry)
                            })
                        total_posted_direct += len(new_entries)
                        logger.info("Queued %d items for direct posting from feed '%s'", len(new_entries), feed_name)

                    else:  # post_mode == "summary"
                        article_data_list = []
                        for entry in new_entries:
                            article_data = extract_article_data(entry, feed, feed_name)
                            article_data_list.append(article_data)

                        add_pending_articles(guild_id_str, channel_id, feed_name, article_data_list)
                        total_collected += len(article_data_list)
                        logger.info("Collected %d items for summary from feed '%s'", len(article_data_list), feed_name)

                    # Add to seen_items
                    for entry in new_entries:
                        seen_items.insert(0, get_item_id(entry))

                    # Trim seen_items
                    feed_info["seen_items"] = seen_items[:max_seen_items]

                    # Update last_check
                    feed_info["last_check"] = dt.datetime.utcnow().isoformat()

            except Exception as e:
                logger.error("Error processing feed '%s' in guild %s: %s", feed_name, guild_id_str, str(e))
                continue

        # Save updated state for this guild
        try:
            set_state_value("rss_feeds", feeds, guild_id_str)
            logger.info("Saved state for guild %s", guild_id_str)
        except Exception as e:
            logger.error("Failed to save state for guild %s: %s", guild_id_str, str(e))

    logger.info(f"Collection summary: {total_collected} articles for summary, {total_posted_direct} articles for direct posting")

    # Post direct items if any
    if to_post_direct:
        token = os.getenv("DISCORD_TOKEN")
        if not token:
            logger.error("DISCORD_TOKEN not set, cannot post direct items")
        else:
            await post_direct_items(to_post_direct, token, all_guild_states)

    logger.info(f"=== RSS Feed Collector Finished (JSON mode) ===")


if __name__ == "__main__":
    asyncio.run(collect_rss_updates())
