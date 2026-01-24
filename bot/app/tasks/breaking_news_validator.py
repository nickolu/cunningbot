"""breaking_news_validator.py
Background task that validates and posts breaking news items.

This script runs every 2 minutes to:
1. Load pending breaking news items
2. Validate with LLM (newsworthy vs coincidence)
3. Apply time filter (< 2 hours old)
4. Check for duplicates (URL + semantic similarity)
5. Post to Discord breaking news channel
6. Update story history

Usage (inside Docker container):
    python -m bot.app.tasks.breaking_news_validator

Wire up in docker-compose.yml:
    command: bash -c "while true; do python -m bot.app.tasks.breaking_news_validator; sleep 120; done"
"""
from __future__ import annotations

import asyncio
import os
import logging
from typing import Dict, Any
from datetime import datetime
from zoneinfo import ZoneInfo

import discord
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

from bot.app.app_state import get_all_guild_states, set_state_value
from bot.app.pending_breaking_news import (
    get_pending_breaking_news_items,
    clear_pending_breaking_news_item,
    increment_retry_count,
    get_all_guilds_with_pending
)
from bot.domain.news.breaking_news_service import (
    validate_breaking_news_relevance,
    is_article_fresh,
    check_breaking_news_duplicate,
    MAX_CONSECUTIVE_LLM_FAILURES
)
from bot.app.story_history import add_stories_to_history

logger = logging.getLogger("BreakingNewsValidator")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)


def format_breaking_news_embed(article: Dict[str, Any], matched_topic: str) -> discord.Embed:
    """
    Format a breaking news article as a Discord embed.

    Args:
        article: Article data dictionary
        matched_topic: The topic that triggered this alert

    Returns:
        Discord embed with red color and breaking news styling
    """
    title = article.get('title', 'No title')
    # Add alert emoji to title
    if not title.startswith('🚨'):
        title = f"🚨 {title}"
    title = title[:256]  # Discord limit

    link = article.get('link', '')
    description = article.get('description', 'No description available')[:2000]  # Discord limit
    source = article.get('source', 'Unknown')
    image_url = article.get('image_url')

    # Parse published timestamp
    published_str = article.get('published')
    timestamp = None
    if published_str:
        try:
            timestamp = datetime.fromisoformat(published_str.replace('Z', '+00:00'))
        except Exception:
            pass

    # Create embed with red color (breaking news)
    embed = discord.Embed(
        title=title,
        url=link,
        description=description,
        color=0xFF0000,  # Red
        timestamp=timestamp
    )

    # Add image if available
    if image_url:
        try:
            embed.set_image(url=image_url)
        except Exception:
            pass

    # Add topic tag field
    embed.add_field(name="Alert Topic", value=matched_topic.title(), inline=True)

    # Add footer
    embed.set_footer(text=f"Breaking News Alert • Source: {source}")

    return embed


async def post_to_discord(
    channel_id: int,
    article: Dict[str, Any],
    matched_topic: str,
    token: str
) -> bool:
    """
    Post a breaking news article to Discord.

    Args:
        channel_id: Discord channel ID
        article: Article data
        matched_topic: Topic that triggered the alert
        token: Discord bot token

    Returns:
        True if posted successfully, False otherwise
    """
    try:
        # Create Discord client
        intents = discord.Intents.none()
        client = discord.Client(intents=intents)

        posted = False

        @client.event  # type: ignore[misc]
        async def on_ready():
            nonlocal posted
            logger.info(f"Discord client logged in as {client.user}")

            try:
                # Fetch channel
                channel = await client.fetch_channel(channel_id)  # type: ignore[attr-defined]

                if not isinstance(channel, (discord.TextChannel, discord.Thread)):
                    logger.warning(f"Channel {channel_id} is not a text channel")
                    return

                # Format and post embed
                embed = format_breaking_news_embed(article, matched_topic)
                await channel.send(embed=embed)

                logger.info(f"Posted breaking news to channel {channel_id}: {article.get('title', 'Unknown')}")
                posted = True

            except discord.NotFound:
                logger.error(f"Channel {channel_id} not found - may have been deleted")
            except discord.Forbidden:
                logger.error(f"Missing permissions to post in channel {channel_id}")
            except discord.HTTPException as exc:
                logger.error(f"HTTP error posting to channel {channel_id}: {exc}")
            except Exception as exc:
                logger.error(f"Unexpected error posting to channel {channel_id}: {exc}")
            finally:
                await client.close()

        # Start client
        await client.start(token)
        return posted

    except Exception as e:
        logger.error(f"Error creating Discord client: {e}")
        return False


async def process_pending_breaking_news() -> None:
    """Main entry point for breaking news validation."""
    logger.info("=== Breaking News Validator Starting ===")

    # Get Discord token
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.error("DISCORD_TOKEN not set, cannot process breaking news")
        return

    # Load all guild states
    all_guild_states = get_all_guild_states()
    if not all_guild_states:
        logger.info("No guild states found")
        return

    # Get all guilds with pending items
    guilds_with_pending = get_all_guilds_with_pending()
    if not guilds_with_pending:
        logger.info("No pending breaking news items")
        return

    logger.info(f"Processing {len(guilds_with_pending)} guilds with pending breaking news")

    # Process each guild
    for guild_id_str in guilds_with_pending:
        logger.info(f"Processing guild {guild_id_str}")

        # Get breaking news config
        guild_state = all_guild_states.get(guild_id_str, {})
        breaking_config = guild_state.get("breaking_news_config")

        if not breaking_config:
            logger.warning(f"Guild {guild_id_str} has pending items but no breaking news config")
            continue

        if not breaking_config.get("enabled"):
            logger.info(f"Guild {guild_id_str} has breaking news disabled")
            continue

        channel_id = breaking_config.get("channel_id")
        if not channel_id:
            logger.warning(f"Guild {guild_id_str} breaking news config missing channel_id")
            continue

        # Get pending items
        pending_items = get_pending_breaking_news_items(guild_id_str)
        logger.info(f"Guild {guild_id_str} has {len(pending_items)} pending items")

        # Process each pending item (in reverse order so we can safely remove)
        for i in range(len(pending_items) - 1, -1, -1):
            item = pending_items[i]
            article = item.get("article", {})
            matched_topic = item.get("matched_topic", "unknown")
            feed_name = item.get("feed_name", "Unknown")
            retry_count = item.get("retry_count", 0)

            article_title = article.get('title', 'Untitled')
            logger.info(f"Processing item {i}: '{article_title}' (topic: {matched_topic})")

            # Stage 1: LLM Validation
            try:
                is_newsworthy = await validate_breaking_news_relevance(article, matched_topic)
            except Exception as e:
                logger.error(f"LLM validation error: {e}")
                # Increment retry count
                new_count = increment_retry_count(guild_id_str, i)
                if new_count >= MAX_CONSECUTIVE_LLM_FAILURES:
                    logger.warning(f"Max retries reached for '{article_title}', removing from queue")
                    clear_pending_breaking_news_item(guild_id_str, i)
                continue

            if not is_newsworthy:
                logger.info(f"LLM rejected: '{article_title}' is not newsworthy")
                clear_pending_breaking_news_item(guild_id_str, i)
                continue

            # Stage 2: Time Filtering
            if not is_article_fresh(article):
                logger.info(f"Article rejected: '{article_title}' is too old")
                clear_pending_breaking_news_item(guild_id_str, i)
                continue

            # Stage 3: Duplicate Detection
            try:
                is_duplicate = await check_breaking_news_duplicate(article, guild_id_str, channel_id)
            except Exception as e:
                logger.error(f"Duplicate check error: {e}")
                is_duplicate = False  # Continue processing if check fails

            if is_duplicate:
                logger.info(f"Duplicate detected: '{article_title}'")
                clear_pending_breaking_news_item(guild_id_str, i)
                continue

            # Stage 4: Post to Discord
            posted = await post_to_discord(channel_id, article, matched_topic, token)

            if posted:
                # Add to story history
                pacific_tz = ZoneInfo("America/Los_Angeles")
                now = datetime.now(pacific_tz)

                story_entry = {
                    "title": article_title,
                    "articles": [article],
                    "posted_at": now.isoformat(),
                    "is_breaking_news": True,
                    "topic": matched_topic
                }

                try:
                    add_stories_to_history(guild_id_str, channel_id, [story_entry])
                    logger.info(f"Added to story history: '{article_title}'")
                except Exception as e:
                    logger.error(f"Error adding to story history: {e}")

                # Remove from pending
                clear_pending_breaking_news_item(guild_id_str, i)
                logger.info(f"Successfully processed breaking news: '{article_title}'")

            else:
                # Check if channel was deleted (NotFound error)
                # If so, disable breaking news for this guild
                logger.error(f"Failed to post '{article_title}' - may need to disable feature")
                # Note: Channel deletion detection happens in post_to_discord logging

    logger.info("=== Breaking News Validator Complete ===")


if __name__ == "__main__":
    asyncio.run(process_pending_breaking_news())
