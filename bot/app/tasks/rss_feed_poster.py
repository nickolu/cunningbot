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

import feedparser
from bot.app.app_state import get_all_guild_states, set_state_value
from bot.app.pending_news import add_pending_articles

logger = logging.getLogger("RSSFeedPoster")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)


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


async def collect_rss_updates() -> None:
    """Main entry point called once per invocation."""
    logger.info("=== RSS Feed Collector Starting ===")

    logger.info("Loading guild states...")
    all_guild_states = get_all_guild_states()
    logger.info("Loaded %d guild states", len(all_guild_states))

    if not all_guild_states:
        logger.info("App state empty – nothing to collect.")
        return

    # Track collected articles for logging
    total_collected = 0

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

        for feed_name, feed_info in feeds.items():
            logger.info("Processing feed '%s' for guild %s", feed_name, guild_id_str)

            if not feed_info.get("enabled", True):
                logger.info("Feed '%s' in guild %s is disabled, skipping", feed_name, guild_id_str)
                continue

            feed_url = feed_info.get("url")
            channel_id = feed_info.get("channel_id")
            seen_items = feed_info.get("seen_items", [])
            max_seen_items = feed_info.get("max_seen_items", 100)

            # Initialize last_summary if not present (migration)
            if "last_summary" not in feed_info:
                feed_info["last_summary"] = None

            logger.info("Feed '%s': url=%s, channel_id=%s, seen_items=%d",
                       feed_name, feed_url, channel_id, len(seen_items))

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
                new_items = []
                for entry in feed.entries:
                    item_id = get_item_id(entry)
                    if item_id not in seen_items:
                        # Extract article data
                        article_data = extract_article_data(entry, feed, feed_name)
                        new_items.append(article_data)

                # If this is the first run (empty seen_items), only collect the 5 most recent items
                if not seen_items and len(new_items) > 5:
                    logger.info("First run for feed '%s', limiting to 5 most recent items", feed_name)
                    new_items = new_items[:5]

                if new_items:
                    logger.info("Collecting %d new items from feed '%s'", len(new_items), feed_name)

                    # Add to pending_news.json
                    add_pending_articles(guild_id_str, channel_id, feed_name, new_items)

                    # Add to seen_items to prevent re-collecting
                    for item in new_items:
                        seen_items.insert(0, item['id'])

                    # Trim seen_items to max
                    feed_info["seen_items"] = seen_items[:max_seen_items]

                    # Update last_check
                    feed_info["last_check"] = dt.datetime.utcnow().isoformat()

                    total_collected += len(new_items)

            except Exception as e:
                logger.error("Error processing feed '%s' in guild %s: %s", feed_name, guild_id_str, str(e))
                continue

        # Save updated state for this guild
        try:
            set_state_value("rss_feeds", feeds, guild_id_str)
            logger.info("Saved state for guild %s", guild_id_str)
        except Exception as e:
            logger.error("Failed to save state for guild %s: %s", guild_id_str, str(e))

    logger.info(f"=== RSS Feed Collector Finished: Collected {total_collected} articles ===")


if __name__ == "__main__":
    asyncio.run(collect_rss_updates())
