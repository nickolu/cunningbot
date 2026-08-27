"""The `get_weather` agent tool."""

from typing import Any, Dict

from bot.api.openmeteo.forecast_client import fetch_forecast
from bot.app.utils.zip_lookup import lookup_zip
from bot.app.utils.logger import get_logger
from bot.domain.agent.tools.base import AgentTool

logger = get_logger()


SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": (
            "Get the current weather forecast for a US ZIP code. "
            "Returns daily forecasts including temperature, precipitation, wind, and conditions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "zip_code": {
                    "type": "string",
                    "description": "US ZIP code (e.g. '92101' for San Diego)",
                },
                "days": {
                    "type": "integer",
                    "description": "Number of forecast days (1-16). Default 3.",
                    "default": 3,
                },
            },
            "required": ["zip_code"],
        },
    },
}


WMO_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Rime fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow",
    80: "Light showers", 81: "Showers", 82: "Heavy showers",
    95: "Thunderstorm", 96: "Thunderstorm w/ hail", 99: "Severe thunderstorm",
}


async def execute_get_weather(arguments: Dict[str, Any]) -> str:
    """Execute the get_weather tool."""
    zip_code = arguments.get("zip_code", "")
    days = min(max(arguments.get("days", 3), 1), 16)

    coords = lookup_zip(zip_code)
    if coords is None:
        return f"Could not find ZIP code '{zip_code}'. Please use a valid US ZIP code."

    lat, lon = coords
    try:
        data = await fetch_forecast(lat, lon, forecast_days=days)
    except Exception as e:
        logger.error(f"Weather API error: {e}")
        return f"Weather API error: {e}"

    daily = data.get("daily", {})
    dates = daily.get("time", [])
    if not dates:
        return "No forecast data available."

    lines = [f"Weather forecast for ZIP {zip_code} ({lat:.2f}, {lon:.2f}):"]
    for i, date in enumerate(dates[:days]):
        hi = daily.get("temperature_2m_max", [None])[i]
        lo = daily.get("temperature_2m_min", [None])[i]
        code = daily.get("weathercode", [None])[i]
        precip = daily.get("precipitation_sum", [0])[i]
        wind = daily.get("wind_speed_10m_max", [0])[i]
        desc = WMO_CODES.get(int(code), f"Code {code}") if code is not None else "Unknown"
        line = f"  {date}: {desc}, High {hi}°F / Low {lo}°F"
        if precip and precip > 0:
            line += f", Precip {precip}\""
        if wind:
            line += f", Wind {wind} mph"
        lines.append(line)

    return "\n".join(lines)


TOOL = AgentTool(
    config_key="weather",
    schema=SCHEMA,
    executor=execute_get_weather,
    channel_aware=False,
)
