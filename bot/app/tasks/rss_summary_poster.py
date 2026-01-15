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
from bot.app.app_state import get_all_guild_states, set_state_value
from bot.app.pending_news import get_all_pending_by_channel, clear_pending_articles_for_channel
from bot.app.story_history import (
    get_todays_story_history,
    add_stories_to_history,
    cleanup_old_history
)
from bot.domain.news.news_summary_service import generate_news_summary

logger = logging.getLogger("RSSSummaryPoster")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Summary times in Pacific timezone (24-hour format)
SUMMARY_TIMES = [
    (8, 0),   # 8:00 AM
    (20, 0),  # 8:00 PM
]


def should_post_summary_for_channel(channel_schedule: List[tuple[int, int]] = None) -> tuple[bool, str]:
    """
    Check if current time matches a channel's summary schedule.

    Args:
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

    # Round to nearest 10-minute interval
    rounded_minute = (now.minute // 10) * 10

    # Check against channel's schedule
    for hour, minute in channel_schedule:
        if now.hour == hour and rounded_minute == minute:
            # Determine edition based on time of day
            if hour < 12:
                edition = "Morning"
            elif hour < 18:
                edition = "Afternoon"
            else:
                edition = "Evening"

            logger.info(f"Time check: {now.hour}:{rounded_minute:02d} matches {hour}:{minute:02d} - {edition} edition")
            return True, edition

    return False, ""


def create_summary_embed(
    summary_text: str,
    article_count: int,
    feed_count: int,
    edition: str
) -> discord.Embed:
    """
    Create a Discord embed for the news summary.

    Args:
        summary_text: AI-generated summary with embedded links
        article_count: Total number of articles summarized
        feed_count: Number of feeds contributing articles
        edition: "Morning" or "Evening"

    Returns:
        Discord Embed object
    """
    embed = discord.Embed(
        title=f"📰 News Summary - {edition} Edition",
        description=summary_text,
        color=0x00a8ff,
        timestamp=dt.datetime.utcnow()
    )

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
                # Check if it's time to post for THIS channel
                channel_schedule = all_schedules.get(channel_id)  # None = use default
                should_post, edition = should_post_summary_for_channel(channel_schedule)

                if not should_post:
                    logger.info(f"Not time for summary in channel {channel_id}, skipping")
                    continue

                articles = data["articles"]
                feed_names = data["feed_names"]
                guild_id = data["guild_id"]

                logger.info(f"Generating {edition} summary for channel {channel_id}: {len(articles)} articles from {len(feed_names)} feeds")

                # Load today's story history for deduplication
                guild_id_str = str(guild_id)
                story_history = get_todays_story_history(guild_id_str, channel_id)
                logger.info(f"Loaded {len(story_history)} stories from today's history for channel {channel_id}")

                # Build filter map from feed configs
                all_guild_states = get_all_guild_states()
                guild_state = all_guild_states.get(guild_id, {})
                all_feeds = guild_state.get('rss_feeds', {})

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
                        edition=edition
                    )
                except Exception as e:
                    logger.error(f"Failed to generate summary for channel {channel_id}: {e}")
                    continue

                # Check if any stories to post after deduplication
                story_summaries = summary_result.get("story_summaries", [])
                if not story_summaries:
                    logger.info(f"No new stories for channel {channel_id} after deduplication")
                    # Still clear pending articles
                    cleared_count = clear_pending_articles_for_channel(guild_id_str, channel_id)
                    logger.info(f"Cleared {cleared_count} pending articles with no new stories")
                    continue

                # Create embed
                embed = create_summary_embed(
                    summary_text=summary_result["summary_text"],
                    article_count=summary_result["total_articles"],
                    feed_count=summary_result["feed_count"],
                    edition=edition
                )

                # Fetch channel and post
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

                except discord.Forbidden:
                    logger.error(f"Missing permissions to post in channel {channel_id}")
                    continue
                except discord.HTTPException as exc:
                    logger.error(f"HTTP error posting to channel {channel_id}: {exc}")
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
                    logger.error(f"Failed to clear pending articles or update state for channel {channel_id}: {e}")

            except Exception as e:
                logger.error(f"Error processing channel {channel_id}: {e}")

        logger.info("=== RSS Summary Poster Finished ===")
        await client.close()

    # Run the client
    await client.start(token)


if __name__ == "__main__":
    asyncio.run(post_summaries())
