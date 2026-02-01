"""Redis storage layer for trivia games.

This module provides atomic operations for trivia game state management,
eliminating race conditions present in the JSON file-based approach.
"""

import json
import logging
from typing import Any, Dict, Optional
from datetime import datetime

from bot.app.redis.client import get_redis_client
from bot.app.redis.serialization import guild_id_to_str

logger = logging.getLogger("TriviaRedisStore")


class TriviaRedisStore:
    """Redis storage operations for trivia games."""

    def __init__(self):
        self.redis_client = get_redis_client()
        self.redis = self.redis_client.redis

    # --- Active Games ---

    async def get_active_games(self, guild_id: str) -> Dict[str, Dict[str, Any]]:
        """Get all active games for a guild.

        Args:
            guild_id: Guild ID as string

        Returns:
            Dictionary of game_id -> game_data
        """
        key = f"trivia:{guild_id}:games:active"
        games_hash = await self.redis.hgetall(key)

        result = {}
        for game_id, game_json in games_hash.items():
            try:
                result[game_id] = json.loads(game_json)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to decode game {game_id}: {e}")

        return result

    async def get_game(self, guild_id: str, game_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific active game.

        Args:
            guild_id: Guild ID as string
            game_id: Game ID

        Returns:
            Game data dictionary or None if not found
        """
        key = f"trivia:{guild_id}:games:active"
        game_json = await self.redis.hget(key, game_id)

        if not game_json:
            return None

        try:
            return json.loads(game_json)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode game {game_id}: {e}")
            return None

    async def create_game(
        self, guild_id: str, game_id: str, game_data: Dict[str, Any]
    ) -> None:
        """Create a new active game.

        Args:
            guild_id: Guild ID as string
            game_id: Unique game ID
            game_data: Game metadata (question, correct_answer, etc.)
        """
        key = f"trivia:{guild_id}:games:active"

        # Add ends_at_epoch for Lua script comparison
        if "ends_at" in game_data:
            try:
                ends_at_dt = datetime.fromisoformat(game_data["ends_at"])
                game_data["ends_at_epoch"] = ends_at_dt.timestamp()
            except (ValueError, TypeError) as e:
                logger.warning(f"Failed to parse ends_at: {e}")

        await self.redis.hset(key, game_id, json.dumps(game_data))
        logger.info(f"Created game {game_id[:8]} for guild {guild_id}")

    async def update_game(
        self, guild_id: str, game_id: str, game_data: Dict[str, Any]
    ) -> None:
        """Update an existing game.

        Args:
            guild_id: Guild ID as string
            game_id: Game ID
            game_data: Updated game data
        """
        key = f"trivia:{guild_id}:games:active"

        # Update ends_at_epoch if ends_at changed
        if "ends_at" in game_data:
            try:
                ends_at_dt = datetime.fromisoformat(game_data["ends_at"])
                game_data["ends_at_epoch"] = ends_at_dt.timestamp()
            except (ValueError, TypeError) as e:
                logger.warning(f"Failed to parse ends_at: {e}")

        await self.redis.hset(key, game_id, json.dumps(game_data))

    async def delete_game(self, guild_id: str, game_id: str) -> bool:
        """Delete an active game.

        Args:
            guild_id: Guild ID as string
            game_id: Game ID

        Returns:
            True if game was deleted, False if it didn't exist
        """
        key = f"trivia:{guild_id}:games:active"
        deleted = await self.redis.hdel(key, game_id)
        return deleted > 0

    # --- Submissions ---

    async def submit_answer_atomic(
        self,
        guild_id: str,
        game_id: str,
        user_id: str,
        submission_data: Dict[str, Any],
    ) -> Dict[str, str]:
        """Submit an answer atomically using Lua script.

        This prevents race conditions by executing entirely within Redis.

        Args:
            guild_id: Guild ID as string
            game_id: Game ID
            user_id: User ID as string
            submission_data: Submission details (answer, submitted_at, etc.)

        Returns:
            Dictionary with either {"ok": "SUBMITTED"} or {"err": "ERROR_CODE"}
            Error codes: GAME_NOT_FOUND, GAME_CLOSED, WINDOW_CLOSED
        """
        games_key = f"trivia:{guild_id}:games:active"
        submissions_key = f"trivia:{guild_id}:game:{game_id}:submissions"
        current_timestamp = datetime.now().timestamp()

        try:
            result = await self.redis_client.execute_script(
                "trivia_submit_answer",
                keys=[games_key, submissions_key],
                args=[
                    game_id,
                    user_id,
                    json.dumps(submission_data),
                    str(current_timestamp),
                ],
            )

            logger.info(f"Lua script result: {result} (type: {type(result)})")
            return result
        except Exception as e:
            logger.error(f"Failed to execute submission script: {e}")
            return {"err": "SCRIPT_ERROR"}

    async def get_submissions(
        self, guild_id: str, game_id: str
    ) -> Dict[str, Dict[str, Any]]:
        """Get all submissions for a game.

        Args:
            guild_id: Guild ID as string
            game_id: Game ID

        Returns:
            Dictionary of user_id -> submission_data
        """
        key = f"trivia:{guild_id}:game:{game_id}:submissions"
        submissions_hash = await self.redis.hgetall(key)

        result = {}
        for user_id, submission_json in submissions_hash.items():
            try:
                result[user_id] = json.loads(submission_json)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to decode submission for user {user_id}: {e}")

        return result

    async def update_submission(
        self,
        guild_id: str,
        game_id: str,
        user_id: str,
        submission_data: Dict[str, Any],
    ) -> None:
        """Update a submission (e.g., add validation results).

        Args:
            guild_id: Guild ID as string
            game_id: Game ID
            user_id: User ID as string
            submission_data: Updated submission data
        """
        key = f"trivia:{guild_id}:game:{game_id}:submissions"
        await self.redis.hset(key, user_id, json.dumps(submission_data))

    # --- Registrations ---

    async def get_registrations(self, guild_id: str) -> Dict[str, Dict[str, Any]]:
        """Get all trivia registrations for a guild.

        Args:
            guild_id: Guild ID as string

        Returns:
            Dictionary of registration_id -> registration_data
        """
        key = f"trivia:{guild_id}:registrations"
        regs_hash = await self.redis.hgetall(key)

        result = {}
        for reg_id, reg_json in regs_hash.items():
            try:
                result[reg_id] = json.loads(reg_json)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to decode registration {reg_id}: {e}")

        return result

    async def save_registration(
        self, guild_id: str, reg_id: str, reg_data: Dict[str, Any]
    ) -> None:
        """Save a trivia registration.

        Args:
            guild_id: Guild ID as string
            reg_id: Registration ID
            reg_data: Registration configuration
        """
        key = f"trivia:{guild_id}:registrations"
        await self.redis.hset(key, reg_id, json.dumps(reg_data))

    async def delete_registration(self, guild_id: str, reg_id: str) -> bool:
        """Delete a trivia registration.

        Args:
            guild_id: Guild ID as string
            reg_id: Registration ID

        Returns:
            True if deleted, False if didn't exist
        """
        key = f"trivia:{guild_id}:registrations"
        deleted = await self.redis.hdel(key, reg_id)
        return deleted > 0

    # --- Used Seeds (Redis Set for O(1) lookups and atomic adds) ---

    async def mark_seed_used(self, guild_id: str, seed: str) -> int:
        """Mark a question seed as used (atomic operation).

        Args:
            guild_id: Guild ID as string
            seed: Question seed to mark as used

        Returns:
            1 if seed was newly added, 0 if already existed
        """
        key = f"trivia:{guild_id}:seeds:used"
        added = await self.redis.sadd(key, seed)
        return added

    async def is_seed_used(self, guild_id: str, seed: str) -> bool:
        """Check if a question seed has been used before.

        Args:
            guild_id: Guild ID as string
            seed: Question seed to check

        Returns:
            True if seed was used before, False otherwise
        """
        key = f"trivia:{guild_id}:seeds:used"
        return await self.redis.sismember(key, seed)

    async def get_used_seeds(self, guild_id: str) -> set[str]:
        """Get all used question seeds for a guild.

        Args:
            guild_id: Guild ID as string

        Returns:
            Set of used seed strings
        """
        key = f"trivia:{guild_id}:seeds:used"
        seeds_set = await self.redis.smembers(key)
        return seeds_set

    async def get_used_seeds_count(self, guild_id: str) -> int:
        """Get count of used seeds.

        Args:
            guild_id: Guild ID as string

        Returns:
            Number of used seeds
        """
        key = f"trivia:{guild_id}:seeds:used"
        return await self.redis.scard(key)

    # --- History ---

    async def move_to_history(
        self,
        guild_id: str,
        game_id: str,
        game_data: Dict[str, Any],
        submissions: Dict[str, Dict[str, Any]],
    ) -> None:
        """Move a game to history (for completed games).

        Args:
            guild_id: Guild ID as string
            game_id: Game ID
            game_data: Game metadata
            submissions: All submissions
        """
        # Add to sorted set with timestamp
        ended_timestamp = datetime.now().timestamp()
        history_set_key = f"trivia:{guild_id}:games:history"
        await self.redis.zadd(history_set_key, {game_id: ended_timestamp})

        # Store full game history
        history_data = {
            "question": game_data.get("question"),
            "correct_answer": game_data.get("correct_answer"),
            "category": game_data.get("category"),
            "started_at": game_data.get("started_at"),
            "ended_at": game_data.get("closed_at", datetime.now().isoformat()),
            "submissions": submissions,
        }

        history_key = f"trivia:{guild_id}:game:{game_id}:history"
        # Store with 7-day TTL
        await self.redis.setex(history_key, 604800, json.dumps(history_data))

        logger.info(f"Moved game {game_id[:8]} to history")

    async def get_history(
        self, guild_id: str, limit: int = 10
    ) -> list[Dict[str, Any]]:
        """Get recent game history.

        Args:
            guild_id: Guild ID as string
            limit: Maximum number of games to return

        Returns:
            List of game history data, newest first
        """
        history_set_key = f"trivia:{guild_id}:games:history"

        # Get most recent game IDs
        game_ids = await self.redis.zrevrange(history_set_key, 0, limit - 1)

        result = []
        for game_id in game_ids:
            history_key = f"trivia:{guild_id}:game:{game_id}:history"
            history_json = await self.redis.get(history_key)

            if history_json:
                try:
                    result.append(json.loads(history_json))
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to decode history for {game_id}: {e}")

        return result

    async def get_all_history_as_dict(self, guild_id: str) -> Dict[str, Dict[str, Any]]:
        """Get all game history as a dictionary (for leaderboard calculations).

        Args:
            guild_id: Guild ID as string

        Returns:
            Dictionary of game_id -> game history data
        """
        history_set_key = f"trivia:{guild_id}:games:history"

        # Get all game IDs from the sorted set
        game_ids = await self.redis.zrevrange(history_set_key, 0, -1)

        result = {}
        for game_id in game_ids:
            history_key = f"trivia:{guild_id}:game:{game_id}:history"
            history_json = await self.redis.get(history_key)

            if history_json:
                try:
                    result[game_id] = json.loads(history_json)
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to decode history for {game_id}: {e}")

        return result

    # --- Bulk Operations ---

    async def clear_registrations_by_channel(self, guild_id: str, channel_id: int) -> int:
        """Clear all registrations for a specific channel.

        Args:
            guild_id: Guild ID as string
            channel_id: Channel ID to clear registrations for

        Returns:
            Number of registrations deleted
        """
        key = f"trivia:{guild_id}:registrations"
        registrations = await self.get_registrations(guild_id)

        deleted = 0
        for reg_id, reg_data in registrations.items():
            if reg_data.get("channel_id") == channel_id:
                await self.redis.hdel(key, reg_id)
                deleted += 1
                logger.info(f"Deleted registration {reg_id[:8]} for channel {channel_id}")

        return deleted

    async def clear_all_registrations(self, guild_id: str) -> int:
        """Clear all registrations for a guild.

        Args:
            guild_id: Guild ID as string

        Returns:
            Number of registrations deleted
        """
        key = f"trivia:{guild_id}:registrations"
        registrations = await self.get_registrations(guild_id)
        count = len(registrations)

        if count > 0:
            await self.redis.delete(key)
            logger.info(f"Deleted all {count} registrations for guild {guild_id}")

        return count

    async def clear_all_stats(self, guild_id: str) -> Dict[str, int]:
        """Clear all game history and stats for a guild.

        This removes all historical data but preserves active games and registrations.

        Args:
            guild_id: Guild ID as string

        Returns:
            Dictionary with counts of deleted items
        """
        # Get all game IDs from history sorted set
        history_set_key = f"trivia:{guild_id}:games:history"
        game_ids = await self.redis.zrevrange(history_set_key, 0, -1)

        deleted_games = 0
        deleted_submissions = 0

        # Delete each game's history and submissions
        for game_id in game_ids:
            history_key = f"trivia:{guild_id}:game:{game_id}:history"
            submissions_key = f"trivia:{guild_id}:game:{game_id}:submissions"

            await self.redis.delete(history_key)
            deleted_games += 1

            # Check if submissions exist before deleting
            if await self.redis.exists(submissions_key):
                await self.redis.delete(submissions_key)
                deleted_submissions += 1

        # Delete the history set itself
        await self.redis.delete(history_set_key)

        # Clear used seeds
        seeds_key = f"trivia:{guild_id}:seeds:used"
        seeds_count = await self.redis.scard(seeds_key)
        if seeds_count > 0:
            await self.redis.delete(seeds_key)

        logger.info(
            f"Cleared stats for guild {guild_id}: "
            f"{deleted_games} games, {deleted_submissions} submission sets, {seeds_count} seeds"
        )

        return {
            "games": deleted_games,
            "submissions": deleted_submissions,
            "seeds": seeds_count
        }
