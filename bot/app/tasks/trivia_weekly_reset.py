"""trivia_weekly_reset.py

Script intended to be invoked every 10 minutes to perform the weekly trivia reset.
On Sundays (Pacific time), it saves a snapshot of the prior week's leaderboard
(Monday through Saturday) and announces the weekly winner in all registered trivia
channels. Sunday's game is not yet closed when this runs, so it is excluded.

Uses an idempotency guard (last_reset_time) to ensure the reset fires exactly once
per week regardless of how many times this script runs.

Usage (inside Docker container):
    python -m bot.app.tasks.trivia_weekly_reset

Runs in a loop with 10-minute intervals via Docker compose.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import logging
from typing import Any, Dict, List, Optional

import discord
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

from bot.app.app_state import get_all_guild_states
from bot.app.redis.trivia_store import TriviaRedisStore
from bot.app.redis.locks import redis_lock
from bot.app.redis.client import get_redis_client, initialize_redis, close_redis
from bot.app.redis.exceptions import LockAcquisitionError
from bot.domain.trivia.trivia_stats_service import TriviaStatsService

logger = logging.getLogger("TriviaWeeklyReset")
logging.basicConfig(level=logging.INFO)

PACIFIC_TZ = ZoneInfo("America/Los_Angeles")


def get_current_week_id(now_pt: dt.datetime) -> str:
    """Return the ISO week ID string for the given datetime.

    Args:
        now_pt: datetime in Pacific timezone

    Returns:
        Week ID string like '2026-07'
    """
    iso_year, iso_week, _ = now_pt.isocalendar()
    return f"{iso_year}-{iso_week:02d}"


def get_week_start_pt(now_pt: dt.datetime) -> dt.datetime:
    """Return Monday 00:00 Pacific for the Mon–Sat scoring window that ends this Saturday.

    When called on a Sunday, this returns the most recent Monday (6 days ago).
    """
    # Sunday=6, Monday=0. On Sunday, days_since_monday == 6.
    days_since_monday = now_pt.weekday()  # Monday=0 … Sunday=6
    return now_pt.replace(hour=0, minute=0, second=0, microsecond=0) - dt.timedelta(days=days_since_monday)


def get_week_end_pt(week_start_pt: dt.datetime) -> dt.datetime:
    """Return Saturday 23:59:59 Pacific for the scoring window starting at week_start_pt.

    The window covers Monday through Saturday (6 days). Sunday's game is posted that
    day but only closes on Monday, so it is excluded from the weekly summary.
    """
    return week_start_pt + dt.timedelta(days=5, hours=23, minutes=59, seconds=59)


async def run_weekly_reset() -> None:
    """Run the weekly trivia reset for all guilds."""
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.error("DISCORD_TOKEN environment variable not set – aborting.")
        return

    # Initialize Redis
    await initialize_redis()
    store = TriviaRedisStore()
    redis_client = get_redis_client()

    now_utc = dt.datetime.now(dt.timezone.utc)
    now_pt = now_utc.astimezone(PACIFIC_TZ)

    logger.info("Trivia weekly reset check at %s PT (weekday=%d)", now_pt.isoformat(), now_pt.weekday())

    # Only run on Sundays (weekday 6)
    if now_pt.weekday() != 6:
        logger.info("Not Sunday (weekday=%d) – skipping weekly reset.", now_pt.weekday())
        await close_redis()
        return

    # Determine which guilds need a reset
    all_guild_states = get_all_guild_states()
    guilds_to_reset: List[str] = []

    # The reset runs on Sunday to snapshot the current week (Mon–Sat).
    # current_week_id is used as the idempotency key so we reset at most once per calendar week.
    current_week_id = get_current_week_id(now_pt)
    # week_start_pt = this Monday 00:00 PT (6 days ago when called on Sunday)
    week_start_pt = get_week_start_pt(now_pt)
    week_start_utc = week_start_pt.astimezone(dt.timezone.utc)
    # week_end_pt = this Saturday 23:59:59 PT
    week_end_pt = get_week_end_pt(week_start_pt)
    week_end_utc = week_end_pt.astimezone(dt.timezone.utc)

    # Scoring window: Monday 00:00 → Saturday 23:59:59 (the week we're summarising)
    prev_week_start_pt = week_start_pt       # Monday of this week
    prev_week_start_utc = week_start_utc
    prev_week_id = current_week_id           # snapshot key uses current week

    for guild_id_str, guild_state in all_guild_states.items():
        if guild_id_str == "global":
            continue

        if not isinstance(guild_state, dict):
            continue

        # Check if guild has trivia registrations
        registrations = await store.get_registrations(guild_id_str)
        if not registrations:
            logger.info("Guild %s has no trivia registrations – skipping.", guild_id_str)
            continue

        # Check idempotency: skip if already reset this week
        last_reset_str = await store.get_last_reset_time(guild_id_str)
        if last_reset_str:
            try:
                last_reset_dt = dt.datetime.fromisoformat(last_reset_str)
                last_reset_week_id = get_current_week_id(last_reset_dt.astimezone(PACIFIC_TZ))
                if last_reset_week_id == current_week_id:
                    logger.info(
                        "Guild %s already reset for week %s – skipping.",
                        guild_id_str, current_week_id
                    )
                    continue
            except (ValueError, TypeError) as e:
                logger.warning("Could not parse last_reset_time for guild %s: %s", guild_id_str, e)

        guilds_to_reset.append(guild_id_str)

    if not guilds_to_reset:
        logger.info("No guilds need a weekly reset.")
        await close_redis()
        return

    logger.info("Guilds to reset: %s", guilds_to_reset)

    # Connect to Discord to post announcements
    intents = discord.Intents.none()
    client = discord.Client(intents=intents)

    @client.event  # type: ignore[misc]
    async def on_ready():
        logger.info("Discord client logged in as %s", client.user)

        for guild_id_str in guilds_to_reset:
            lock_resource = f"trivia:{guild_id_str}:weekly_reset"
            try:
                async with redis_lock(redis_client, lock_resource, timeout=120):
                    await process_guild_reset(
                        client, store, guild_id_str,
                        current_week_id,
                        prev_week_id, prev_week_start_pt, prev_week_start_utc,
                        week_end_utc,
                        now_utc,
                    )
            except LockAcquisitionError:
                logger.info("Could not acquire lock for guild %s reset (another instance is processing)", guild_id_str)
            except Exception as exc:
                logger.error("Unexpected error resetting guild %s: %s", guild_id_str, exc, exc_info=True)

        await client.close()
        await close_redis()

    await client.start(token)


async def process_guild_reset(
    client: discord.Client,
    store: TriviaRedisStore,
    guild_id_str: str,
    current_week_id: str,
    prev_week_id: str,
    prev_week_start_pt: dt.datetime,
    prev_week_start_utc: dt.datetime,
    week_end_utc: dt.datetime,
    now_utc: dt.datetime,
) -> None:
    """Perform the weekly reset for a single guild.

    Snapshots the current week's Mon–Sat window. Called on Sundays.

    Args:
        client: Discord client
        store: TriviaRedisStore instance
        guild_id_str: Guild ID string
        current_week_id: ISO week ID of the current week (used for idempotency)
        prev_week_id: ISO week ID being snapshotted (same as current_week_id when posting on Sunday)
        prev_week_start_pt: Monday 00:00 Pacific datetime (start of scoring window)
        prev_week_start_utc: prev_week_start_pt converted to UTC
        week_end_utc: Saturday 23:59:59 UTC (inclusive upper bound for game filter)
        now_utc: Current UTC datetime
    """
    # Re-check idempotency inside lock (double-check pattern)
    last_reset_str = await store.get_last_reset_time(guild_id_str)
    if last_reset_str:
        try:
            last_reset_dt = dt.datetime.fromisoformat(last_reset_str)
            last_reset_week_id = get_current_week_id(last_reset_dt.astimezone(PACIFIC_TZ))
            if last_reset_week_id == current_week_id:
                logger.info("Guild %s already reset (double-check) – skipping.", guild_id_str)
                return
        except (ValueError, TypeError):
            pass

    # Get all history and filter to the previous week window
    trivia_history = await store.get_all_history_as_dict(guild_id_str)

    # Calculate leaderboard for Mon–Sat of the current week
    stats_service = TriviaStatsService()
    leaderboard = stats_service.calculate_leaderboard(
        trivia_history,
        since=prev_week_start_utc,
        until=week_end_utc,
    )

    prev_week_end_pt = get_week_end_pt(prev_week_start_pt)

    if leaderboard:
        # Build snapshot rankings with usernames
        rankings = []
        for rank, (user_id, points, correct, total, accuracy) in enumerate(leaderboard, 1):
            try:
                user = await client.fetch_user(int(user_id))
                username = user.display_name
            except Exception:
                username = f"User {user_id}"

            rankings.append({
                "rank": rank,
                "user_id": user_id,
                "username": username,
                "points": points,
                "correct": correct,
                "total": total,
            })

        # Save snapshot for the previous week
        snapshot_data = {
            "week_id": prev_week_id,
            "week_start": prev_week_start_pt.isoformat(),
            "week_end": prev_week_end_pt.isoformat(),
            "rankings": rankings,
        }
        await store.save_weekly_snapshot(guild_id_str, prev_week_id, snapshot_data)
        logger.info("Saved weekly snapshot for guild %s, week %s", guild_id_str, prev_week_id)

        # Build winner announcement embed
        winner = rankings[0]
        week_label = f"{prev_week_start_pt.strftime('%b %-d')}–{prev_week_end_pt.strftime('%b %-d')}"

        embed = discord.Embed(
            title=f"👑 Weekly Trivia Winner — {week_label}",
            description=f"**{winner['username']}** dominated this week with **{winner['points']} pts**!",
            color=0xFFD700,
            timestamp=now_utc,
        )

        # Top 3 podium
        podium_lines = []
        medals = ["🥇", "🥈", "🥉"]
        for i, entry in enumerate(rankings[:3]):
            medal = medals[i] if i < len(medals) else f"#{i+1}"
            podium_lines.append(f"{medal} **{entry['username']}** — {entry['points']} pts")

        if podium_lines:
            embed.add_field(name="Top 3", value="\n".join(podium_lines), inline=False)

        embed.set_footer(
            text=(
                f"{len(rankings)} player(s) participated • "
                f"{prev_week_start_pt.strftime('%b %-d')}–{prev_week_end_pt.strftime('%b %-d, %Y')}"
            )
        )

        # Post announcement to all registered channels
        registrations = await store.get_registrations(guild_id_str)
        posted_channels: set[int] = set()

        for reg_data in registrations.values():
            channel_id = reg_data.get("channel_id")
            if not channel_id or channel_id in posted_channels:
                continue

            try:
                channel = client.get_channel(channel_id)
                if channel is None:
                    channel = await client.fetch_channel(channel_id)

                if isinstance(channel, discord.TextChannel):
                    await channel.send(embed=embed)
                    posted_channels.add(channel_id)
                    logger.info("Posted weekly winner to channel %s in guild %s", channel_id, guild_id_str)

            except discord.Forbidden:
                logger.error("Missing permissions to post in channel %s", channel_id)
            except discord.HTTPException as exc:
                logger.error("HTTP error posting to channel %s: %s", channel_id, exc)
            except Exception as exc:
                logger.error("Error posting to channel %s: %s", channel_id, exc, exc_info=True)

    else:
        logger.info("No participation for guild %s week %s – saving empty snapshot.", guild_id_str, prev_week_id)
        snapshot_data = {
            "week_id": prev_week_id,
            "week_start": prev_week_start_pt.isoformat(),
            "week_end": prev_week_end_pt.isoformat(),
            "rankings": [],
        }
        await store.save_weekly_snapshot(guild_id_str, prev_week_id, snapshot_data)

    # Mark reset as done
    await store.set_last_reset_time(guild_id_str, now_utc.isoformat())
    logger.info("Completed weekly reset for guild %s, week %s", guild_id_str, current_week_id)


if __name__ == "__main__":
    asyncio.run(run_weekly_reset())
