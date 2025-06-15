"""Discord thread analysis utilities for daily game statistics.

This module contains Discord-specific operations for analyzing daily game threads
and extracting player participation data.
"""

import discord
import re
from datetime import datetime, timezone
from typing import Dict, List, Set, Optional, Tuple
from dataclasses import dataclass


@dataclass
class DailyGameParticipation:
    """Represents a single day's participation in a daily game."""
    date: datetime
    participants: Set[str]  # Discord user IDs as strings


@dataclass  
class GameStatsResult:
    """Result of analyzing daily game statistics."""
    game_name: str
    start_date: datetime
    end_date: datetime
    daily_participation: List[DailyGameParticipation]
    total_days_in_range: int
    
    @property
    def all_participants(self) -> Set[str]:
        """Get all unique participants across all days."""
        participants = set()
        for day in self.daily_participation:
            participants.update(day.participants)
        return participants
    
    def get_user_participation_count(self, user_id: str) -> int:
        """Get how many days a specific user participated."""
        return sum(1 for day in self.daily_participation if user_id in day.participants)


class ThreadAnalyzer:
    """Analyzes Discord threads for daily game participation statistics."""
    
    def __init__(self, bot: discord.Client):
        self.bot = bot
    
    async def analyze_daily_game_stats(
        self,
        channel: discord.TextChannel,
        game_name: str,
        start_date: datetime,
        end_date: datetime
    ) -> GameStatsResult:
        """
        Analyze daily game participation for a specific game in a date range.
        
        Args:
            channel: The Discord channel to search for threads
            game_name: Name of the game to analyze
            start_date: Start of the date range (inclusive)
            end_date: End of the date range (inclusive)
            
        Returns:
            GameStatsResult containing participation data
        """
        # Calculate total days in range
        total_days = (end_date.date() - start_date.date()).days + 1
        
        # Find matching threads
        matching_threads = await self._find_daily_game_threads(
            channel, game_name, start_date, end_date
        )
        
        # Analyze participation in each thread
        daily_participation = []
        for thread, thread_date in matching_threads:
            participants = await self._extract_thread_participants(thread)
            daily_participation.append(
                DailyGameParticipation(date=thread_date, participants=participants)
            )
        
        # Sort by date
        daily_participation.sort(key=lambda x: x.date)
        
        return GameStatsResult(
            game_name=game_name,
            start_date=start_date,
            end_date=end_date,
            daily_participation=daily_participation,
            total_days_in_range=total_days
        )
    
    async def _find_daily_game_threads(
        self,
        channel: discord.TextChannel,
        game_name: str,
        start_date: datetime,
        end_date: datetime
    ) -> List[Tuple[discord.Thread, datetime]]:
        """
        Find threads matching the daily game pattern within the date range.
        
        Returns list of (thread, parsed_date) tuples.
        """
        matching_threads = []
        
        # Pattern: "{game_name} – YYYY-MM-DD"
        # We need to escape special regex characters in the game name
        escaped_game_name = re.escape(game_name)
        pattern = rf"^{escaped_game_name}\s*[–—-]\s*(\d{{4}}-\d{{2}}-\d{{2}})$"
        
        # Get all threads in the channel (active and archived)
        threads = []
        
        # Active threads
        try:
            active_threads = await channel.guild.active_threads()
            for thread in active_threads:
                if thread.parent_id == channel.id:
                    threads.append(thread)
        except discord.Forbidden:
            # Bot might not have permission to view active threads
            pass
        
        # Archived threads - we need to paginate through them
        try:
            async for thread in channel.archived_threads(limit=None):
                threads.append(thread)
        except discord.Forbidden:
            # Bot might not have permission to view archived threads
            pass
        
        for thread in threads:
            match = re.match(pattern, thread.name)
            if match:
                date_str = match.group(1)
                try:
                    thread_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    
                    # Check if thread date is within our range
                    if start_date.date() <= thread_date.date() <= end_date.date():
                        matching_threads.append((thread, thread_date))
                except ValueError:
                    # Invalid date format, skip
                    continue
        
        return matching_threads
    
    async def _extract_thread_participants(self, thread: discord.Thread) -> Set[str]:
        """
        Extract unique participants from a thread based on message authors.
        
        Returns set of user IDs as strings.
        """
        participants = set()
        
        try:
            # Iterate through all messages in the thread
            async for message in thread.history(limit=None):
                # Skip bot messages and system messages
                if not message.author.bot and message.author.id != self.bot.user.id:
                    participants.add(str(message.author.id))
        except discord.Forbidden:
            # Bot might not have permission to read thread history
            pass
        except discord.HTTPException:
            # Other Discord API errors
            pass
        
        return participants 