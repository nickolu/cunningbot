"""Domain service for daily game statistics.

This module contains business logic for processing and formatting daily game statistics.
"""

import discord
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Tuple
from bot.api.discord.thread_analyzer import GameStatsResult


class DailyGameStatsService:
    """Service for processing and formatting daily game statistics."""
    
    @staticmethod
    def get_default_date_range() -> Tuple[datetime, datetime]:
        """Get the default date range (last 30 days)."""
        end_date = datetime.now(timezone.utc).replace(hour=23, minute=59, second=59)
        start_date = end_date - timedelta(days=29)  # 30 days total including today
        start_date = start_date.replace(hour=0, minute=0, second=0)
        return start_date, end_date
    
    @staticmethod
    def parse_utc_timestamp(timestamp_str: str) -> datetime:
        """Parse a UTC timestamp string to datetime."""
        try:
            # Try parsing as Unix timestamp first
            timestamp = float(timestamp_str)
            return datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except ValueError:
            # Try parsing as ISO format
            try:
                dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                raise ValueError(f"Invalid timestamp format: {timestamp_str}")
    
    @staticmethod
    def format_stats_response(stats: GameStatsResult, bot: discord.Client) -> str:
        """
        Format the statistics result into a Discord message.
        
        Args:
            stats: The game statistics result
            bot: Discord bot client to get user information
            
        Returns:
            Formatted string for Discord message
        """
        if not stats.daily_participation:
            return (
                f"**Stats for \"{stats.game_name}\"**\n"
                f"({stats.start_date.strftime('%m/%d/%Y')}-{stats.end_date.strftime('%m/%d/%Y')})\n\n"
                f"No participation data found for this game in the specified date range."
            )
        
        # Build user participation summary
        user_stats = []
        all_participants = stats.all_participants
        
        for user_id in all_participants:
            participation_count = stats.get_user_participation_count(user_id)
            percentage = (participation_count / stats.total_days_in_range) * 100
            
            # Try to get user mention, fallback to ID if not found
            try:
                user = bot.get_user(int(user_id))
                user_mention = user.mention if user else f"<@{user_id}>"
            except (ValueError, AttributeError):
                user_mention = f"<@{user_id}>"
            
            user_stats.append((user_mention, participation_count, percentage))
        
        # Sort by participation count (descending), then by percentage
        user_stats.sort(key=lambda x: (-x[1], -x[2]))
        
        # Format the response
        response_parts = [
            f"**Stats for \"{stats.game_name}\"**",
            f"({stats.start_date.strftime('%m/%d/%Y')}-{stats.end_date.strftime('%m/%d/%Y')})",
            ""
        ]
        
        # Add user participation summary
        for user_mention, count, percentage in user_stats:
            response_parts.append(f"{user_mention}: played {count}/{stats.total_days_in_range} days ({percentage:.0f}%)")
        
        response_parts.append("")
        
        # Add daily breakdown
        response_parts.append("**Date | Users Who Played**")
        
        # Show most recent dates first
        for participation in reversed(stats.daily_participation):
            date_str = participation.date.strftime('%m/%d/%Y')
            if participation.participants:
                # Get user mentions for this day
                user_mentions = []
                for user_id in participation.participants:
                    try:
                        user = bot.get_user(int(user_id))
                        user_mention = user.mention if user else f"<@{user_id}>"
                    except (ValueError, AttributeError):
                        user_mention = f"<@{user_id}>"
                    user_mentions.append(user_mention)
                
                response_parts.append(f"{date_str}: {', '.join(user_mentions)}")
            else:
                response_parts.append(f"{date_str}: *No participants*")
        
        return "\n".join(response_parts)
    
    @staticmethod
    def validate_date_range(start_date: datetime, end_date: datetime) -> None:
        """
        Validate that the date range is reasonable.
        
        Raises:
            ValueError: If the date range is invalid
        """
        if start_date >= end_date:
            raise ValueError("Start date must be before end date")
        
        # Limit to reasonable range (e.g., 1 year)
        max_days = 365
        if (end_date - start_date).days > max_days:
            raise ValueError(f"Date range cannot exceed {max_days} days")
        
        # Don't allow future dates
        now = datetime.now(timezone.utc)
        if start_date > now:
            raise ValueError("Start date cannot be in the future")
        if end_date > now:
            raise ValueError("End date cannot be in the future") 