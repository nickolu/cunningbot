"""Statistics and leaderboard calculations for trivia games."""

from collections import defaultdict
from typing import Dict, List, Tuple, Optional
import datetime as dt

from bot.domain.trivia.question_seeds import CATEGORIES
from bot.app.commands.trivia.trivia_constants import LEGACY_CATEGORY_MAP


class TriviaStatsService:
    """Service for calculating trivia statistics and leaderboards."""

    @staticmethod
    def calculate_leaderboard(
        trivia_history: Dict,
        category: Optional[str] = None,
        days: Optional[int] = None,
        since: Optional[dt.datetime] = None,
        until: Optional[dt.datetime] = None,
    ) -> List[Tuple[str, int, int, int, float]]:
        """
        Calculate leaderboard from trivia history.

        Args:
            trivia_history: Dictionary of completed games
            category: Optional category filter
            days: Optional number of days to look back (None = all time)
            since: Optional explicit cutoff datetime (takes precedence over days)
            until: Optional exclusive upper bound datetime (games ending on or after this are excluded)

        Returns:
            List of (user_id, points, correct_count, total_count, accuracy) tuples,
            sorted by points (desc), then accuracy (desc)
        """
        user_stats = defaultdict(lambda: {"correct": 0, "total": 0, "points": 0})

        cutoff_date = since
        if cutoff_date is None and days:
            cutoff_date = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)

        for game_id, game in trivia_history.items():
            # Filter by category if specified
            if category:
                game_category = game.get("category", "")
                game_category = LEGACY_CATEGORY_MAP.get(game_category, game_category)
                if game_category != category:
                    continue

            # Filter by date if specified
            ended_at_str = game.get("ended_at")
            if cutoff_date or until:
                if ended_at_str:
                    try:
                        game_date = dt.datetime.fromisoformat(ended_at_str)
                        if cutoff_date and game_date < cutoff_date:
                            continue
                        if until and game_date >= until:
                            continue
                    except (ValueError, TypeError):
                        pass  # Skip if date parsing fails

            # Check if this is a batch game or single question game
            is_batch = "question_count" in game

            if is_batch:
                # Batch game: submissions have multiple answers
                for user_id, submission in game.get("submissions", {}).items():
                    # New format with points
                    points = submission.get("points")
                    correct = submission.get("correct_count", 0)
                    total = submission.get("total_count", 0)

                    if points is None:
                        # Backward compatibility: estimate from score string
                        score_str = submission.get("score", "0/0")
                        try:
                            correct = int(score_str.split("/")[0])
                            total = int(score_str.split("/")[1])
                        except (ValueError, IndexError):
                            correct = 0
                            total = 0
                        points = (correct * 15) + ((total - correct) * 5)

                    user_stats[user_id]["total"] += total
                    user_stats[user_id]["correct"] += correct
                    user_stats[user_id]["points"] += points
            else:
                # Single question game
                for user_id, submission in game.get("submissions", {}).items():
                    user_stats[user_id]["total"] += 1
                    is_correct = submission.get("is_correct", False)
                    points = submission.get("points")

                    if points is None:
                        # Backward compatibility
                        difficulty = game.get("difficulty", "medium")
                        source = game.get("source", "opentdb")
                        # Inline the points calculation logic
                        if not is_correct:
                            points = 5
                        elif source == "ai":
                            points = 15
                        elif difficulty.lower() == "easy":
                            points = 10
                        elif difficulty.lower() == "hard":
                            points = 20
                        else:
                            points = 15

                    if is_correct:
                        user_stats[user_id]["correct"] += 1
                    user_stats[user_id]["points"] += points

        # Convert to sorted list
        leaderboard = [
            (
                user_id,
                stats["points"],
                stats["correct"],
                stats["total"],
                stats["correct"] / stats["total"] if stats["total"] > 0 else 0
            )
            for user_id, stats in user_stats.items()
        ]

        # Sort by points (desc), then by accuracy (desc)
        leaderboard.sort(key=lambda x: (x[1], x[4]), reverse=True)

        return leaderboard

    @staticmethod
    def calculate_user_stats(
        trivia_history: Dict,
        user_id: str
    ) -> Dict:
        """
        Calculate detailed stats for a specific user.

        Args:
            trivia_history: Dictionary of completed games
            user_id: User ID to calculate stats for

        Returns:
            dict: {
                "total_games": int,
                "correct_answers": int,
                "total_answers": int,
                "accuracy": float,
                "total_points": int,
                "avg_points_per_game": float,
                "by_category": {
                    "History": {"correct": int, "total": int, "points": int, "accuracy": float},
                    ...
                },
                "recent_games": [
                    {
                        "question": str,
                        "user_answer": str,
                        "correct_answer": str,
                        "is_correct": bool,
                        "category": str,
                        "date": str
                    }
                ]
            }
        """
        total_games = 0
        total_answers = 0
        correct_answers = 0
        total_points = 0
        by_category = {cat: {"correct": 0, "total": 0, "points": 0} for cat in CATEGORIES}
        recent_games = []

        # Sort games by date (most recent first)
        sorted_games = sorted(
            trivia_history.items(),
            key=lambda x: x[1].get("ended_at", ""),
            reverse=True
        )

        for game_id, game in sorted_games:
            submission = game.get("submissions", {}).get(user_id)
            if not submission:
                continue

            # Check if this is a batch game or single question game
            is_batch = "question_count" in game

            if is_batch:
                # Batch game: submissions have multiple answers
                points = submission.get("points")
                correct = submission.get("correct_count", 0)
                total = submission.get("total_count", 0)

                if points is None:
                    # Backward compatibility: estimate from score string
                    score_str = submission.get("score", "0/0")
                    try:
                        correct = int(score_str.split("/")[0])
                        total = int(score_str.split("/")[1])
                    except (ValueError, IndexError):
                        correct = 0
                        total = 0
                    points = (correct * 15) + ((total - correct) * 5)

                total_games += 1
                total_answers += total
                correct_answers += correct
                total_points += points

                # Track by category
                category = game.get("category", "Unknown")
                category = LEGACY_CATEGORY_MAP.get(category, category)
                if category in by_category:
                    by_category[category]["total"] += total
                    by_category[category]["correct"] += correct
                    by_category[category]["points"] += points
            else:
                # Single question game
                is_correct = submission.get("is_correct", False)
                points = submission.get("points")

                if points is None:
                    # Backward compatibility
                    difficulty = game.get("difficulty", "medium")
                    source = game.get("source", "opentdb")
                    # Inline the points calculation logic
                    if not is_correct:
                        points = 5
                    elif source == "ai":
                        points = 15
                    elif difficulty.lower() == "easy":
                        points = 10
                    elif difficulty.lower() == "hard":
                        points = 20
                    else:
                        points = 15

                total_games += 1
                total_answers += 1
                total_points += points

                if is_correct:
                    correct_answers += 1

                # Track by category
                category = game.get("category", "Unknown")
                category = LEGACY_CATEGORY_MAP.get(category, category)
                if category in by_category:
                    by_category[category]["total"] += 1
                    by_category[category]["points"] += points
                    if is_correct:
                        by_category[category]["correct"] += 1

            # Track recent games (limit to 10)
            if len(recent_games) < 10:
                recent_games.append({
                    "question": game.get("question", "Unknown"),
                    "user_answer": submission.get("answer", ""),
                    "correct_answer": game.get("correct_answer", ""),
                    "is_correct": is_correct if not is_batch else (correct > 0),
                    "category": category,
                    "date": game.get("ended_at", "")
                })

        # Calculate accuracy per category
        for category in by_category:
            total = by_category[category]["total"]
            by_category[category]["accuracy"] = (
                by_category[category]["correct"] / total if total > 0 else 0
            )

        return {
            "total_games": total_games,
            "total_answers": total_answers,
            "correct_answers": correct_answers,
            "accuracy": correct_answers / total_answers if total_answers > 0 else 0,
            "total_points": total_points,
            "avg_points_per_game": total_points / total_games if total_games > 0 else 0,
            "by_category": by_category,
            "recent_games": recent_games
        }

    @staticmethod
    def calculate_alltime_leaderboard(snapshots: List[dict]) -> List[dict]:
        """Aggregate all weekly snapshots into lifetime stats per player.

        Args:
            snapshots: List of weekly snapshot dicts (from get_all_weekly_snapshots)

        Returns:
            List of dicts sorted by total_points descending:
            {user_id, username, total_points, weekly_wins, avg_rank, weeks_played}
        """
        player_stats: Dict[str, dict] = {}

        for snapshot in snapshots:
            rankings = snapshot.get("rankings", [])
            for entry in rankings:
                user_id = entry.get("user_id")
                if not user_id:
                    continue

                if user_id not in player_stats:
                    player_stats[user_id] = {
                        "user_id": user_id,
                        "username": entry.get("username", f"User {user_id}"),
                        "total_points": 0,
                        "weekly_wins": 0,
                        "rank_sum": 0,
                        "weeks_played": 0,
                    }

                stats = player_stats[user_id]
                stats["total_points"] += entry.get("points", 0)
                stats["weeks_played"] += 1
                rank = entry.get("rank", 999)
                stats["rank_sum"] += rank
                if rank == 1:
                    stats["weekly_wins"] += 1
                # Update username in case it changed
                stats["username"] = entry.get("username", stats["username"])

        result = []
        for stats in player_stats.values():
            weeks = stats["weeks_played"]
            result.append({
                "user_id": stats["user_id"],
                "username": stats["username"],
                "total_points": stats["total_points"],
                "weekly_wins": stats["weekly_wins"],
                "avg_rank": round(stats["rank_sum"] / weeks, 1) if weeks > 0 else 0,
                "weeks_played": weeks,
            })

        result.sort(key=lambda x: x["total_points"], reverse=True)
        return result
