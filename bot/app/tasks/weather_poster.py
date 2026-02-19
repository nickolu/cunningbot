"""weather_poster.py

Scheduled weather forecast poster for CunningBot.
Runs every 5 minutes. For each guild with a weather schedule, checks if the
current time is within 5 minutes of any scheduled posting time, then fetches
a forecast and posts it to the configured Discord channel.

Usage (inside Docker container):
    python -m bot.app.tasks.weather_poster

Wire up in docker-compose.yml:
    command: bash -c "while true; do python -m bot.app.tasks.weather_poster; sleep 300; done"

Requires DISCORD_TOKEN and OPENAI_API_KEY environment variables.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
from typing import Optional
from zoneinfo import ZoneInfo

import discord
from dotenv import load_dotenv

load_dotenv()

from bot.api.openmeteo.forecast_client import fetch_forecast
from bot.app.commands.weather.weather import build_forecast_embeds, generate_llm_summary
from bot.app.redis.client import close_redis, get_redis_client, initialize_redis
from bot.app.redis.exceptions import LockAcquisitionError
from bot.app.redis.locks import redis_lock
from bot.app.redis.weather_store import WeatherRedisStore
from bot.app.utils.zip_lookup import lookup_zip

logger = logging.getLogger("WeatherPoster")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

DISCORD_ERROR_UNKNOWN_CHANNEL = 10003


def is_time_to_post(
    times: list[str], timezone: str, window_minutes: int = 5
) -> Optional[str]:
    """Check if the current local time is within the posting window.

    Args:
        times: List of "HH:MM" strings (24-hour, schedule's local time)
        timezone: IANA timezone string for the schedule
        window_minutes: How many minutes after each scheduled time to consider valid

    Returns:
        The matched slot string "HH:MM", or None if no slot matches.
    """
    try:
        tz = ZoneInfo(timezone)
    except Exception:
        tz = ZoneInfo("UTC")

    now = dt.datetime.now(tz)

    for slot in times:
        try:
            h, m = map(int, slot.split(":"))
        except ValueError:
            continue

        scheduled = now.replace(hour=h, minute=m, second=0, microsecond=0)
        diff_seconds = (now - scheduled).total_seconds()

        if 0 <= diff_seconds < window_minutes * 60:
            return slot

    return None


async def post_weather() -> None:
    """Main entry point — posts pending weather forecasts."""
    logger.info("=== Weather Poster Starting ===")

    await initialize_redis()
    store = WeatherRedisStore()
    redis_client = get_redis_client()

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.error("DISCORD_TOKEN environment variable not set – aborting.")
        await close_redis()
        return

    # Discover guilds with weather schedules
    guild_ids = await store.get_all_guilds_with_schedules()
    if not guild_ids:
        logger.info("No guilds with weather schedules found.")
        await close_redis()
        return

    logger.info(f"Found {len(guild_ids)} guilds with weather schedules")

    # Collect pending posts: (guild_id, channel_id_str, config, slot_key)
    pending_posts = []

    for guild_id in guild_ids:
        schedules = await store.get_all_schedules(guild_id)
        for channel_id_str, config in schedules.items():
            if not config.get("enabled", True):
                continue

            times = config.get("times", [])
            timezone = config.get("timezone", "America/Los_Angeles")

            matched_slot = is_time_to_post(times, timezone)
            if not matched_slot:
                continue

            # Build dedup key: "YYYY-MM-DD:HH:MM" in the schedule's local timezone
            try:
                tz = ZoneInfo(timezone)
                now_local = dt.datetime.now(tz)
                date_str = now_local.strftime("%Y-%m-%d")
            except Exception:
                date_str = dt.datetime.utcnow().strftime("%Y-%m-%d")

            slot_key = f"{date_str}:{matched_slot}"

            # Quick dedup check before taking a lock
            if await store.has_posted(guild_id, channel_id_str, slot_key):
                logger.info(
                    f"Already posted: guild={guild_id} channel={channel_id_str} slot={slot_key}"
                )
                continue

            pending_posts.append((guild_id, channel_id_str, config, slot_key))

    if not pending_posts:
        logger.info("No pending weather posts this run.")
        await close_redis()
        return

    logger.info(f"Processing {len(pending_posts)} pending weather post(s)")

    # Create a minimal Discord client
    intents = discord.Intents.none()
    client = discord.Client(intents=intents)

    @client.event  # type: ignore[misc]
    async def on_ready():
        logger.info("Discord client logged in as %s", client.user)

        for guild_id, channel_id_str, config, slot_key in pending_posts:
            lock_resource = f"weather:{guild_id}:post:{channel_id_str}"

            try:
                async with redis_lock(redis_client, lock_resource, timeout=120):
                    logger.info(
                        f"Acquired lock for guild={guild_id} channel={channel_id_str}"
                    )

                    # Re-check dedup after acquiring lock (another container may have posted)
                    if await store.has_posted(guild_id, channel_id_str, slot_key):
                        logger.info(
                            f"Post already completed (post-lock): slot={slot_key}"
                        )
                        continue

                    # Verify the Discord channel is still accessible
                    channel_id = int(channel_id_str)
                    try:
                        channel = client.get_channel(channel_id)
                        if channel is None:
                            channel = await client.fetch_channel(channel_id)  # type: ignore[attr-defined]
                        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
                            logger.warning(
                                f"Channel {channel_id} is not a text channel, skipping"
                            )
                            continue
                    except discord.HTTPException as exc:
                        if exc.code == DISCORD_ERROR_UNKNOWN_CHANNEL:
                            logger.error(
                                f"Channel {channel_id} not found (10003) — disabling schedule"
                            )
                            await store.disable_schedule(guild_id, channel_id_str)
                        else:
                            logger.warning(
                                f"HTTP error fetching channel {channel_id} (code {exc.code}): {exc}"
                            )
                        continue
                    except Exception as exc:
                        logger.error(f"Error fetching channel {channel_id}: {exc}")
                        continue

                    # Look up coordinates for the ZIP code
                    zip_code = config.get("zip", "")
                    coords = lookup_zip(zip_code)
                    if not coords:
                        logger.error(
                            f"ZIP {zip_code} not found in lookup — skipping guild={guild_id} channel={channel_id}"
                        )
                        continue

                    lat, lon = coords
                    forecast_days = config.get("forecast_days", 7)
                    past_days = config.get("past_days", 0)
                    label = config.get("label", zip_code)

                    # Fetch forecast data
                    try:
                        weather_data = await asyncio.wait_for(
                            fetch_forecast(lat, lon, forecast_days, past_days),
                            timeout=20.0,
                        )
                    except Exception as e:
                        logger.error(
                            f"Forecast fetch failed for guild={guild_id} channel={channel_id}: {e}"
                        )
                        continue

                    # MARK AS POSTED BEFORE SENDING to prevent infinite retries
                    # on network flaps — we'd rather miss one post than spam
                    await store.mark_posted(guild_id, channel_id_str, slot_key)

                    # Generate LLM summary (with fallback on any failure)
                    try:
                        summary = await asyncio.wait_for(
                            generate_llm_summary(weather_data, label, forecast_days),
                            timeout=30.0,
                        )
                    except Exception as e:
                        logger.warning(f"LLM summary failed, using fallback: {e}")
                        summary = f"Here's the weather forecast for {label}."

                    # Build embeds and post
                    embeds = build_forecast_embeds(
                        weather_data, label, zip_code, forecast_days, past_days
                    )

                    try:
                        await channel.send(content=summary, embeds=embeds)
                        logger.info(
                            f"Posted weather: guild={guild_id} channel={channel_id} slot={slot_key}"
                        )
                    except discord.Forbidden:
                        logger.error(
                            f"Missing permissions for channel {channel_id}"
                        )
                    except discord.HTTPException as exc:
                        if exc.code == DISCORD_ERROR_UNKNOWN_CHANNEL:
                            logger.error(
                                f"Channel {channel_id} deleted after verification — disabling"
                            )
                            await store.disable_schedule(guild_id, channel_id_str)
                        else:
                            logger.warning(
                                f"HTTP error posting to channel {channel_id} (code {exc.code}): {exc}"
                            )

            except LockAcquisitionError:
                logger.info(
                    f"Channel {channel_id_str} being processed by another container, skipping"
                )
                continue
            except Exception as e:
                logger.error(
                    f"Error processing guild={guild_id} channel={channel_id_str}: {e}"
                )
                continue

        logger.info("=== Weather Poster Finished ===")

        # Close the OpenAI client to prevent connection leaks
        from bot.api.openai.chat_completions_client import openai

        try:
            await openai.close()
            await asyncio.sleep(1.0)
        except Exception as e:
            logger.warning(f"Error closing OpenAI client: {e}")

        await asyncio.sleep(0.5)
        await client.close()

    try:
        await client.start(token)
    finally:
        if not client.is_closed():
            await client.close()
        await close_redis()


if __name__ == "__main__":
    asyncio.run(post_weather())
