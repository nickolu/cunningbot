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
