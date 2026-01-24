"""migrate_rss.py

One-time migration script to move RSS feed data from JSON to Redis.

This script:
1. Reads all RSS feed configs from app_state.json
2. Creates each feed in Redis Hash
3. Migrates seen_items lists to Redis Sets
4. Migrates pending articles from pending_news.json to Redis Lists
5. Migrates summary tracking data to Redis
6. Validates migration was successful
7. Optionally backs up JSON data

Usage (inside Docker container):
    python -m bot.app.redis.migrations.migrate_rss [--dry-run] [--backup]

Options:
    --dry-run    Show what would be migrated without making changes
    --backup     Keep JSON data after migration (default: keep as backup)
    --delete     Remove JSON data after successful migration (use with caution)
"""
import asyncio
import argparse
import logging
from typing import Dict, Any

from bot.app.app_state import get_all_guild_states, set_state_value
from bot.app.pending_news import get_all_pending_by_channel, clear_pending_articles_for_channel
from bot.app.redis.rss_store import RSSRedisStore
from bot.app.redis.client import initialize_redis, close_redis

logger = logging.getLogger("RSSMigration")
logging.basicConfig(level=logging.INFO)


async def migrate_rss_data(dry_run: bool = False, delete_json: bool = False) -> None:
    """Migrate RSS feed data from JSON to Redis.

    Args:
        dry_run: If True, only show what would be migrated
        delete_json: If True, remove JSON data after successful migration
    """
    logger.info("Starting RSS migration (dry_run=%s)", dry_run)

    # Initialize Redis
    await initialize_redis()
    store = RSSRedisStore()

    # Get all guild states from JSON
    all_guild_states = get_all_guild_states()
    if not all_guild_states:
        logger.info("No guild states found - nothing to migrate")
        await close_redis()
        return

    total_feeds = 0
    total_seen_items = 0
    total_pending_articles = 0
    total_summary_tracking = 0
    migrated_feeds = 0
    migrated_seen_items = 0
    migrated_pending_articles = 0
    migrated_summary_tracking = 0

    # Migrate each guild
    for guild_id_str, guild_state in all_guild_states.items():
        if guild_id_str == "global":
            continue

        if not isinstance(guild_state, dict):
            logger.warning(
                "Guild state for %s is not a dict (got %s) - skipping",
                guild_id_str, type(guild_state)
            )
            continue

        logger.info("Processing guild %s", guild_id_str)

        # Migrate RSS feeds
        rss_feeds = guild_state.get("rss_feeds", {})
        total_feeds += len(rss_feeds)

        for feed_name, feed_config in rss_feeds.items():
            logger.info(
                "  Feed '%s': url=%s, channel_id=%s, enabled=%s",
                feed_name,
                feed_config.get("url", "")[:50],
                feed_config.get("channel_id"),
                feed_config.get("enabled", True)
            )

            # Extract seen items
            seen_items = feed_config.get("seen_items", [])
            total_seen_items += len(seen_items)

            if not dry_run:
                # Save feed config to Redis
                await store.save_feed(guild_id_str, feed_name, feed_config)
                migrated_feeds += 1
                logger.info("    ✓ Migrated feed config for '%s'", feed_name)

                # Migrate seen items to Redis Set
                if seen_items:
                    added_count = await store.mark_seen(guild_id_str, feed_name, seen_items)
                    migrated_seen_items += added_count
                    logger.info(
                        "    ✓ Migrated %d seen items for '%s'",
                        added_count, feed_name
                    )

        # Migrate channel summary tracking data
        channel_last_summaries = guild_state.get("channel_last_summaries", {})

        for channel_id_str, editions in channel_last_summaries.items():
            if isinstance(editions, dict):
                for edition, timestamp in editions.items():
                    total_summary_tracking += 1

                    if not dry_run:
                        try:
                            channel_id = int(channel_id_str)
                            await store.set_last_summary(
                                guild_id_str, channel_id, edition, timestamp
                            )
                            migrated_summary_tracking += 1
                            logger.info(
                                "    ✓ Migrated summary tracking for channel %s, edition %s",
                                channel_id, edition
                            )
                        except (ValueError, TypeError) as e:
                            logger.warning(
                                "    ✗ Failed to migrate summary tracking for channel %s: %s",
                                channel_id_str, e
                            )

    # Migrate pending articles from pending_news.json
    logger.info("Migrating pending articles from pending_news.json...")
    pending_by_channel = get_all_pending_by_channel()

    for channel_id, channel_data in pending_by_channel.items():
        guild_id_str = channel_data.get("guild_id")
        articles_by_feed = channel_data.get("articles", {})

        if not guild_id_str:
            logger.warning("  Channel %s has no guild_id - skipping", channel_id)
            continue

        for feed_name, articles in articles_by_feed.items():
            total_pending_articles += len(articles)

            logger.info(
                "  Channel %s, feed '%s': %d pending articles",
                channel_id, feed_name, len(articles)
            )

            if not dry_run and articles:
                length = await store.add_pending(
                    guild_id_str, channel_id, feed_name, articles
                )
                migrated_pending_articles += len(articles)
                logger.info(
                    "    ✓ Migrated %d pending articles to Redis (queue length: %d)",
                    len(articles), length
                )

    # Print summary
    logger.info("")
    logger.info("=" * 60)
    logger.info("Migration Summary")
    logger.info("=" * 60)
    logger.info("Total RSS feeds:          %d", total_feeds)
    logger.info("Total seen items:         %d", total_seen_items)
    logger.info("Total pending articles:   %d", total_pending_articles)
    logger.info("Total summary tracking:   %d", total_summary_tracking)

    if dry_run:
        logger.info("")
        logger.info("DRY RUN - No changes made")
        logger.info("Run without --dry-run to perform migration")
    else:
        logger.info("")
        logger.info("Migrated feeds:           %d", migrated_feeds)
        logger.info("Migrated seen items:      %d", migrated_seen_items)
        logger.info("Migrated pending articles:%d", migrated_pending_articles)
        logger.info("Migrated summary tracking:%d", migrated_summary_tracking)
        logger.info("")

        # Validate migration
        logger.info("Validating migration...")
        validation_errors = []

        for guild_id_str, guild_state in all_guild_states.items():
            if guild_id_str == "global":
                continue

            if not isinstance(guild_state, dict):
                continue

            # Check feeds
            feeds_json = guild_state.get("rss_feeds", {})
            feeds_redis = await store.get_feeds(guild_id_str)

            if len(feeds_json) != len(feeds_redis):
                validation_errors.append(
                    f"Guild {guild_id_str}: Feed count mismatch "
                    f"(JSON={len(feeds_json)}, Redis={len(feeds_redis)})"
                )

            # Check seen items for each feed
            for feed_name, feed_config in feeds_json.items():
                seen_items_json = feed_config.get("seen_items", [])
                seen_count_redis = await store.get_seen_count(guild_id_str, feed_name)

                if len(seen_items_json) != seen_count_redis:
                    validation_errors.append(
                        f"Feed '{feed_name}': Seen items count mismatch "
                        f"(JSON={len(seen_items_json)}, Redis={seen_count_redis})"
                    )

        # Check pending articles
        for channel_id, channel_data in pending_by_channel.items():
            guild_id_str = channel_data.get("guild_id")
            if not guild_id_str:
                continue

            articles_by_feed_json = channel_data.get("articles", {})
            articles_by_feed_redis = await store.get_pending(guild_id_str, channel_id)

            total_json = sum(len(articles) for articles in articles_by_feed_json.values())
            total_redis = sum(len(articles) for articles in articles_by_feed_redis.values())

            if total_json != total_redis:
                validation_errors.append(
                    f"Channel {channel_id}: Pending articles count mismatch "
                    f"(JSON={total_json}, Redis={total_redis})"
                )

        if validation_errors:
            logger.error("Validation FAILED:")
            for error in validation_errors:
                logger.error("  - %s", error)
            logger.error("")
            logger.error("Migration may be incomplete - keeping JSON data")
        else:
            logger.info("✓ Validation PASSED - All data migrated successfully")
            logger.info("")

            if delete_json:
                logger.warning("Deleting JSON data as requested...")
                for guild_id_str, guild_state in all_guild_states.items():
                    if guild_id_str == "global":
                        continue
                    if not isinstance(guild_state, dict):
                        continue

                    # Clear RSS feeds
                    set_state_value("rss_feeds", {}, guild_id_str)

                    # Clear channel summary tracking
                    set_state_value("channel_last_summaries", {}, guild_id_str)

                # Clear pending articles from pending_news.json
                for channel_id in pending_by_channel.keys():
                    # We need guild_id to clear, but we'll clear all by channel
                    guild_id_str = pending_by_channel[channel_id].get("guild_id")
                    if guild_id_str:
                        try:
                            guild_id = int(guild_id_str)
                            clear_pending_articles_for_channel(guild_id, channel_id)
                        except (ValueError, TypeError):
                            logger.warning("Could not clear pending for channel %s", channel_id)

                logger.warning("✓ Deleted JSON RSS data")
            else:
                logger.info("Keeping JSON data as backup (use --delete to remove)")

    logger.info("=" * 60)

    await close_redis()


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Migrate RSS feed data from JSON to Redis"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be migrated without making changes"
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete JSON data after successful migration (default: keep as backup)"
    )

    args = parser.parse_args()

    asyncio.run(migrate_rss_data(
        dry_run=args.dry_run,
        delete_json=args.delete
    ))


if __name__ == "__main__":
    main()
