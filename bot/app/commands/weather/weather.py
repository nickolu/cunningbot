"""Weather command cog for CunningBot.

Provides /weather schedule, /weather unschedule, /weather list,
/weather forecast, and /weather history commands using the free
Open-Meteo API (no API key required).
"""

import asyncio
import logging
from datetime import datetime
from typing import List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord import app_commands
from discord.ext import commands

from bot.api.openmeteo.forecast_client import fetch_forecast
from bot.api.openmeteo.history_client import fetch_history
from bot.api.openai.chat_completions_client import ChatCompletionsClient
from bot.app.redis.weather_store import WeatherRedisStore
from bot.app.redis.serialization import guild_id_to_str
from bot.app.utils.zip_lookup import lookup_zip, is_valid_zip

logger = logging.getLogger("WeatherCommands")

# ---------------------------------------------------------------------------
# WMO Weather Code descriptions
# ---------------------------------------------------------------------------

WMO_CODES = {
    0: "Clear",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Rime fog",
    51: "Lt drizzle",
    53: "Drizzle",
    55: "Hvy drizzle",
    61: "Lt rain",
    63: "Rain",
    65: "Hvy rain",
    71: "Lt snow",
    73: "Snow",
    75: "Hvy snow",
    77: "Snow grains",
    80: "Lt showers",
    81: "Showers",
    82: "Hvy showers",
    85: "Snow showers",
    86: "Hvy snow shwrs",
    95: "Tstorm",
    96: "Tstorm+hail",
    99: "Tstorm+hail",
}


def _wmo_description(code) -> str:
    if code is None:
        return "Unknown"
    return WMO_CODES.get(int(code), f"Code {code}")


def _wmo_color(code) -> int:
    """Return Discord embed color based on WMO weather code."""
    if code is None:
        return 0x808080
    c = int(code)
    if c <= 1:
        return 0xFFD700   # Gold – sunny
    elif c <= 3:
        return 0x9E9E9E   # Gray – cloudy/overcast
    elif c in (45, 48):
        return 0xB0BEC5   # Light gray – fog
    elif (51 <= c <= 65) or (80 <= c <= 82):
        return 0x2196F3   # Blue – rain
    elif (71 <= c <= 77) or c in (85, 86):
        return 0x81D4FA   # Light blue – snow
    elif c >= 95:
        return 0xB71C1C   # Dark red – thunderstorm
    return 0x808080


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt_temp(val) -> str:
    if val is None:
        return "N/A"
    return f"{int(round(float(val)))}°"


def _fmt_precip(val) -> str:
    if val is None or float(val) == 0:
        return '0.00"'
    return f'{float(val):.2f}"'


def _fmt_wind(val) -> str:
    if val is None:
        return "N/A"
    return f"{int(round(float(val)))}mph"


def _fmt_date(date_str: str) -> str:
    """Format 'YYYY-MM-DD' to 'MM/DD' (e.g. '02/08')."""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        return d.strftime("%m/%d")
    except Exception:
        return date_str[5:10]


def _fmt_time(time_str: str) -> str:
    """Extract HH:MM from 'YYYY-MM-DDTHH:MM'."""
    try:
        return time_str[11:16]
    except Exception:
        return time_str


# ---------------------------------------------------------------------------
# Table builders
# ---------------------------------------------------------------------------

def _fmt_wind_gust(wind_val, gust_val) -> str:
    """Format wind and gust as 'XX/YYmph'."""
    w = int(round(float(wind_val))) if wind_val is not None else "?"
    g = int(round(float(gust_val))) if gust_val is not None else "?"
    return f"{w}/{g}mph"


def _build_daily_table(weather_data: dict, num_days: int) -> str:
    """Build a monospace daily forecast/history table."""
    daily = weather_data.get("daily", {})
    dates = daily.get("time", [])
    hi_temps = daily.get("temperature_2m_max", [])
    lo_temps = daily.get("temperature_2m_min", [])
    codes = daily.get("weathercode", [])
    rains = daily.get("precipitation_sum", [])
    winds = daily.get("wind_speed_10m_max", [])
    gusts = daily.get("wind_gusts_10m_max", [])

    header = f"{'Date':<6} {'Hi':<5} {'Lo':<5} {'Condition':<15} {'Rain':<6} Wind/Gust"
    sep = "-" * len(header)
    rows = [header, sep]

    for i in range(min(num_days, len(dates))):
        date_s = _fmt_date(dates[i]) if i < len(dates) else "N/A"
        hi = _fmt_temp(hi_temps[i] if i < len(hi_temps) else None)
        lo = _fmt_temp(lo_temps[i] if i < len(lo_temps) else None)
        cond = _wmo_description(codes[i] if i < len(codes) else None)[:14]
        rain = _fmt_precip(rains[i] if i < len(rains) else None)
        wg = _fmt_wind_gust(
            winds[i] if i < len(winds) else None,
            gusts[i] if i < len(gusts) else None,
        )
        rows.append(f"{date_s:<6} {hi:<5} {lo:<5} {cond:<15} {rain:<6} {wg}")

    return "```\n" + "\n".join(rows) + "\n```"


def _build_hourly_table(weather_data: dict, num_days: int) -> str:
    """Build a monospace hourly forecast table (every 3 hours, capped at 7 days)."""
    hourly = weather_data.get("hourly", {})
    times = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])
    feels = hourly.get("apparent_temperature", [])
    codes = hourly.get("weathercode", [])
    rain_probs = hourly.get("precipitation_probability", [])
    winds = hourly.get("windspeed_10m", [])
    gusts = hourly.get("windgusts_10m", [])

    header = (
        f"{'Time':<6} {'Temp':<5} {'Feels':<6} {'Condition':<15} {'Rain%':<6} Wind/Gust"
    )
    sep = "-" * len(header)
    rows = [header, sep]

    # Cap to 7 days to stay within embed limits
    max_hours = min(num_days, 7) * 24

    for i in range(min(max_hours, len(times))):
        if i % 3 != 0:
            continue
        time_s = _fmt_time(times[i])
        temp = _fmt_temp(temps[i] if i < len(temps) else None)
        feel = _fmt_temp(feels[i] if i < len(feels) else None)
        cond = _wmo_description(codes[i] if i < len(codes) else None)[:14]
        rp = rain_probs[i] if i < len(rain_probs) else None
        rain_pct = f"{int(rp)}%" if rp is not None else "N/A"
        wg = _fmt_wind_gust(
            winds[i] if i < len(winds) else None,
            gusts[i] if i < len(gusts) else None,
        )
        rows.append(
            f"{time_s:<6} {temp:<5} {feel:<6} {cond:<15} {rain_pct:<6} {wg}"
        )

    return "```\n" + "\n".join(rows) + "\n```"


# ---------------------------------------------------------------------------
# Public embed builders (used by the poster too)
# ---------------------------------------------------------------------------

def build_forecast_embeds(
    weather_data: dict,
    label: str,
    zip_code: str,
    forecast_days: int,
    past_days: int = 0,
) -> List[discord.Embed]:
    """Build Discord embeds for a weather forecast.

    Uses hourly table for 1–3 day forecasts, daily table for 7–16 day forecasts.
    """
    daily = weather_data.get("daily", {})
    codes = daily.get("weathercode", [])
    leading_code = codes[past_days] if past_days < len(codes) else (codes[0] if codes else None)
    color = _wmo_color(leading_code)

    lat = weather_data.get("latitude", 0)
    lon = weather_data.get("longitude", 0)
    footer = f"Powered by Open-Meteo • {label} (ZIP {zip_code}) • {lat:.2f}, {lon:.2f}"

    total_days = past_days + forecast_days
    title = f"Weather Forecast — {label}"
    if past_days > 0:
        title += f" (+{past_days}d history)"

    if forecast_days <= 3:
        table = _build_hourly_table(weather_data, total_days)
    else:
        table = _build_daily_table(weather_data, total_days)

    embed = discord.Embed(title=title, description=table, color=color)
    embed.set_footer(text=footer)
    return [embed]


def build_history_embeds(
    weather_data: dict,
    label: str,
    zip_code: str,
    start_date: str,
    end_date: str,
) -> List[discord.Embed]:
    """Build Discord embeds for historical weather data."""
    daily = weather_data.get("daily", {})
    codes = daily.get("weathercode", [])
    leading_code = codes[0] if codes else None
    color = _wmo_color(leading_code)

    lat = weather_data.get("latitude", 0)
    lon = weather_data.get("longitude", 0)
    footer = f"Powered by Open-Meteo • {label} (ZIP {zip_code}) • {lat:.2f}, {lon:.2f}"

    num_days = len(daily.get("time", []))
    table = _build_daily_table(weather_data, num_days)

    embed = discord.Embed(
        title=f"Weather History — {label} ({start_date} to {end_date})",
        description=table,
        color=color,
    )
    embed.set_footer(text=footer)
    return [embed]


# ---------------------------------------------------------------------------
# LLM summary (used by the poster too)
# ---------------------------------------------------------------------------

async def generate_llm_summary(
    weather_data: dict,
    label: str,
    forecast_days: int,
) -> str:
    """Generate a friendly TV-meteorologist style weather intro via OpenAI.

    Falls back to a plain string if the LLM call fails.
    """
    daily = weather_data.get("daily", {})
    dates = daily.get("time", [])
    codes = daily.get("weathercode", [])
    hi_temps = daily.get("temperature_2m_max", [])
    lo_temps = daily.get("temperature_2m_min", [])
    winds = daily.get("wind_speed_10m_max", [])
    gusts = daily.get("wind_gusts_10m_max", [])
    rains = daily.get("precipitation_sum", [])
    snows = daily.get("snowfall_sum", [])

    # Past days offset: first forecast day is at index past_days; use first 3 forecast days
    # weather_data may contain past days too; we summarize the first 3 available days
    lines = []
    for i in range(min(3, len(dates))):
        cond = _wmo_description(codes[i] if i < len(codes) else None)
        hi = int(round(float(hi_temps[i]))) if i < len(hi_temps) and hi_temps[i] is not None else "?"
        lo = int(round(float(lo_temps[i]))) if i < len(lo_temps) and lo_temps[i] is not None else "?"
        wind = int(round(float(winds[i]))) if i < len(winds) and winds[i] is not None else "?"
        gust = int(round(float(gusts[i]))) if i < len(gusts) and gusts[i] is not None else "?"
        rain = float(rains[i]) if i < len(rains) and rains[i] is not None else 0.0
        snow = float(snows[i]) if i < len(snows) and snows[i] is not None else 0.0
        date_fmt = _fmt_date(dates[i]) if i < len(dates) else "?"
        lines.append(
            f"{date_fmt}: {cond}, hi {hi}°F lo {lo}°F, "
            f"wind {wind}mph gusts {gust}mph, rain {rain:.2f}\" snow {snow:.1f}\""
        )

    summary_data = "\n".join(lines)
    prompt = (
        f"You are a friendly TV meteorologist. Write a 2-3 sentence weather intro for {label}. "
        f"Be specific with numbers and give practical local advice. Keep it under 300 characters.\n\n"
        f"Weather data:\n{summary_data}"
    )

    try:
        client = ChatCompletionsClient(model="gpt-4o-mini")
        result = await client.chat([{"role": "user", "content": prompt}])
        return result.strip()
    except Exception as e:
        logger.error(f"LLM weather summary failed: {e}")
        return f"Here's the weather forecast for {label}."


# ---------------------------------------------------------------------------
# View and factory functions for "Show Details" button
# ---------------------------------------------------------------------------

class WeatherTableView(discord.ui.View):
    """A view with a single 'Show Details' button. No callback — handled by Cog listener."""
    def __init__(self, custom_id: str):
        super().__init__(timeout=None)
        btn = discord.ui.Button(
            label="Show Details",
            style=discord.ButtonStyle.secondary,
            emoji="📊",
            custom_id=custom_id,
        )
        self.add_item(btn)


def make_forecast_view(zip_code: str, forecast_days: int, past_days: int, label: str) -> WeatherTableView:
    safe_label = label[:50].replace(":", "|")
    return WeatherTableView(f"weather_table:{zip_code}:{forecast_days}:{past_days}:{safe_label}")


def make_history_view(zip_code: str, start_date: str, end_date: str, label: str) -> WeatherTableView:
    safe_label = label[:50].replace(":", "|")
    return WeatherTableView(f"weather_history:{zip_code}:{start_date}:{end_date}:{safe_label}")


# ---------------------------------------------------------------------------
# Shared choice lists
# ---------------------------------------------------------------------------

_FORECAST_DAYS_CHOICES = [
    app_commands.Choice(name="1 day", value=1),
    app_commands.Choice(name="3 days", value=3),
    app_commands.Choice(name="7 days", value=7),
    app_commands.Choice(name="14 days", value=14),
    app_commands.Choice(name="16 days", value=16),
]

_PAST_DAYS_CHOICES = [
    app_commands.Choice(name="None", value=0),
    app_commands.Choice(name="1 day", value=1),
    app_commands.Choice(name="2 days", value=2),
    app_commands.Choice(name="3 days", value=3),
    app_commands.Choice(name="5 days", value=5),
    app_commands.Choice(name="7 days", value=7),
    app_commands.Choice(name="14 days", value=14),
    app_commands.Choice(name="30 days", value=30),
    app_commands.Choice(name="60 days", value=60),
    app_commands.Choice(name="90 days", value=90),
]


# ---------------------------------------------------------------------------
# Command Cog
# ---------------------------------------------------------------------------

class WeatherCog(commands.Cog):
    """Cog for weather forecast commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ------------------------------------------------------------------
    # Button interaction handler (handles clicks from poster-sent messages)
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type != discord.InteractionType.component:
            return
        custom_id = (interaction.data or {}).get("custom_id", "")
        if custom_id.startswith("weather_table:"):
            await self._handle_forecast_button(interaction, custom_id)
        elif custom_id.startswith("weather_history:"):
            await self._handle_history_button(interaction, custom_id)

    async def _handle_forecast_button(self, interaction: discord.Interaction, custom_id: str) -> None:
        # Format: "weather_table:{zip}:{forecast_days}:{past_days}:{label}"
        parts = custom_id.split(":", 4)
        zip_code, forecast_days, past_days = parts[1], int(parts[2]), int(parts[3])
        label = parts[4].replace("|", ":") if len(parts) > 4 else zip_code

        coords = lookup_zip(zip_code)
        if not coords:
            await interaction.response.send_message("Could not resolve ZIP code.", ephemeral=True)
            return

        await interaction.response.defer()
        lat, lon = coords
        try:
            weather_data = await asyncio.wait_for(
                fetch_forecast(lat, lon, forecast_days, past_days), timeout=20.0
            )
        except Exception as e:
            await interaction.edit_original_response(content=f"Failed to fetch weather data: {e}")
            return

        embeds = build_forecast_embeds(weather_data, label, zip_code, forecast_days, past_days)
        await interaction.message.edit(
            content=interaction.message.content,
            embeds=embeds,
            view=discord.ui.View(),
        )

    async def _handle_history_button(self, interaction: discord.Interaction, custom_id: str) -> None:
        # Format: "weather_history:{zip}:{start_date}:{end_date}:{label}"
        parts = custom_id.split(":", 4)
        zip_code, start_date, end_date = parts[1], parts[2], parts[3]
        label = parts[4].replace("|", ":") if len(parts) > 4 else zip_code

        coords = lookup_zip(zip_code)
        if not coords:
            await interaction.response.send_message("Could not resolve ZIP code.", ephemeral=True)
            return

        await interaction.response.defer()
        lat, lon = coords
        try:
            weather_data = await asyncio.wait_for(
                fetch_history(lat, lon, start_date, end_date), timeout=20.0
            )
        except Exception as e:
            await interaction.edit_original_response(content=f"Failed to fetch weather data: {e}")
            return

        embeds = build_history_embeds(weather_data, label, zip_code, start_date, end_date)
        await interaction.message.edit(
            content=interaction.message.content,
            embeds=embeds,
            view=discord.ui.View(),
        )

    weather = app_commands.Group(
        name="weather",
        description="Weather forecasts and scheduled daily posts.",
    )

    # ------------------------------------------------------------------
    # /weather schedule
    # ------------------------------------------------------------------

    @weather.command(
        name="schedule",
        description="Schedule daily weather posts in this channel.",
    )
    @app_commands.describe(
        zip="ZIP code for the location (e.g. 92101)",
        times="Posting times, comma-separated 24h format (e.g. '08:00,18:00')",
        forecast_days="Number of forecast days (default: 7)",
        past_days="Past days to include for context (default: 0)",
        label="Friendly location name (e.g. 'San Diego, CA')",
        timezone="IANA timezone (default: America/Los_Angeles)",
    )
    @app_commands.choices(
        forecast_days=_FORECAST_DAYS_CHOICES,
        past_days=_PAST_DAYS_CHOICES,
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def schedule(
        self,
        interaction: discord.Interaction,
        zip: str,
        times: str,
        forecast_days: int = 7,
        past_days: int = 0,
        label: Optional[str] = None,
        timezone: str = "America/Los_Angeles",
    ) -> None:
        """Schedule daily weather posts for the current channel."""
        await interaction.response.defer(ephemeral=True)

        if not is_valid_zip(zip):
            await interaction.followup.send(
                f"ZIP code `{zip}` was not found. Please check and try again.",
                ephemeral=True,
            )
            return

        # Parse and validate times
        time_list = [t.strip() for t in times.split(",") if t.strip()]
        if not time_list:
            await interaction.followup.send(
                "Please provide at least one time (e.g. `08:00`).", ephemeral=True
            )
            return

        for t in time_list:
            try:
                parts = t.split(":")
                if len(parts) != 2:
                    raise ValueError()
                h, m = int(parts[0]), int(parts[1])
                if not (0 <= h <= 23 and 0 <= m <= 59):
                    raise ValueError()
            except (ValueError, AttributeError):
                await interaction.followup.send(
                    f"Invalid time `{t}`. Use HH:MM 24-hour format (e.g. `08:00`).",
                    ephemeral=True,
                )
                return

        # Validate timezone
        try:
            ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, KeyError):
            await interaction.followup.send(
                f"Unknown timezone `{timezone}`. "
                "Use IANA names like `America/Los_Angeles` or `America/New_York`.",
                ephemeral=True,
            )
            return

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.followup.send(
                "This command can only be used in a text channel.", ephemeral=True
            )
            return

        display_label = label or zip
        guild_id_str = guild_id_to_str(interaction.guild_id)
        channel_id_str = str(channel.id)

        config = {
            "channel_id": channel.id,
            "zip": zip,
            "label": display_label,
            "times": time_list,
            "timezone": timezone,
            "forecast_days": forecast_days,
            "past_days": past_days,
            "enabled": True,
            "created_at": datetime.utcnow().isoformat(),
        }

        store = WeatherRedisStore()
        await store.save_schedule(guild_id_str, channel_id_str, config)

        embed = discord.Embed(title="Weather Schedule Set", color=0x00a8ff)
        embed.add_field(
            name="Location", value=f"{display_label} (ZIP {zip})", inline=True
        )
        embed.add_field(name="Times", value=", ".join(time_list), inline=True)
        embed.add_field(name="Timezone", value=timezone, inline=True)
        embed.add_field(name="Forecast Days", value=str(forecast_days), inline=True)
        embed.add_field(name="Past Days", value=str(past_days), inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /weather unschedule
    # ------------------------------------------------------------------

    @weather.command(
        name="unschedule",
        description="Remove the weather schedule from this channel.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def unschedule(self, interaction: discord.Interaction) -> None:
        """Remove the weather schedule for the current channel."""
        await interaction.response.defer(ephemeral=True)

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.followup.send(
                "This command can only be used in a text channel.", ephemeral=True
            )
            return

        guild_id_str = guild_id_to_str(interaction.guild_id)
        store = WeatherRedisStore()
        deleted = await store.delete_schedule(guild_id_str, str(channel.id))

        if deleted:
            await interaction.followup.send(
                "Weather schedule removed from this channel.", ephemeral=True
            )
        else:
            await interaction.followup.send(
                "No weather schedule found for this channel.", ephemeral=True
            )

    # ------------------------------------------------------------------
    # /weather list
    # ------------------------------------------------------------------

    @weather.command(
        name="list",
        description="Show the weather schedule configured for this channel.",
    )
    async def list(self, interaction: discord.Interaction) -> None:
        """Show the current weather schedule for this channel."""
        await interaction.response.defer(ephemeral=True)

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.followup.send(
                "This command can only be used in a text channel.", ephemeral=True
            )
            return

        guild_id_str = guild_id_to_str(interaction.guild_id)
        store = WeatherRedisStore()
        config = await store.get_schedule(guild_id_str, str(channel.id))

        if not config:
            await interaction.followup.send(
                "No weather schedule configured for this channel.", ephemeral=True
            )
            return

        status = "Enabled" if config.get("enabled", True) else "Disabled"
        embed = discord.Embed(title="Weather Schedule", color=0x00a8ff)
        embed.add_field(
            name="Location",
            value=f"{config.get('label', config.get('zip'))} (ZIP {config.get('zip')})",
            inline=True,
        )
        embed.add_field(
            name="Times", value=", ".join(config.get("times", [])), inline=True
        )
        embed.add_field(
            name="Timezone",
            value=config.get("timezone", "America/Los_Angeles"),
            inline=True,
        )
        embed.add_field(
            name="Forecast Days", value=str(config.get("forecast_days", 7)), inline=True
        )
        embed.add_field(
            name="Past Days", value=str(config.get("past_days", 0)), inline=True
        )
        embed.add_field(name="Status", value=status, inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /weather forecast
    # ------------------------------------------------------------------

    @weather.command(
        name="forecast",
        description="Get an on-demand weather forecast for any ZIP code.",
    )
    @app_commands.describe(
        zip="ZIP code for the location (e.g. 92101)",
        forecast_days="Number of forecast days (default: 7)",
        past_days="Past days to include for context (default: 0)",
        label="Friendly location name (e.g. 'San Diego, CA')",
    )
    @app_commands.choices(
        forecast_days=_FORECAST_DAYS_CHOICES,
        past_days=_PAST_DAYS_CHOICES,
    )
    async def forecast(
        self,
        interaction: discord.Interaction,
        zip: str,
        forecast_days: int = 7,
        past_days: int = 0,
        label: Optional[str] = None,
    ) -> None:
        """Fetch and post an on-demand weather forecast."""
        await interaction.response.defer()

        if not is_valid_zip(zip):
            await interaction.followup.send(
                f"ZIP code `{zip}` was not found. Please check and try again."
            )
            return

        coords = lookup_zip(zip)
        lat, lon = coords
        display_label = label or zip

        try:
            weather_data = await asyncio.wait_for(
                fetch_forecast(lat, lon, forecast_days, past_days),
                timeout=20.0,
            )
        except asyncio.TimeoutError:
            await interaction.followup.send(
                "The weather API timed out. Please try again in a moment."
            )
            return
        except Exception as e:
            await interaction.followup.send(f"Failed to fetch weather data: {e}")
            return

        try:
            summary = await asyncio.wait_for(
                generate_llm_summary(weather_data, display_label, forecast_days),
                timeout=30.0,
            )
        except Exception:
            summary = f"Here's the weather forecast for {display_label}."

        view = make_forecast_view(zip, forecast_days, past_days, display_label)
        await interaction.followup.send(content=summary, view=view)

    # ------------------------------------------------------------------
    # /weather history
    # ------------------------------------------------------------------

    @weather.command(
        name="history",
        description="Get historical weather data for any ZIP code.",
    )
    @app_commands.describe(
        zip="ZIP code for the location (e.g. 92101)",
        start_date="Start date in YYYY-MM-DD format",
        end_date="End date in YYYY-MM-DD format (max 92-day range)",
        label="Friendly location name (e.g. 'San Diego, CA')",
    )
    async def history(
        self,
        interaction: discord.Interaction,
        zip: str,
        start_date: str,
        end_date: str,
        label: Optional[str] = None,
    ) -> None:
        """Fetch and post historical weather data."""
        await interaction.response.defer()

        if not is_valid_zip(zip):
            await interaction.followup.send(
                f"ZIP code `{zip}` was not found. Please check and try again."
            )
            return

        # Validate dates
        try:
            start = datetime.strptime(start_date, "%Y-%m-%d").date()
            end = datetime.strptime(end_date, "%Y-%m-%d").date()
        except ValueError:
            await interaction.followup.send(
                "Invalid date format. Use YYYY-MM-DD (e.g. `2026-01-01`)."
            )
            return

        if end < start:
            await interaction.followup.send("End date must be on or after start date.")
            return

        if (end - start).days > 92:
            await interaction.followup.send(
                "Maximum date range is 92 days. Please narrow your selection."
            )
            return

        coords = lookup_zip(zip)
        lat, lon = coords
        display_label = label or zip

        try:
            weather_data = await asyncio.wait_for(
                fetch_history(lat, lon, start_date, end_date),
                timeout=20.0,
            )
        except asyncio.TimeoutError:
            await interaction.followup.send(
                "The weather API timed out. Please try again in a moment."
            )
            return
        except Exception as e:
            await interaction.followup.send(f"Failed to fetch historical data: {e}")
            return

        view = make_history_view(zip, start_date, end_date, display_label)
        await interaction.followup.send(
            content=f"Historical weather for {display_label} ({start_date} to {end_date}).",
            view=view,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WeatherCog(bot))
