"""migrate_trivia.py

One-time migration script to move active trivia games from JSON to Redis.

This script:
1. Reads all active_trivia_games from app_state.json
2. Creates each game in Redis
3. Migrates all submissions to Redis
4. Validates migration was successful
5. Optionally backs up JSON data

Usage (inside Docker container):
    python -m bot.app.redis.migrations.migrate_trivia [--dry-run] [--backup]

Options:
    --dry-run    Show what would be migrated without making changes
    --backup     Keep JSON data after migration (default: keep as backup)
    --delete     Remove JSON data after successful migration (use with caution)
"""
import asyncio
import argparse
import json
import logging
from datetime import datetime
from typing import Dict, Any

from bot.app.app_state import get_all_guild_states, set_state_value
from bot.app.redis.trivia_store import TriviaRedisStore
from bot.app.redis.client import initialize_redis, close_redis

logger = logging.getLogger("TriviaMigration")
logging.basicConfig(level=logging.INFO)


async def migrate_trivia_games(dry_run: bool = False, delete_json: bool = False) -> None:
    """Migrate active trivia games from JSON to Redis.

    Args:
        dry_run: If True, only show what would be migrated
        delete_json: If True, remove JSON data after successful migration
    """
    logger.info("Starting trivia migration (dry_run=%s)", dry_run)

    # Initialize Redis
    await initialize_redis()
    store = TriviaRedisStore()

    # Get all guild states from JSON
    all_guild_states = get_all_guild_states()
    if not all_guild_states:
        logger.info("No guild states found - nothing to migrate")
        await close_redis()
        return

    total_games = 0
    total_submissions = 0
    total_registrations = 0
    migrated_games = 0
    migrated_submissions = 0
    migrated_registrations = 0

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

        # Migrate active games
        active_games = guild_state.get("active_trivia_games", {})
        total_games += len(active_games)

        for game_id, game_data in active_games.items():
            logger.info(
                "  Game %s: question='%s...', ends_at=%s",
                game_id[:8],
                game_data.get("question", "")[:50],
                game_data.get("ends_at", "unknown")
            )

            # Extract submissions before creating game
            submissions = game_data.get("submissions", {})
            total_submissions += len(submissions)

            if not dry_run:
                # Create game in Redis (submissions field will be ignored)
                game_data_copy = game_data.copy()
                if "submissions" in game_data_copy:
                    del game_data_copy["submissions"]  # Submissions stored separately

                await store.create_game(guild_id_str, game_id, game_data_copy)
                migrated_games += 1
                logger.info("    ✓ Migrated game %s", game_id[:8])

                # Create submissions in Redis
                for user_id, submission_data in submissions.items():
                    await store.update_submission(
                        guild_id_str, game_id, user_id, submission_data
                    )
                    migrated_submissions += 1

                if submissions:
                    logger.info(
                        "    ✓ Migrated %d submission(s) for game %s",
                        len(submissions), game_id[:8]
                    )

        # Migrate registrations
        registrations = guild_state.get("trivia_registrations", {})
        total_registrations += len(registrations)

        for reg_id, reg_data in registrations.items():
            logger.info(
                "  Registration %s: channel=%s, schedule=%s",
                reg_id[:8],
                reg_data.get("channel_id", "unknown"),
                reg_data.get("schedule_times", [])
            )

            if not dry_run:
                await store.save_registration(guild_id_str, reg_id, reg_data)
                migrated_registrations += 1
                logger.info("    ✓ Migrated registration %s", reg_id[:8])

    # Print summary
    logger.info("")
    logger.info("=" * 60)
    logger.info("Migration Summary")
    logger.info("=" * 60)
    logger.info("Total active games:      %d", total_games)
    logger.info("Total submissions:       %d", total_submissions)
    logger.info("Total registrations:     %d", total_registrations)

    if dry_run:
        logger.info("")
        logger.info("DRY RUN - No changes made")
        logger.info("Run without --dry-run to perform migration")
    else:
        logger.info("")
        logger.info("Migrated games:          %d", migrated_games)
        logger.info("Migrated submissions:    %d", migrated_submissions)
        logger.info("Migrated registrations:  %d", migrated_registrations)
        logger.info("")

        # Validate migration
        logger.info("Validating migration...")
        validation_errors = []

        for guild_id_str, guild_state in all_guild_states.items():
            if guild_id_str == "global":
                continue

            if not isinstance(guild_state, dict):
                continue

            # Check games
            active_games_json = guild_state.get("active_trivia_games", {})
            active_games_redis = await store.get_active_games(guild_id_str)

            if len(active_games_json) != len(active_games_redis):
                validation_errors.append(
                    f"Guild {guild_id_str}: Game count mismatch "
                    f"(JSON={len(active_games_json)}, Redis={len(active_games_redis)})"
                )

            # Check submissions for each game
            for game_id in active_games_json.keys():
                submissions_json = active_games_json[game_id].get("submissions", {})
                submissions_redis = await store.get_submissions(guild_id_str, game_id)

                if len(submissions_json) != len(submissions_redis):
                    validation_errors.append(
                        f"Game {game_id[:8]}: Submission count mismatch "
                        f"(JSON={len(submissions_json)}, Redis={len(submissions_redis)})"
                    )

            # Check registrations
            registrations_json = guild_state.get("trivia_registrations", {})
            registrations_redis = await store.get_registrations(guild_id_str)

            if len(registrations_json) != len(registrations_redis):
                validation_errors.append(
                    f"Guild {guild_id_str}: Registration count mismatch "
                    f"(JSON={len(registrations_json)}, Redis={len(registrations_redis)})"
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

                    # Clear active games and registrations
                    set_state_value("active_trivia_games", {}, guild_id_str)
                    set_state_value("trivia_registrations", {}, guild_id_str)

                logger.warning("✓ Deleted JSON trivia data")
            else:
                logger.info("Keeping JSON data as backup (use --delete to remove)")

    logger.info("=" * 60)

    await close_redis()


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Migrate trivia games from JSON to Redis"
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

    asyncio.run(migrate_trivia_games(
        dry_run=args.dry_run,
        delete_json=args.delete
    ))


if __name__ == "__main__":
    main()
