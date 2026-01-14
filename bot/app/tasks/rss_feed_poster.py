"""rss_feed_poster.py
Script intended to be invoked every 10 minutes (e.g. via cron or Docker loop).
It reads the registered *rss_feeds* in each guild's app state and, if any feed is
enabled, fetches new items and posts them to the configured Discord channel.

Usage (inside Docker container hosting the bot):
    python -m bot.app.tasks.rss_feed_poster

You can wire this up in docker-compose.yml:
    command: bash -c "while true; do python -m bot.app.tasks.rss_feed_poster; sleep 600; done"

Ensure the container has the DISCORD_TOKEN environment variable set and that the bot
has permission to send messages to the registered channels.
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
        stripper.feed(html_text)
        text = stripper.get_text().strip()
    except Exception:
        # If HTML parsing fails, just strip basic tags
        import re
        text = re.sub(r'<[^>]+>', '', html_text).strip()

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


def format_item_embed(entry, feed) -> discord.Embed:
    """Format an RSS entry as a Discord embed."""
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


async def post_rss_updates() -> None:
    """Main entry point called once per invocation."""
    logger.info("=== RSS Feed Poster Starting ===")

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.error("DISCORD_TOKEN environment variable not set – aborting.")
        return

    logger.info("Loading guild states...")
    all_guild_states = get_all_guild_states()
    logger.info("Loaded %d guild states", len(all_guild_states))

    if not all_guild_states:
        logger.info("App state empty – nothing to post.")
        return

    # Build a mapping channel_id -> list[dict] of items to post
    to_post: Dict[int, List[Dict[str, Any]]] = {}

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
                        new_items.append({
                            'id': item_id,
                            'entry': entry,
                            'feed': feed,
                            'feed_name': feed_name,
                            'guild_id': guild_id_str,
                        })

                # If this is the first run (empty seen_items), only post the 5 most recent items
                if not seen_items and len(new_items) > 5:
                    logger.info("First run for feed '%s', limiting to 5 most recent items", feed_name)
                    new_items = new_items[:5]

                # Sort by published date (oldest first) so we post in chronological order
                new_items.sort(key=lambda x: x['entry'].get('published', ''))

                if new_items:
                    logger.info("Found %d new items in feed '%s'", len(new_items), feed_name)

                    # Add items to post queue
                    to_post.setdefault(channel_id, []).extend(new_items)

            except Exception as e:
                logger.error("Error processing feed '%s' in guild %s: %s", feed_name, guild_id_str, str(e))
                continue

    logger.info("Finished processing all feeds. Items queued for posting: %d", sum(len(items) for items in to_post.values()))
    logger.info("Channels with items: %s", list(to_post.keys()))

    if not to_post:
        logger.info("No new items to post.")
        return

    # Create Discord client and post items
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

        # Update state only for successfully posted items
        # Group by guild and feed
        state_updates: Dict[str, Dict[str, List[str]]] = {}  # guild_id -> feed_name -> [item_ids]

        for item in successfully_posted_items:
            guild_id = item['guild_id']
            feed_name = item['feed_name']
            item_id = item['id']

            if guild_id not in state_updates:
                state_updates[guild_id] = {}
            if feed_name not in state_updates[guild_id]:
                state_updates[guild_id][feed_name] = []

            state_updates[guild_id][feed_name].append(item_id)

        # Update state for each guild
        for guild_id_str, feed_updates in state_updates.items():
            try:
                # Get current guild state
                guild_state = all_guild_states.get(guild_id_str, {})
                feeds = guild_state.get('rss_feeds', {})

                for feed_name, new_ids in feed_updates.items():
                    if feed_name in feeds:
                        # Add new IDs to seen_items and trim
                        current_seen = feeds[feed_name].get('seen_items', [])
                        max_items = feeds[feed_name].get('max_seen_items', 100)
                        updated_seen = new_ids + current_seen
                        updated_seen = updated_seen[:max_items]

                        feeds[feed_name]['seen_items'] = updated_seen
                        feeds[feed_name]['last_check'] = dt.datetime.utcnow().isoformat()

                # Save updated feeds back to state
                set_state_value("rss_feeds", feeds, guild_id_str)
                logger.info("Updated state for guild %s: marked %d items as seen across %d feeds",
                           guild_id_str, sum(len(ids) for ids in feed_updates.values()), len(feed_updates))
            except Exception as e:
                logger.error("Failed to update state for guild %s: %s", guild_id_str, str(e))

        await client.close()

    # Run the client
    await client.start(token)


if __name__ == "__main__":
    asyncio.run(post_rss_updates())
