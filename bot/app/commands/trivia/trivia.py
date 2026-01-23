"""Trivia game Discord commands."""

import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional
import uuid
import re
import datetime as dt
import zoneinfo

from bot.app.app_state import (
    set_state_value_from_interaction,
    get_state_value_from_interaction,
)
from bot.domain.trivia.trivia_stats_service import TriviaStatsService
from bot.domain.trivia.question_seeds import CATEGORIES
from bot.app.utils.logger import get_logger

logger = get_logger()
PACIFIC_TZ = zoneinfo.ZoneInfo("America/Los_Angeles")


def parse_schedule(schedule_str: str) -> list[str]:
    """
    Parse schedule string into list of time strings.

    Args:
        schedule_str: Comma-separated times like "8:00,12:00,17:00"

    Returns:
        List of validated time strings

    Raises:
        ValueError: If format is invalid
    """
    times = [t.strip() for t in schedule_str.split(",")]

    for time in times:
        if not re.match(r'^\d{1,2}:\d{2}$', time):
            raise ValueError(f"Invalid time format: {time}. Use HH:MM format.")

        hour, minute = map(int, time.split(":"))
        if not (0 <= hour < 24 and 0 <= minute < 60):
            raise ValueError(f"Invalid time: {time}. Hour must be 0-23, minute 0-59.")

    return times


def parse_duration(duration_str: str) -> int:
    """
    Parse duration string into total minutes.

    Args:
        duration_str: Duration like "1h", "30m", "2h30m"

    Returns:
        Total minutes

    Raises:
        ValueError: If format is invalid
    """
    minutes = 0

    hours_match = re.search(r'(\d+)h', duration_str.lower())
    if hours_match:
        minutes += int(hours_match.group(1)) * 60

    minutes_match = re.search(r'(\d+)m', duration_str.lower())
    if minutes_match:
        minutes += int(minutes_match.group(1))

    if minutes == 0:
        raise ValueError(
            f"Invalid duration format: {duration_str}. Use format like '1h', '30m', or '2h30m'."
        )

    return minutes


class TriviaCog(commands.Cog):
    """Cog for trivia game commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    trivia = app_commands.Group(
        name="trivia",
        description="Daily trivia game commands"
    )

    @trivia.command(name="register", description="Register a trivia game for this channel.")
    @app_commands.describe(
        schedule="Comma-separated times in Pacific timezone (e.g., '8:00,12:00,17:00')",
        answer_window="How long users can answer (e.g., '1h', '30m', '2h')",
        channel="Channel to post in (defaults to current channel)"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def register(
        self,
        interaction: discord.Interaction,
        schedule: str,
        answer_window: str,
        channel: Optional[discord.TextChannel] = None
    ) -> None:
        """Register a new trivia game schedule."""
        # Default to current channel
        target_channel = channel or interaction.channel
        if not isinstance(target_channel, discord.TextChannel):
            await interaction.response.send_message(
                "Can only register trivia in text channels.", ephemeral=True
            )
            return

        try:
            # Parse and validate schedule
            schedule_times = parse_schedule(schedule)

            # Parse and validate answer window
            answer_window_minutes = parse_duration(answer_window)

        except ValueError as e:
            await interaction.response.send_message(
                f"❌ {str(e)}", ephemeral=True
            )
            return

        # Fetch current registrations
        registrations = get_state_value_from_interaction(
            "trivia_registrations", interaction.guild_id
        )
        if registrations is None:
            registrations = {}

        # Generate unique registration ID
        registration_id = str(uuid.uuid4())

        # Create registration
        registrations[registration_id] = {
            "channel_id": target_channel.id,
            "schedule_times": schedule_times,
            "answer_window_minutes": answer_window_minutes,
            "enabled": True,
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat()
        }

        # Save to state
        set_state_value_from_interaction(
            "trivia_registrations", registrations, interaction.guild_id
        )

        times_str = ", ".join(schedule_times)
        await interaction.response.send_message(
            f"✅ Trivia game registered!\n"
            f"• Channel: {target_channel.mention}\n"
            f"• Schedule: {times_str} (Pacific time)\n"
            f"• Answer window: {answer_window}\n"
            f"• Registration ID: `{registration_id[:8]}...`",
            ephemeral=True
        )

    @trivia.command(name="list", description="List all registered trivia games.")
    async def list_games(self, interaction: discord.Interaction) -> None:
        """List all trivia game registrations for this guild."""
        registrations = get_state_value_from_interaction(
            "trivia_registrations", interaction.guild_id
        )

        if not registrations:
            await interaction.response.send_message(
                "No trivia games are currently registered for this guild.", ephemeral=True
            )
            return

        # Build formatted list
        game_list = []
        for reg_id, reg_info in registrations.items():
            status = "✅ Enabled" if reg_info.get("enabled", True) else "🚫 Disabled"
            channel_id = reg_info.get("channel_id")
            channel_mention = f"<#{channel_id}>" if channel_id else "Unknown channel"
            times = ", ".join(reg_info.get("schedule_times", []))
            window = reg_info.get("answer_window_minutes", 60)

            game_entry = (
                f"**{reg_id[:8]}**\n"
                f"  • Status: {status}\n"
                f"  • Channel: {channel_mention}\n"
                f"  • Schedule: {times} Pacific\n"
                f"  • Answer window: {window} minutes\n"
            )
            game_list.append(game_entry)

        # Create embed
        embed = discord.Embed(
            title="🎯 Registered Trivia Games",
            description="\n".join(game_list),
            color=0x00ff00 if any(r.get("enabled", True) for r in registrations.values()) else 0xff0000
        )

        embed.set_footer(text=f"Total registrations: {len(registrations)}")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @trivia.command(name="enable", description="Enable a registered trivia game.")
    @app_commands.describe(registration="Registration ID (use /trivia list to see IDs)")
    @app_commands.checks.has_permissions(administrator=True)
    async def enable_game(self, interaction: discord.Interaction, registration: str) -> None:
        """Enable a trivia game registration."""
        registrations = get_state_value_from_interaction(
            "trivia_registrations", interaction.guild_id
        ) or {}

        # Find matching registration (allow partial ID match)
        matching_reg = None
        for reg_id in registrations:
            if reg_id.startswith(registration):
                matching_reg = reg_id
                break

        if not matching_reg:
            await interaction.response.send_message(
                f"No registration found matching '{registration}'.", ephemeral=True
            )
            return

        registrations[matching_reg]["enabled"] = True
        set_state_value_from_interaction(
            "trivia_registrations", registrations, interaction.guild_id
        )

        await interaction.response.send_message(
            f"✅ Trivia game '{matching_reg[:8]}...' has been enabled.", ephemeral=True
        )

    @trivia.command(name="disable", description="Disable a registered trivia game.")
    @app_commands.describe(registration="Registration ID (use /trivia list to see IDs)")
    @app_commands.checks.has_permissions(administrator=True)
    async def disable_game(self, interaction: discord.Interaction, registration: str) -> None:
        """Disable a trivia game registration."""
        registrations = get_state_value_from_interaction(
            "trivia_registrations", interaction.guild_id
        ) or {}

        # Find matching registration (allow partial ID match)
        matching_reg = None
        for reg_id in registrations:
            if reg_id.startswith(registration):
                matching_reg = reg_id
                break

        if not matching_reg:
            await interaction.response.send_message(
                f"No registration found matching '{registration}'.", ephemeral=True
            )
            return

        registrations[matching_reg]["enabled"] = False
        set_state_value_from_interaction(
            "trivia_registrations", registrations, interaction.guild_id
        )

        await interaction.response.send_message(
            f"🚫 Trivia game '{matching_reg[:8]}...' has been disabled.", ephemeral=True
        )

    @trivia.command(name="delete", description="Delete a registered trivia game.")
    @app_commands.describe(registration="Registration ID (use /trivia list to see IDs)")
    @app_commands.checks.has_permissions(administrator=True)
    async def delete_game(self, interaction: discord.Interaction, registration: str) -> None:
        """Delete a trivia game registration."""
        registrations = get_state_value_from_interaction(
            "trivia_registrations", interaction.guild_id
        ) or {}

        # Find matching registration (allow partial ID match)
        matching_reg = None
        for reg_id in registrations:
            if reg_id.startswith(registration):
                matching_reg = reg_id
                break

        if not matching_reg:
            await interaction.response.send_message(
                f"No registration found matching '{registration}'.", ephemeral=True
            )
            return

        # Get channel info for confirmation
        channel_id = registrations[matching_reg].get("channel_id")

        # Delete registration
        del registrations[matching_reg]
        set_state_value_from_interaction(
            "trivia_registrations", registrations, interaction.guild_id
        )

        channel_mention = f"<#{channel_id}>" if channel_id else "Unknown channel"
        await interaction.response.send_message(
            f"🗑️ Trivia game '{matching_reg[:8]}...' has been deleted. "
            f"It will no longer post to {channel_mention}.",
            ephemeral=True
        )

    @trivia.command(name="answer", description="Submit your answer to the current trivia question.")
    @app_commands.describe(message="Your answer to the trivia question")
    async def answer(self, interaction: discord.Interaction, message: str) -> None:
        """Submit an answer to an active trivia game."""
        # Check if we're in a thread
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message(
                "❌ You can only submit answers in trivia game threads.", ephemeral=True
            )
            return

        # Find active game for this thread
        active_games = get_state_value_from_interaction(
            "active_trivia_games", interaction.guild_id
        ) or {}

        # Find game by thread_id
        game_id = None
        game_data = None
        for gid, gdata in active_games.items():
            if gdata.get("thread_id") == interaction.channel.id:
                game_id = gid
                game_data = gdata
                break

        if not game_id:
            await interaction.response.send_message(
                "❌ No active trivia game found in this thread.", ephemeral=True
            )
            return

        # Check if game has ended
        ends_at_str = game_data.get("ends_at")
        if ends_at_str:
            try:
                ends_at = dt.datetime.fromisoformat(ends_at_str)
                if dt.datetime.now(dt.timezone.utc) > ends_at:
                    await interaction.response.send_message(
                        "❌ The answer window has closed. Wait for results!", ephemeral=True
                    )
                    return
            except (ValueError, TypeError):
                pass

        # Store submission (allow updates)
        if "submissions" not in game_data:
            game_data["submissions"] = {}

        game_data["submissions"][str(interaction.user.id)] = {
            "answer": message,
            "submitted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "is_correct": None  # Will be evaluated when game closes
        }

        # Save updated game state
        active_games[game_id] = game_data
        set_state_value_from_interaction(
            "active_trivia_games", active_games, interaction.guild_id
        )

        await interaction.response.send_message(
            "✅ Your answer has been recorded!", ephemeral=True
        )

    @trivia.command(name="leaderboard", description="Show trivia leaderboard.")
    @app_commands.describe(
        category="Filter by category (optional)",
        timeframe="Timeframe for leaderboard (optional)"
    )
    @app_commands.choices(
        category=[
            app_commands.Choice(name=cat, value=cat) for cat in CATEGORIES
        ],
        timeframe=[
            app_commands.Choice(name="All Time", value="all-time"),
            app_commands.Choice(name="Last 30 Days", value="30-days"),
            app_commands.Choice(name="Last 7 Days", value="7-days")
        ]
    )
    async def leaderboard(
        self,
        interaction: discord.Interaction,
        category: Optional[str] = None,
        timeframe: Optional[str] = None
    ) -> None:
        """Show trivia leaderboard."""
        # Get trivia history
        trivia_history = get_state_value_from_interaction(
            "trivia_history", interaction.guild_id
        ) or {}

        if not trivia_history:
            await interaction.response.send_message(
                "No trivia games have been completed yet.", ephemeral=True
            )
            return

        # Parse timeframe to days
        days = None
        if timeframe == "30-days":
            days = 30
        elif timeframe == "7-days":
            days = 7

        # Calculate leaderboard
        stats_service = TriviaStatsService()
        leaderboard = stats_service.calculate_leaderboard(
            trivia_history,
            category=category,
            days=days
        )

        if not leaderboard:
            await interaction.response.send_message(
                "No trivia participation data available for these filters.", ephemeral=True
            )
            return

        # Format leaderboard
        title = "🏆 Trivia Leaderboard"
        if category:
            title += f" - {category}"
        if timeframe and timeframe != "all-time":
            title += f" ({timeframe.replace('-', ' ').title()})"

        leaderboard_text = []
        for i, (user_id, correct, total, accuracy) in enumerate(leaderboard[:10], 1):
            user = await self.bot.fetch_user(int(user_id))
            username = user.display_name if user else f"User {user_id}"

            medal = ""
            if i == 1:
                medal = "🥇"
            elif i == 2:
                medal = "🥈"
            elif i == 3:
                medal = "🥉"

            leaderboard_text.append(
                f"{medal} **{i}.** {username}: {correct}/{total} correct ({accuracy*100:.1f}%)"
            )

        embed = discord.Embed(
            title=title,
            description="\n".join(leaderboard_text),
            color=0xFFD700
        )

        embed.set_footer(text=f"Total players: {len(leaderboard)}")

        await interaction.response.send_message(embed=embed)

    @trivia.command(name="stats", description="Show trivia stats for a user.")
    @app_commands.describe(user="User to show stats for (defaults to you)")
    async def stats(
        self,
        interaction: discord.Interaction,
        user: Optional[discord.User] = None
    ) -> None:
        """Show trivia stats for a specific user."""
        target_user = user or interaction.user

        # Get trivia history
        trivia_history = get_state_value_from_interaction(
            "trivia_history", interaction.guild_id
        ) or {}

        if not trivia_history:
            await interaction.response.send_message(
                "No trivia games have been completed yet.", ephemeral=True
            )
            return

        # Calculate stats
        stats_service = TriviaStatsService()
        user_stats = stats_service.calculate_user_stats(
            trivia_history,
            str(target_user.id)
        )

        if user_stats["total_games"] == 0:
            await interaction.response.send_message(
                f"{target_user.display_name} hasn't played any trivia games yet.", ephemeral=True
            )
            return

        # Format stats
        embed = discord.Embed(
            title=f"📊 Trivia Stats - {target_user.display_name}",
            color=0x00BFFF
        )

        # Overall stats
        embed.add_field(
            name="Overall",
            value=(
                f"Games Played: **{user_stats['total_games']}**\n"
                f"Correct Answers: **{user_stats['correct_answers']}**\n"
                f"Accuracy: **{user_stats['accuracy']*100:.1f}%**"
            ),
            inline=False
        )

        # Category breakdown
        category_lines = []
        for category, cat_stats in user_stats["by_category"].items():
            if cat_stats["total"] > 0:
                category_lines.append(
                    f"**{category}:** {cat_stats['correct']}/{cat_stats['total']} "
                    f"({cat_stats['accuracy']*100:.1f}%)"
                )

        if category_lines:
            embed.add_field(
                name="By Category",
                value="\n".join(category_lines),
                inline=False
            )

        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    """Setup function for loading the cog."""
    await bot.add_cog(TriviaCog(bot))
