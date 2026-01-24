"""Trivia game Discord commands."""

import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional
import uuid
import re
import datetime as dt
import zoneinfo

from bot.app.app_state import get_state_value_from_interaction
from bot.app.redis.trivia_store import TriviaRedisStore
from bot.domain.trivia.trivia_stats_service import TriviaStatsService
from bot.domain.trivia.question_seeds import CATEGORIES, get_unused_seed
from bot.domain.trivia.question_generator import generate_trivia_question
from bot.app.utils.logger import get_logger
from bot.app.commands.trivia.trivia_views import TriviaQuestionView
from bot.app.commands.trivia.trivia_submission_handler import submit_trivia_answer

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


def create_question_embed(question_data: dict, game_id: str, ends_at: dt.datetime) -> discord.Embed:
    """Create rich embed for trivia question."""
    # Map categories to colors
    category_colors = {
        "History": 0x8B4513,
        "Science": 0x4169E1,
        "Sports": 0xFF4500,
        "Entertainment": 0xFF1493,
        "Arts & Literature": 0x9370DB,
        "Geography": 0x228B22
    }

    color = category_colors.get(question_data["category"], 0x0099FF)

    embed = discord.Embed(
        title="🎯 Trivia Question",
        description=question_data["question"],
        color=color,
        timestamp=dt.datetime.now(dt.timezone.utc)
    )

    embed.add_field(name="Category", value=question_data["category"], inline=True)
    embed.add_field(name="Ends At", value=f"<t:{int(ends_at.timestamp())}:R>", inline=True)
    embed.add_field(
        name="How to Answer",
        value="Click the 'Submit Answer' button below or use `/trivia answer`",
        inline=False
    )

    embed.set_footer(text=f"Game ID: {game_id[:8]}")

    return embed


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

        # Generate unique registration ID
        registration_id = str(uuid.uuid4())

        # Create registration data
        reg_data = {
            "channel_id": target_channel.id,
            "schedule_times": schedule_times,
            "answer_window_minutes": answer_window_minutes,
            "enabled": True,
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat()
        }

        # Save to Redis
        store = TriviaRedisStore()
        await store.save_registration(str(interaction.guild_id), registration_id, reg_data)

        times_str = ", ".join(schedule_times)
        await interaction.response.send_message(
            f"✅ Trivia game registered!\n"
            f"• Channel: {target_channel.mention}\n"
            f"• Schedule: {times_str} (Pacific time)\n"
            f"• Answer window: {answer_window}\n"
            f"• Registration ID: `{registration_id[:8]}...`",
            ephemeral=True
        )

    @trivia.command(name="post", description="Post a trivia question immediately.")
    @app_commands.describe(
        channel="Channel to post in (defaults to current channel)",
        answer_window="How long users can answer (e.g., '1h', '30m', '2h') - defaults to 1h"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def post_now(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
        answer_window: Optional[str] = None
    ) -> None:
        """Post a trivia question immediately."""
        # Defer response since question generation takes time
        await interaction.response.defer(ephemeral=True)

        # Default to current channel
        target_channel = channel or interaction.channel
        if not isinstance(target_channel, discord.TextChannel):
            await interaction.followup.send(
                "Can only post trivia in text channels.", ephemeral=True
            )
            return

        try:
            # Parse answer window (default to 1 hour)
            answer_window_minutes = parse_duration(answer_window) if answer_window else 60

        except ValueError as e:
            await interaction.followup.send(
                f"❌ {str(e)}", ephemeral=True
            )
            return

        try:
            # Get used seeds from Redis
            store = TriviaRedisStore()
            used_seeds = await store.get_used_seeds(str(interaction.guild_id))

            # Generate new seed
            seed = get_unused_seed(used_seeds)

            # Generate question
            logger.info(f"Generating trivia question with seed: {seed}")
            question_data = await generate_trivia_question(seed)

            # Calculate end time
            now_utc = dt.datetime.now(dt.timezone.utc)
            ends_at = now_utc + dt.timedelta(minutes=answer_window_minutes)

            # Generate game ID
            game_id = str(uuid.uuid4())

            # Create embed
            embed = create_question_embed(question_data, game_id, ends_at)

            # Create view with button
            view = TriviaQuestionView(game_id, str(interaction.guild_id), self.bot)

            # Post message with view
            message = await target_channel.send(embed=embed, view=view)
            logger.info(f"Posted trivia question to channel {target_channel.id}")

            # Create thread
            now_pt = dt.datetime.now(PACIFIC_TZ)
            thread_name = f"Trivia – {question_data['category']} – {now_pt:%Y-%m-%d %H:%M}"
            thread = None
            try:
                thread = await message.create_thread(
                    name=thread_name,
                    auto_archive_duration=1440  # 24 hours
                )
                logger.info(f"Created thread '{thread_name}' for trivia game")
            except discord.HTTPException as exc:
                logger.error(f"Failed to create thread: {exc}")

            # Store game data
            game_data = {
                "registration_id": None,  # Manual post, not from a registration
                "channel_id": target_channel.id,
                "thread_id": thread.id if thread else None,
                "question": question_data["question"],
                "correct_answer": question_data["correct_answer"],
                "category": question_data["category"],
                "explanation": question_data["explanation"],
                "seed": seed,
                "started_at": now_utc.isoformat(),
                "ends_at": ends_at.isoformat(),
                "message_id": message.id,
            }

            # Store in Redis
            await store.create_game(str(interaction.guild_id), game_id, game_data)

            # Mark seed as used in Redis (atomic operation)
            await store.mark_seed_used(str(interaction.guild_id), seed)

            logger.info(f"Saved game state for game_id {game_id[:8]}")

            await interaction.followup.send(
                f"✅ Trivia question posted!\n"
                f"• Channel: {target_channel.mention}\n"
                f"• Category: {question_data['category']}\n"
                f"• Answer window: {answer_window_minutes} minutes\n"
                f"• Ends: <t:{int(ends_at.timestamp())}:R>",
                ephemeral=True
            )

        except Exception as e:
            logger.error(f"Error posting trivia question: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ Failed to post trivia question: {str(e)}", ephemeral=True
            )

    @trivia.command(name="list", description="List all registered trivia games.")
    async def list_games(self, interaction: discord.Interaction) -> None:
        """List all trivia game registrations for this guild."""
        store = TriviaRedisStore()
        registrations = await store.get_registrations(str(interaction.guild_id))

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
        store = TriviaRedisStore()
        registrations = await store.get_registrations(str(interaction.guild_id))

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
        await store.save_registration(str(interaction.guild_id), matching_reg, registrations[matching_reg])

        await interaction.response.send_message(
            f"✅ Trivia game '{matching_reg[:8]}...' has been enabled.", ephemeral=True
        )

    @trivia.command(name="disable", description="Disable a registered trivia game.")
    @app_commands.describe(registration="Registration ID (use /trivia list to see IDs)")
    @app_commands.checks.has_permissions(administrator=True)
    async def disable_game(self, interaction: discord.Interaction, registration: str) -> None:
        """Disable a trivia game registration."""
        store = TriviaRedisStore()
        registrations = await store.get_registrations(str(interaction.guild_id))

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
        await store.save_registration(str(interaction.guild_id), matching_reg, registrations[matching_reg])

        await interaction.response.send_message(
            f"🚫 Trivia game '{matching_reg[:8]}...' has been disabled.", ephemeral=True
        )

    @trivia.command(name="delete", description="Delete a registered trivia game.")
    @app_commands.describe(registration="Registration ID (use /trivia list to see IDs)")
    @app_commands.checks.has_permissions(administrator=True)
    async def delete_game(self, interaction: discord.Interaction, registration: str) -> None:
        """Delete a trivia game registration."""
        store = TriviaRedisStore()
        registrations = await store.get_registrations(str(interaction.guild_id))

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
        await store.delete_registration(str(interaction.guild_id), matching_reg)

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
        await submit_trivia_answer(
            self.bot, interaction, message, str(interaction.guild_id)
        )

    @trivia.command(name="status", description="Show status of the current trivia question.")
    async def status(self, interaction: discord.Interaction) -> None:
        """Show status of current trivia game in this thread."""
        # Check if we're in a thread
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message(
                "❌ You can only check status in trivia game threads.", ephemeral=True
            )
            return

        # Find active game for this thread
        store = TriviaRedisStore()
        active_games = await store.get_active_games(str(interaction.guild_id))

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

        # Get submission count
        submissions = await store.get_submissions(str(interaction.guild_id), game_id)
        submission_count = len(submissions)

        # Check if game has ended
        ends_at_str = game_data.get("ends_at")
        game_ended = False
        time_remaining = None

        if ends_at_str:
            try:
                ends_at = dt.datetime.fromisoformat(ends_at_str)
                now = dt.datetime.now(dt.timezone.utc)
                if now > ends_at:
                    game_ended = True
                else:
                    time_remaining = ends_at - now
            except (ValueError, TypeError):
                pass

        # Create status embed
        embed = discord.Embed(
            title="📊 Trivia Question Status",
            color=0x00FF00 if not game_ended else 0xFF0000
        )

        # Add category and question info
        category = game_data.get("category", "Unknown")
        embed.add_field(name="Category", value=category, inline=True)

        # Add submission count
        participants_text = f"**{submission_count}** {'player' if submission_count == 1 else 'players'}"
        embed.add_field(name="Answers Submitted", value=participants_text, inline=True)

        # Add time status
        if game_ended:
            embed.add_field(name="Status", value="⏰ **Closed** - Waiting for results", inline=False)
        elif time_remaining:
            total_seconds = int(time_remaining.total_seconds())
            hours, remainder = divmod(total_seconds, 3600)
            minutes, seconds = divmod(remainder, 60)

            if hours > 0:
                time_str = f"{hours}h {minutes}m"
            elif minutes > 0:
                time_str = f"{minutes}m {seconds}s"
            else:
                time_str = f"{seconds}s"

            embed.add_field(name="Time Remaining", value=f"⏱️ **{time_str}**", inline=False)

        # List participants (without showing answers)
        if submission_count > 0:
            participant_names = []
            for user_id in submissions.keys():
                try:
                    user = await self.bot.fetch_user(int(user_id))
                    participant_names.append(user.display_name)
                except:
                    participant_names.append(f"User {user_id}")

            # Show up to 10 participants
            if len(participant_names) <= 10:
                embed.add_field(
                    name="Participants",
                    value=", ".join(participant_names),
                    inline=False
                )
            else:
                shown_names = participant_names[:10]
                embed.add_field(
                    name="Participants",
                    value=", ".join(shown_names) + f" and {len(participant_names) - 10} more...",
                    inline=False
                )

        embed.set_footer(text=f"Game ID: {game_id[:8]}")

        await interaction.response.send_message(embed=embed)

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
