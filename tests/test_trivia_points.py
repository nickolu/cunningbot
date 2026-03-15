"""Test trivia point calculation system."""

import pytest
from bot.app.commands.trivia.trivia_submission_handler import calculate_question_points


class TestPointCalculation:
    """Test the point calculation function."""

    def test_wrong_answer_returns_5_points(self):
        """Wrong answers should always return 5 participation points."""
        assert calculate_question_points(False, "easy", "opentdb") == 5
        assert calculate_question_points(False, "medium", "opentdb") == 5
        assert calculate_question_points(False, "hard", "opentdb") == 5
        assert calculate_question_points(False, "", "ai") == 5

    def test_easy_correct_answer_returns_10_points(self):
        """Easy correct answers should return 10 points."""
        assert calculate_question_points(True, "easy", "opentdb") == 10
        assert calculate_question_points(True, "Easy", "opentdb") == 10
        assert calculate_question_points(True, "EASY", "opentdb") == 10

    def test_medium_correct_answer_returns_15_points(self):
        """Medium correct answers should return 15 points."""
        assert calculate_question_points(True, "medium", "opentdb") == 15
        assert calculate_question_points(True, "Medium", "opentdb") == 15
        assert calculate_question_points(True, "MEDIUM", "opentdb") == 15

    def test_hard_correct_answer_returns_20_points(self):
        """Hard correct answers should return 20 points."""
        assert calculate_question_points(True, "hard", "opentdb") == 20
        assert calculate_question_points(True, "Hard", "opentdb") == 20
        assert calculate_question_points(True, "HARD", "opentdb") == 20

    def test_ai_correct_answer_returns_15_points(self):
        """AI correct answers should return 15 points (medium equivalent)."""
        assert calculate_question_points(True, "", "ai") == 15
        assert calculate_question_points(True, "easy", "ai") == 15
        assert calculate_question_points(True, "hard", "ai") == 15

    def test_unknown_difficulty_returns_15_points(self):
        """Unknown difficulty should default to 15 points."""
        assert calculate_question_points(True, "", "opentdb") == 15
        assert calculate_question_points(True, "unknown", "opentdb") == 15
        assert calculate_question_points(True, None, "opentdb") == 15


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
