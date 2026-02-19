"""Open-Meteo forecast API client.

Supports both future forecast and recent past days in a single call.
No API key required.
"""

import aiohttp
from typing import Any, Dict

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

DAILY_VARS = [
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "rain_sum",
    "showers_sum",
    "snowfall_sum",
    "precipitation_hours",
    "precipitation_probability_max",
    "wind_speed_10m_max",
    "wind_gusts_10m_max",
    "wind_direction_10m_dominant",
    "uv_index_max",
    "sunrise",
    "sunset",
    "weathercode",
]

HOURLY_VARS = [
    "temperature_2m",
    "apparent_temperature",
    "precipitation_probability",
    "precipitation",
    "rain",
    "showers",
    "snowfall",
    "snow_depth",
    "visibility",
    "windspeed_10m",
    "windgusts_10m",
    "uv_index",
    "weathercode",
]


async def fetch_forecast(
    lat: float,
    lon: float,
    forecast_days: int = 7,
    past_days: int = 0,
) -> Dict[str, Any]:
    """Fetch weather forecast from Open-Meteo API.

    Args:
        lat: Latitude
        lon: Longitude
        forecast_days: Number of forecast days (1-16)
        past_days: Number of past days to include (0-92)

    Returns:
        API response dictionary with daily and hourly data
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": ",".join(DAILY_VARS),
        "hourly": ",".join(HOURLY_VARS),
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": "auto",
        "forecast_days": forecast_days,
        "past_days": past_days,
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(
            FORECAST_URL,
            params=params,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()
