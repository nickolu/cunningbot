"""rss_summary_poster.py
Script intended to be invoked every 10 minutes (e.g. via Docker loop).
It checks if the current time is 8am or 8pm Pacific, and if so, generates
AI-powered summaries of pending RSS articles and posts them to Discord.

Usage (inside Docker container hosting the bot):
    python -m bot.app.tasks.rss_summary_poster

You can wire this up in docker-compose.yml:
    command: bash -c "while true; do python -m bot.app.tasks.rss_summary_poster; sleep 600; done"

Ensure the container has DISCORD_TOKEN and OPENAI_API_KEY environment variables set.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import logging
from typing import Any, Dict, List
from zoneinfo import ZoneInfo
from collections import defaultdict

import discord
from bot.app.app_state import get_all_guild_states, set_state_value, get_state_value
from bot.app.pending_news import get_all_pending_by_channel, clear_pending_articles_for_channel
from bot.app.story_history import (
    get_todays_story_history,
    get_stories_within_window,
    get_channel_dedup_window,
    add_stories_to_history,
    cleanup_old_history
)
from bot.domain.news.news_summary_service import generate_news_summary

logger = logging.getLogger("RSSSummaryPoster")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Discord API error codes
DISCORD_ERROR_UNKNOWN_CHANNEL = 10003
DISCORD_ERROR_UNKNOWN_GUILD = 10004
DISCORD_ERROR_MISSING_ACCESS = 50001
DISCORD_ERROR_MISSING_PERMISSIONS = 50013

# Summary times in Pacific timezone (24-hour format)
SUMMARY_TIMES = [
    (8, 0),   # 8:00 AM
    (20, 0),  # 8:00 PM
]


def should_post_summary_for_channel(
    guild_id: int,
    channel_id: int,
    channel_schedule: List[tuple[int, int]] = None
) -> tuple[bool, str]:
    """
    Check if it's time to post a summary for a channel using flexible time windows.

    Instead of strict time matching, this checks if enough time has passed since
    the last summary of each edition (Morning/Evening), allowing summaries that
    take longer than 10 minutes to complete without missing the next scheduled post.

    Args:
        guild_id: Guild ID
        channel_id: Channel ID
        channel_schedule: List of (hour, minute) tuples for this channel, or None for default

    Returns:
        Tuple of (should_post, edition) where edition is "Morning", "Afternoon", or "Evening"
    """
    # Use default schedule if not provided
    if channel_schedule is None:
        channel_schedule = SUMMARY_TIMES

    # Get current Pacific time
    pacific_tz = ZoneInfo("America/Los_Angeles")
    now = dt.datetime.now(pacific_tz)

    # Load last summary times for this channel
    guild_id_str = str(guild_id)
    all_last_summaries = get_state_value("channel_last_summaries", guild_id_str) or {}
    channel_last_summaries = all_last_summaries.get(str(channel_id), {})

    # Check each scheduled time to see if we should post
    for hour, minute in channel_schedule:
        # Determine edition based on time of day
        if hour < 12:
            edition = "Morning"
        elif hour < 18:
            edition = "Afternoon"
        else:
            edition = "Evening"

        # Check if current time is past the scheduled time
        scheduled_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

        # Only consider this edition if we're past the scheduled time (within reason)
        # Allow up to 2 hours after scheduled time
        if now < scheduled_time or (now - scheduled_time).total_seconds() > 7200:
            continue

        # Check when we last posted this edition
        last_summary_str = channel_last_summaries.get(edition)

        if not last_summary_str:
            # Never posted this edition, go ahead
            logger.info(f"Time check: Never posted {edition} edition for channel {channel_id} - posting now")
            return True, edition

        try:
            # Parse last summary time
            last_summary = dt.datetime.fromisoformat(last_summary_str)
            if last_summary.tzinfo is None:
                last_summary = last_summary.replace(tzinfo=pacific_tz)

            hours_since_last = (now - last_summary).total_seconds() / 3600

            # Post if it's been more than 11 hours since last summary of this edition
            if hours_since_last > 11:
                logger.info(f"Time check: {hours_since_last:.1f}h since last {edition} edition (posted at {last_summary_str}) for channel {channel_id} - posting now")
                return True, edition
            else:
                logger.info(f"Time check: Only {hours_since_last:.1f}h since last {edition} edition (posted at {last_summary_str}) for channel {channel_id} - skipping")

        except (ValueError, TypeError) as e:
            logger.error(f"CRITICAL: Could not parse last summary time '{last_summary_str}' for channel {channel_id}: {e}")
            logger.error(f"channel_last_summaries data: {channel_last_summaries}")
            # Return FALSE to prevent repeated posts due to parsing errors
            # This is conservative - better to skip than spam
            return False, ""

    return False, ""


def _cleanup_inaccessible_channel(guild_id: int, channel_id: int, reason: str) -> None:
    """
    Clean up pending articles and state for channels that are permanently inaccessible.

    Args:
        guild_id: Guild ID
        channel_id: Channel ID that's inaccessible
        reason: Human-readable reason (for logging)
    """
    logger.info(f"Cleaning up inaccessible channel {channel_id}: {reason}")

    try:
        # Clear pending articles to prevent future retry attempts
        cleared_count = clear_pending_articles_for_channel(guild_id, channel_id)
        logger.info(f"Cleared {cleared_count} pending articles for deleted/inaccessible channel {channel_id}")

        # Optionally: Remove channel from any custom configurations
        # (schedules, limits, diversity settings, etc.)
        # This prevents accumulation of config for deleted channels

    except Exception as e:
        logger.error(f"Error cleaning up channel {channel_id}: {e}")


def create_summary_embed(
    summary_text: str,
    article_count: int,
    feed_count: int,
    edition: str,
    stats: Dict[str, int] = None
) -> discord.Embed:
    """
    Create a Discord embed for the news summary.

    Args:
        summary_text: AI-generated summary with embedded links
        article_count: Total number of articles summarized
        feed_count: Number of feeds contributing articles
        edition: "Morning" or "Evening"
        stats: Optional filtering statistics

    Returns:
        Discord Embed object
    """
    # Determine color based on whether there are stories
    if article_count > 0:
        color = 0x00a8ff  # Blue for normal summaries
    else:
        color = 0x808080  # Gray for empty summaries

    embed = discord.Embed(
        title=f"📰 News Summary - {edition} Edition",
        description=summary_text,
        color=color,
        timestamp=dt.datetime.utcnow()
    )

    # Build footer with statistics
    if stats:
        footer_parts = []

        # Original count
        original = stats.get("original_count", 0)
        footer_parts.append(f"Collected: {original}")

        # Filtering breakdown
        filter_parts = []
        if stats.get("filtered_by_limit", 0) > 0:
            filter_parts.append(f"{stats['filtered_by_limit']} by limit")
        if stats.get("filtered_by_feed_filter", 0) > 0:
            filter_parts.append(f"{stats['filtered_by_feed_filter']} by filters")
        if stats.get("filtered_by_url_dedup", 0) > 0:
            filter_parts.append(f"{stats['filtered_by_url_dedup']} by URL dedup")
        if stats.get("filtered_by_story_dedup", 0) > 0:
            filter_parts.append(f"{stats['filtered_by_story_dedup']} by story dedup")

        if filter_parts:
            footer_parts.append(f"Filtered: {', '.join(filter_parts)}")

        # Feed distribution (if diversity applied)
        feed_dist = stats.get("feed_distribution")
        if feed_dist:
            # Format as "Feed1: 5, Feed2: 3, Feed3: 2"
            dist_items = sorted(feed_dist.items(), key=lambda x: -x[1])  # Sort by count desc
            dist_str = ", ".join(f"{feed}: {count}" for feed, count in dist_items[:5])  # Top 5
            if len(dist_items) > 5:
                dist_str += f", +{len(dist_items) - 5} more"
            footer_parts.append(f"Distribution: {dist_str}")

        # Feed count
        feed_text = "feed" if feed_count == 1 else "feeds"
        footer_parts.append(f"{feed_count} {feed_text}")

        embed.set_footer(text=" • ".join(footer_parts))
    else:
        # Fallback to old format
        feed_text = "feed" if feed_count == 1 else "feeds"
        article_text = "article" if article_count == 1 else "articles"
        embed.set_footer(text=f"Summarized {article_count} {article_text} from {feed_count} {feed_text}")

    return embed


async def post_summaries() -> None:
    """Main entry point called once per invocation."""
    logger.info("=== RSS Summary Poster Starting ===")

    # Clean up old story history from previous days
    cleanup_old_history()

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.error("DISCORD_TOKEN environment variable not set – aborting.")
        return

    # Get all pending articles grouped by channel
    channel_articles = get_all_pending_by_channel()

    if not channel_articles:
        logger.info("No pending articles to summarize.")
        return

    logger.info(f"Found {len(channel_articles)} channels with pending articles")

    # Load all channel schedules from app_state
    all_guild_states = get_all_guild_states()
    all_schedules = {}
    for guild_id_str in all_guild_states.keys():
        if guild_id_str != "global" and isinstance(all_guild_states[guild_id_str], dict):
            guild_schedules = all_guild_states[guild_id_str].get("channel_summary_schedules", {})
            # Convert string channel_ids to int and merge
            for ch_id_str, schedule in guild_schedules.items():
                try:
                    all_schedules[int(ch_id_str)] = schedule
                except (ValueError, TypeError):
                    logger.warning(f"Invalid channel_id in schedules: {ch_id_str}")

    logger.info(f"Loaded custom schedules for {len(all_schedules)} channels")

    # Create Discord client
    intents = discord.Intents.none()
    client = discord.Client(intents=intents)

    @client.event  # type: ignore[misc]
    async def on_ready():
        logger.info("Discord client logged in as %s", client.user)

        for channel_id, data in channel_articles.items():
            try:
                articles = data["articles"]
                feed_names = data["feed_names"]
                guild_id = data["guild_id"]

                # Check if it's time to post for THIS channel
                channel_schedule = all_schedules.get(channel_id)  # None = use default
                should_post, edition = should_post_summary_for_channel(guild_id, channel_id, channel_schedule)

                if not should_post:
                    logger.info(f"Not time for summary in channel {channel_id}, skipping")
                    continue

                # Filter out articles from removed feeds
                all_guild_states = get_all_guild_states()
                guild_state = all_guild_states.get(str(guild_id), {})
                all_feeds = guild_state.get('rss_feeds', {})

                # Check for orphaned feeds (feeds with pending articles but removed from config)
                valid_feed_names = [name for name in feed_names if name in all_feeds]
                orphaned_feeds = [name for name in feed_names if name not in all_feeds]

                if orphaned_feeds:
                    from bot.app.pending_news import clear_pending_articles_for_feed
                    logger.warning(f"Found orphaned feeds in channel {channel_id}: {orphaned_feeds}")
                    for orphaned_feed in orphaned_feeds:
                        cleared = clear_pending_articles_for_feed(str(guild_id), channel_id, orphaned_feed)
                        logger.info(f"Cleaned up {cleared} orphaned articles from removed feed: {orphaned_feed}")

                    # Update feed_names to only include valid feeds
                    feed_names = valid_feed_names

                    # Filter articles to only include those from valid feeds
                    articles = [a for a in articles if a.get('feed_name') in valid_feed_names]

                    if not articles:
                        logger.info(f"No articles remaining after filtering orphaned feeds for channel {channel_id}")
                        continue

                logger.info(f"Generating {edition} summary for channel {channel_id}: {len(articles)} articles from {len(feed_names)} feeds")

                # Load story history within deduplication window
                guild_id_str = str(guild_id)
                window_hours = get_channel_dedup_window(guild_id, channel_id)
                story_history = get_stories_within_window(guild_id_str, channel_id, window_hours)
                logger.info(f"Using {window_hours}h dedup window for channel {channel_id}")

                # Load article processing limits for this channel
                from bot.domain.news.news_summary_service import get_channel_article_limits
                limits = get_channel_article_limits(guild_id, channel_id)
                logger.info(f"Using limits for channel {channel_id}: {limits['initial_limit']} → {limits['top_articles_limit']} → {limits['cluster_limit']}")

                # Load feed diversity config for this channel
                from bot.domain.news.feed_diversity import get_channel_feed_diversity
                diversity_config = get_channel_feed_diversity(guild_id, channel_id)
                if diversity_config.get("strategy") != "disabled":
                    logger.info(f"Feed diversity enabled for channel {channel_id}: {diversity_config}")

                # Build filter map from feed configs (all_feeds already loaded above)
                filter_map = {
                    name: feed.get('filter_instructions')
                    for name, feed in all_feeds.items()
                    if name in feed_names and feed.get('filter_instructions')
                }

                # Generate AI summary with deduplication
                try:
                    summary_result = await generate_news_summary(
                        articles=articles,
                        feed_names=feed_names,
                        filter_map=filter_map,
                        story_history=story_history,
                        edition=edition,
                        initial_limit=limits["initial_limit"],
                        top_articles_limit=limits["top_articles_limit"],
                        cluster_limit=limits["cluster_limit"],
                        diversity_config=diversity_config
                    )
                except Exception as e:
                    logger.error(f"Failed to generate summary for channel {channel_id}: {e}")
                    continue

                # Get stats and story summaries
                story_summaries = summary_result.get("story_summaries", [])
                stats = summary_result.get("stats", {})

                # Create embed (always post, even if no stories)
                embed = create_summary_embed(
                    summary_text=summary_result["summary_text"],
                    article_count=summary_result["total_articles"],
                    feed_count=summary_result["feed_count"],
                    edition=edition,
                    stats=stats
                )

                # CRITICAL: Save timestamp FIRST to prevent infinite retries
                # Even if posting fails, we won't retry for 11+ hours
                try:
                    pacific_tz = ZoneInfo("America/Los_Angeles")
                    current_time = dt.datetime.now(pacific_tz).isoformat()

                    guild_id_str = str(guild_id)
                    all_last_summaries = get_state_value("channel_last_summaries", guild_id_str) or {}
                    channel_id_str = str(channel_id)

                    if channel_id_str not in all_last_summaries:
                        all_last_summaries[channel_id_str] = {}

                    all_last_summaries[channel_id_str][edition] = current_time
                    set_state_value("channel_last_summaries", all_last_summaries, guild_id_str)
                    logger.info(f"Pre-saved {edition} edition timestamp for channel {channel_id}")

                except Exception as e:
                    logger.error(f"CRITICAL: Failed to pre-save timestamp for channel {channel_id}: {e}")
                    # Don't continue - if we can't save timestamp, we'll have infinite retries
                    continue

                # NOW try to post (if this fails, timestamp is already saved)
                try:
                    channel = client.get_channel(channel_id)
                    if channel is None:
                        channel = await client.fetch_channel(channel_id)  # type: ignore[attr-defined]

                    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
                        logger.warning(f"Channel ID {channel_id} is not a text channel")
                        continue

                    await channel.send(embed=embed)
                    logger.info(f"Posted {edition} summary to channel {channel_id}")

                    # Save story data to history for deduplication
                    pacific_tz = ZoneInfo("America/Los_Angeles")
                    story_data = [
                        {
                            "title": story["title"],
                            "summary": story["summary"],
                            "article_urls": [link["url"] for link in story["links"]],
                            "posted_at": dt.datetime.now(pacific_tz).isoformat(),
                            "edition": edition
                        }
                        for story in story_summaries
                    ]
                    add_stories_to_history(guild_id_str, channel_id, story_data)
                    logger.info(f"Saved {len(story_data)} stories to history for channel {channel_id}")

                except discord.Forbidden as exc:
                    logger.error(f"PERMANENT: Missing permissions for channel {channel_id}: {exc}")
                    # Clean up - this channel is inaccessible
                    _cleanup_inaccessible_channel(guild_id, channel_id, "Missing permissions")
                    continue

                except discord.HTTPException as exc:
                    # Check if this is a permanent failure (404, 403)
                    if exc.code == DISCORD_ERROR_UNKNOWN_CHANNEL:  # 10003: Unknown Channel
                        logger.error(f"PERMANENT: Channel {channel_id} does not exist (404)")
                        _cleanup_inaccessible_channel(guild_id, channel_id, "Channel deleted")
                        continue
                    elif exc.code == DISCORD_ERROR_MISSING_PERMISSIONS:  # 50013: Missing Access
                        logger.error(f"PERMANENT: No access to channel {channel_id} (403)")
                        _cleanup_inaccessible_channel(guild_id, channel_id, "Access denied")
                        continue
                    else:
                        # Transient error (rate limit, server error, etc)
                        logger.warning(f"TRANSIENT: HTTP error for channel {channel_id} (code {exc.code}): {exc}")
                        logger.warning(f"Will retry next scheduled run")
                        continue

                except Exception as exc:
                    logger.error(f"Unexpected error posting to channel {channel_id}: {exc}")
                    continue

                # Clear pending articles from pending_news.json
                try:
                    cleared_count = clear_pending_articles_for_channel(guild_id, channel_id)
                    logger.info(f"Cleared {cleared_count} pending articles for channel {channel_id}")

                    # Update last_summary timestamp in app_state for feeds in this channel
                    all_guild_states = get_all_guild_states()
                    guild_state = all_guild_states.get(guild_id, {})
                    all_feeds = guild_state.get('rss_feeds', {})

                    for feed_name in feed_names:
                        if feed_name in all_feeds:
                            all_feeds[feed_name]["last_summary"] = dt.datetime.utcnow().isoformat()

                    # Save updated feeds back to state
                    set_state_value("rss_feeds", all_feeds, guild_id)
                    logger.info(f"Updated last_summary for {len(feed_names)} feeds in guild {guild_id}")

                except Exception as e:
                    logger.error(f"Failed to clear pending articles or update feed state for channel {channel_id}: {e}")

            except Exception as e:
                logger.error(f"Error processing channel {channel_id}: {e}")

        logger.info("=== RSS Summary Poster Finished ===")

        # Close OpenAI client to prevent connection leaks
        from bot.api.openai.chat_completions_client import openai
        try:
            await openai.close()
            logger.info("Closed OpenAI client connections")
            # Give extra time for HTTP connections to fully close
            await asyncio.sleep(1.0)
        except Exception as e:
            logger.warning(f"Error closing OpenAI client: {e}")

        # Give a moment for any pending operations to complete
        await asyncio.sleep(0.5)
        await client.close()

    # Run the client
    try:
        await client.start(token)
    finally:
        # Ensure client is closed even if start() fails
        if not client.is_closed():
            await client.close()


if __name__ == "__main__":
    asyncio.run(post_summaries())
