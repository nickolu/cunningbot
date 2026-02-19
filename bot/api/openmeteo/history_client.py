"""Open-Meteo historical archive API client.

Uses the archive API which supports data back to 1940.
No API key required.
"""

import aiohttp
from typing import Any, Dict

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

DAILY_VARS = [
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "rain_sum",
    "showers_sum",
    "snowfall_sum",
    "precipitation_hours",
    "wind_speed_10m_max",
    "wind_gusts_10m_max",
    "wind_direction_10m_dominant",
    "weathercode",
]

HOURLY_VARS = [
    "temperature_2m",
    "apparent_temperature",
    "precipitation",
    "rain",
    "showers",
    "snowfall",
    "snow_depth",
    "visibility",
    "windspeed_10m",
    "windgusts_10m",
    "weathercode",
]


async def fetch_history(
    lat: float,
    lon: float,
    start_date: str,
    end_date: str,
) -> Dict[str, Any]:
    """Fetch historical weather data from Open-Meteo archive API.

    Args:
        lat: Latitude
        lon: Longitude
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format

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
        "start_date": start_date,
        "end_date": end_date,
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(
            ARCHIVE_URL,
            params=params,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()
