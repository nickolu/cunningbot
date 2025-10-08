"""Unit tests for DailyGameStatsService."""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, MagicMock
from bot.domain.daily_game.daily_game_stats_service import DailyGameStatsService
from bot.api.discord.thread_analyzer import GameStatsResult, DailyGameParticipation


class TestGetDefaultDateRange:
    """Tests for get_default_date_range method."""

    def test_returns_30_day_range(self) -> None:
        """Test that default range is 30 days."""
        start_date, end_date = DailyGameStatsService.get_default_date_range()

        # Calculate the difference
        days_diff = (end_date - start_date).days

        # Should be 29 days difference (30 days total including both start and end)
        assert days_diff == 29

    def test_start_date_at_midnight(self) -> None:
        """Test that start date is set to midnight."""
        start_date, _ = DailyGameStatsService.get_default_date_range()

        assert start_date.hour == 0
        assert start_date.minute == 0
        assert start_date.second == 0
        assert start_date.microsecond == 0

    def test_dates_in_utc(self) -> None:
        """Test that dates are in UTC timezone."""
        start_date, end_date = DailyGameStatsService.get_default_date_range()

        assert start_date.tzinfo == timezone.utc
        assert end_date.tzinfo == timezone.utc

    def test_end_date_is_now(self) -> None:
        """Test that end date is approximately now."""
        _, end_date = DailyGameStatsService.get_default_date_range()
        now = datetime.now(timezone.utc)

        # End date should be within 1 second of now
        diff = abs((now - end_date).total_seconds())
        assert diff < 1


class TestParseUtcTimestamp:
    """Tests for parse_utc_timestamp method."""

    def test_parse_unix_timestamp(self) -> None:
        """Test parsing Unix timestamp."""
        # January 1, 2024, 00:00:00 UTC
        timestamp = "1704067200"
        result = DailyGameStatsService.parse_utc_timestamp(timestamp)

        assert result.year == 2024
        assert result.month == 1
        assert result.day == 1
        assert result.tzinfo == timezone.utc

    def test_parse_iso_format_with_z(self) -> None:
        """Test parsing ISO format with Z suffix."""
        timestamp = "2024-01-15T12:30:00Z"
        result = DailyGameStatsService.parse_utc_timestamp(timestamp)

        assert result.year == 2024
        assert result.month == 1
        assert result.day == 15
        assert result.hour == 12
        assert result.minute == 30
        assert result.tzinfo == timezone.utc

    def test_parse_iso_format_with_timezone(self) -> None:
        """Test parsing ISO format with timezone offset."""
        timestamp = "2024-03-20T15:45:00+00:00"
        result = DailyGameStatsService.parse_utc_timestamp(timestamp)

        assert result.year == 2024
        assert result.month == 3
        assert result.day == 20
        assert result.tzinfo is not None

    def test_parse_iso_format_without_timezone(self) -> None:
        """Test parsing ISO format without timezone adds UTC."""
        timestamp = "2024-06-10T10:00:00"
        result = DailyGameStatsService.parse_utc_timestamp(timestamp)

        assert result.tzinfo == timezone.utc

    def test_invalid_timestamp_raises_error(self) -> None:
        """Test that invalid timestamp raises ValueError."""
        with pytest.raises(ValueError, match="Invalid timestamp format"):
            DailyGameStatsService.parse_utc_timestamp("not-a-timestamp")

    def test_parse_float_timestamp(self) -> None:
        """Test parsing float Unix timestamp."""
        timestamp = "1704067200.5"
        result = DailyGameStatsService.parse_utc_timestamp(timestamp)

        assert result.year == 2024
        assert result.month == 1


class TestFormatStatsResponse:
    """Tests for format_stats_response method."""

    def test_format_response_with_no_participation(self) -> None:
        """Test formatting response when there's no participation data."""
        start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end_date = datetime(2024, 1, 30, tzinfo=timezone.utc)

        stats = GameStatsResult(
            game_name="Wordle",
            start_date=start_date,
            end_date=end_date,
            daily_participation=[],
            total_days_in_range=0
        )

        mock_bot = Mock()
        result = DailyGameStatsService.format_stats_response(stats, mock_bot)

        assert "Wordle" in result
        assert "01/01/2024" in result
        assert "01/30/2024" in result
        assert "No participation data found" in result

    def test_format_response_with_single_user(self) -> None:
        """Test formatting response with single user participation."""
        start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end_date = datetime(2024, 1, 5, tzinfo=timezone.utc)

        daily_participation = [
            DailyGameParticipation(
                date=datetime(2024, 1, 1, tzinfo=timezone.utc),
                participants={"123456"}
            ),
            DailyGameParticipation(
                date=datetime(2024, 1, 2, tzinfo=timezone.utc),
                participants={"123456"}
            ),
            DailyGameParticipation(
                date=datetime(2024, 1, 3, tzinfo=timezone.utc),
                participants=set()
            ),
        ]

        stats = GameStatsResult(
            game_name="Wordle",
            start_date=start_date,
            end_date=end_date,
            daily_participation=daily_participation,
            total_days_in_range=3
        )

        # Mock bot user lookup
        mock_user = Mock()
        mock_user.mention = "<@123456>"
        mock_bot = Mock()
        mock_bot.get_user.return_value = mock_user

        result = DailyGameStatsService.format_stats_response(stats, mock_bot)

        assert "Wordle" in result
        assert "<@123456>" in result
        assert "played 2/3 days (67%)" in result
        assert "Date | Users Who Played" in result

    def test_format_response_with_multiple_users(self) -> None:
        """Test formatting response with multiple users."""
        start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end_date = datetime(2024, 1, 3, tzinfo=timezone.utc)

        daily_participation = [
            DailyGameParticipation(
                date=datetime(2024, 1, 1, tzinfo=timezone.utc),
                participants={"111", "222"}
            ),
            DailyGameParticipation(
                date=datetime(2024, 1, 2, tzinfo=timezone.utc),
                participants={"111", "222", "333"}
            ),
            DailyGameParticipation(
                date=datetime(2024, 1, 3, tzinfo=timezone.utc),
                participants={"222"}
            ),
        ]

        stats = GameStatsResult(
            game_name="Wordle",
            start_date=start_date,
            end_date=end_date,
            daily_participation=daily_participation,
            total_days_in_range=3
        )

        # Mock bot user lookup
        def get_user_mock(user_id: int) -> Mock:
            mock = Mock()
            mock.mention = f"<@{user_id}>"
            return mock

        mock_bot = Mock()
        mock_bot.get_user.side_effect = get_user_mock

        result = DailyGameStatsService.format_stats_response(stats, mock_bot)

        # User 222 participated all 3 days (100%)
        assert "played 3/3 days (100%)" in result
        # User 111 participated 2 days (67%)
        assert "played 2/3 days (67%)" in result
        # User 333 participated 1 day (33%)
        assert "played 1/3 days (33%)" in result

    def test_format_response_sorts_by_participation(self) -> None:
        """Test that users are sorted by participation count."""
        start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end_date = datetime(2024, 1, 3, tzinfo=timezone.utc)

        daily_participation = [
            DailyGameParticipation(
                date=datetime(2024, 1, 1, tzinfo=timezone.utc),
                participants={"high", "medium", "low"}
            ),
            DailyGameParticipation(
                date=datetime(2024, 1, 2, tzinfo=timezone.utc),
                participants={"high", "medium"}
            ),
            DailyGameParticipation(
                date=datetime(2024, 1, 3, tzinfo=timezone.utc),
                participants={"high"}
            ),
        ]

        stats = GameStatsResult(
            game_name="Test",
            start_date=start_date,
            end_date=end_date,
            daily_participation=daily_participation,
            total_days_in_range=3
        )

        mock_bot = Mock()
        mock_bot.get_user.return_value = None

        result = DailyGameStatsService.format_stats_response(stats, mock_bot)
        lines = result.split("\n")

        # Find participation summary lines
        participation_lines = [l for l in lines if "played" in l]

        # "high" with 3 days should appear before "medium" with 2 days and "low" with 1 day
        high_idx = next(i for i, l in enumerate(participation_lines) if "high" in l)
        medium_idx = next(i for i, l in enumerate(participation_lines) if "medium" in l)
        low_idx = next(i for i, l in enumerate(participation_lines) if "low" in l)

        assert high_idx < medium_idx
        assert medium_idx < low_idx

    def test_format_response_shows_no_participants_for_empty_day(self) -> None:
        """Test that days with no participants show appropriate message."""
        start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end_date = datetime(2024, 1, 2, tzinfo=timezone.utc)

        daily_participation = [
            DailyGameParticipation(
                date=datetime(2024, 1, 1, tzinfo=timezone.utc),
                participants=set()
            ),
        ]

        stats = GameStatsResult(
            game_name="Test",
            start_date=start_date,
            end_date=end_date,
            daily_participation=daily_participation,
            total_days_in_range=1
        )

        mock_bot = Mock()
        result = DailyGameStatsService.format_stats_response(stats, mock_bot)

        assert "*No participants*" in result

    def test_format_response_handles_user_not_found(self) -> None:
        """Test that response handles case when bot can't find user."""
        start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end_date = datetime(2024, 1, 1, tzinfo=timezone.utc)

        daily_participation = [
            DailyGameParticipation(
                date=datetime(2024, 1, 1, tzinfo=timezone.utc),
                participants={"999999"}
            ),
        ]

        stats = GameStatsResult(
            game_name="Test",
            start_date=start_date,
            end_date=end_date,
            daily_participation=daily_participation,
            total_days_in_range=1
        )

        # Bot returns None for unknown user
        mock_bot = Mock()
        mock_bot.get_user.return_value = None

        result = DailyGameStatsService.format_stats_response(stats, mock_bot)

        # Should fall back to user ID mention format
        assert "<@999999>" in result


class TestValidateDateRange:
    """Tests for validate_date_range method."""

    def test_valid_date_range(self) -> None:
        """Test that valid date range doesn't raise error."""
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 31, tzinfo=timezone.utc)

        # Should not raise
        DailyGameStatsService.validate_date_range(start, end)

    def test_start_after_end_raises_error(self) -> None:
        """Test that start date after end date raises error."""
        start = datetime(2024, 2, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 1, tzinfo=timezone.utc)

        with pytest.raises(ValueError, match="Start date must be before end date"):
            DailyGameStatsService.validate_date_range(start, end)

    def test_start_equal_to_end_raises_error(self) -> None:
        """Test that start date equal to end date raises error."""
        date = datetime(2024, 1, 1, tzinfo=timezone.utc)

        with pytest.raises(ValueError, match="Start date must be before end date"):
            DailyGameStatsService.validate_date_range(date, date)

    def test_range_exceeds_max_days(self) -> None:
        """Test that date range exceeding 365 days raises error."""
        start = datetime(2023, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 2, 1, tzinfo=timezone.utc)  # More than 365 days

        with pytest.raises(ValueError, match="Date range cannot exceed 365 days"):
            DailyGameStatsService.validate_date_range(start, end)

    def test_future_start_date_raises_error(self) -> None:
        """Test that future start date raises error."""
        now = datetime.now(timezone.utc)
        start = now + timedelta(days=1)
        end = now + timedelta(days=2)

        with pytest.raises(ValueError, match="Start date cannot be in the future"):
            DailyGameStatsService.validate_date_range(start, end)

    def test_future_end_date_raises_error(self) -> None:
        """Test that future end date raises error."""
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=10)
        end = now + timedelta(days=1)

        with pytest.raises(ValueError, match="End date cannot be in the future"):
            DailyGameStatsService.validate_date_range(start, end)

    def test_exactly_365_days_is_valid(self) -> None:
        """Test that exactly 365 days is valid."""
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2025, 1, 1, tzinfo=timezone.utc)  # Exactly 365 days

        # Should not raise (but might raise due to future date check)
        # We'll use a past date range instead
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=365)
        end = now

        # Should not raise
        DailyGameStatsService.validate_date_range(start, end)

    def test_one_day_range_is_valid(self) -> None:
        """Test that a one-day range is valid."""
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=1)
        end = now

        # Should not raise
        DailyGameStatsService.validate_date_range(start, end)
