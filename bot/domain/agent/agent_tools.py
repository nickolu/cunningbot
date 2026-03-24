"""Tool definitions and executors for the channel agent.

Each tool is defined as an OpenAI function-calling schema plus an async
executor function that performs the actual work and returns a string result.
"""

import json
import uuid
from io import BytesIO
from typing import Any, Callable, Coroutine, Dict, List, Optional

import aiohttp
import discord

from bot.api.openmeteo.forecast_client import fetch_forecast
from bot.api.openai.image_generation_client import ImageGenerationClient
from bot.api.google.image_generation_client import GeminiImageGenerationClient
from bot.api.google.image_edit_client import GeminiImageEditClient
from bot.api.animation_factory.client import AnimationFactoryClient
from bot.app.commands.dice.roll import DiceRoller
from bot.app.utils.zip_lookup import lookup_zip
from bot.app.utils.logger import get_logger

logger = get_logger()

# ---------------------------------------------------------------------------
# Tool schema definitions (OpenAI function-calling format)
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: Dict[str, dict] = {
    "weather": {
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
    },
    "image": {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": (
                "Generate an image from a text description. The image will be "
                "sent as an attachment in the Discord channel. Use vivid, detailed "
                "prompts for best results."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Detailed text description of the image to generate",
                    },
                    "size": {
                        "type": "string",
                        "enum": ["1024x1024", "1536x1024", "1024x1536"],
                        "description": "Image dimensions. Default square.",
                        "default": "1024x1024",
                    },
                },
                "required": ["prompt"],
            },
        },
    },
    "dice": {
        "type": "function",
        "function": {
            "name": "roll_dice",
            "description": (
                "Roll dice using standard notation (e.g. '2d6', '1d20+5', '4d6+2d4*10'). "
                "Returns the breakdown and total."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "Dice expression like '2d6', '1d20+3', '4d6'",
                    },
                },
                "required": ["expression"],
            },
        },
    },
    "edit_image": {
        "type": "function",
        "function": {
            "name": "edit_image",
            "description": (
                "Edit an existing image from the chat. Use the image URL from "
                "[Image: filename | URL] annotations in the conversation history. "
                "The edited image will be sent as an attachment in the Discord channel."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "image_url": {
                        "type": "string",
                        "description": (
                            "URL of the image to edit. Use the URL from "
                            "[Image: filename | URL] annotations in the conversation history."
                        ),
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Description of the edit to make to the image.",
                    },
                    "size": {
                        "type": "string",
                        "enum": ["1024x1024", "1536x1024", "1024x1536"],
                        "description": "Output image dimensions. Default square.",
                        "default": "1024x1024",
                    },
                },
                "required": ["image_url", "prompt"],
            },
        },
    },
    "search_gifs": {
        "type": "function",
        "function": {
            "name": "search_gifs",
            "description": (
                "Search for animated GIFs by keyword. Returns a list of matching GIF URLs "
                "from the Animation Factory database."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search keyword(s) for finding GIFs",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results to return (1-12). Default 5.",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        },
    },
}

# WMO weather codes (subset for agent summary)
WMO_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Rime fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow",
    80: "Light showers", 81: "Showers", 82: "Heavy showers",
    95: "Thunderstorm", 96: "Thunderstorm w/ hail", 99: "Severe thunderstorm",
}


# ---------------------------------------------------------------------------
# Tool executor functions
# ---------------------------------------------------------------------------

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


async def execute_generate_image(
    arguments: Dict[str, Any],
    channel: discord.TextChannel,
) -> str:
    """Execute the generate_image tool. Returns status message; image is sent to channel."""
    prompt = arguments.get("prompt", "")
    size = arguments.get("size", "1024x1024")

    if not prompt:
        return "No prompt provided for image generation."

    # Try Gemini first, fall back to OpenAI
    image_bytes: Optional[bytes] = None
    error_msg = ""
    model_used = ""

    try:
        client = GeminiImageGenerationClient.factory()
        image_bytes, error_msg = await client.generate_image(prompt, size=size)
        model_used = "Gemini"
    except EnvironmentError:
        pass  # GOOGLE_API_KEY not set

    if image_bytes is None:
        try:
            client = ImageGenerationClient.factory()
            image_bytes, error_msg = await client.generate_image(prompt, size=size)
            model_used = "OpenAI"
        except EnvironmentError:
            return "Image generation is not available (no API keys configured)."

    if image_bytes is None:
        return f"Image generation failed: {error_msg}"

    # Send the image to the channel
    filename = f"agent_image_{uuid.uuid4().hex[:8]}.png"
    stream = BytesIO(image_bytes)
    stream.seek(0)
    file = discord.File(fp=stream, filename=filename)
    await channel.send(file=file)

    return f"Image generated and sent to channel using {model_used}. Prompt: '{prompt[:80]}'"


async def execute_roll_dice(arguments: Dict[str, Any]) -> str:
    """Execute the roll_dice tool."""
    expression = arguments.get("expression", "1d20")
    roller = DiceRoller()
    try:
        breakdown, total, original = roller.parse_and_roll(expression)
        return f"Rolled {original}: {breakdown} = **{total}**"
    except ValueError as e:
        return f"Dice error: {e}"


async def execute_edit_image(
    arguments: Dict[str, Any],
    channel: discord.TextChannel,
) -> str:
    """Execute the edit_image tool. Downloads the source image, edits it via Gemini, and sends the result."""
    image_url = arguments.get("image_url", "")
    prompt = arguments.get("prompt", "")
    size = arguments.get("size", "1024x1024")

    if not image_url:
        return "No image URL provided. Look for [Image: ...] annotations in the conversation."
    if not prompt:
        return "No edit prompt provided."

    # Download the source image
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(image_url) as resp:
                if resp.status != 200:
                    return f"Failed to download image: HTTP {resp.status}"
                image_bytes = await resp.read()
    except Exception as e:
        logger.error(f"Image download failed: {e}")
        return f"Failed to download image: {e}"

    # Edit via Gemini
    try:
        client = GeminiImageEditClient.factory()
        result_images, error_msg = await client.edit_image(
            image=image_bytes,
            prompt=prompt,
            size=size,
        )
    except EnvironmentError:
        return "Image editing is not available (GOOGLE_API_KEY not configured)."
    except Exception as e:
        logger.error(f"Image edit failed: {e}", exc_info=True)
        return f"Image editing failed: {e}"

    if not result_images:
        return f"Image editing failed: {error_msg}"

    # Send the edited image to the channel
    filename = f"agent_edit_{uuid.uuid4().hex[:8]}.png"
    stream = BytesIO(result_images[0])
    stream.seek(0)
    file = discord.File(fp=stream, filename=filename)
    await channel.send(file=file)

    return f"Image edited and sent to channel. Edit prompt: '{prompt[:80]}'"


async def execute_search_gifs(arguments: Dict[str, Any]) -> str:
    """Execute the search_gifs tool."""
    query = arguments.get("query", "")
    limit = min(max(arguments.get("limit", 5), 1), 12)

    if not query:
        return "No search query provided."

    client = AnimationFactoryClient()
    try:
        results = await client.search(query, limit=limit)
    except RuntimeError as e:
        return f"GIF search error: {e}"

    if not results:
        return f"No GIFs found for '{query}'."

    lines = [f"Found {len(results)} GIF(s) for '{query}':"]
    for r in results:
        filename = r.get("filename", "unknown")
        # Build the clear-background URL
        url = f"https://manchat.men/af/clear/{filename}"
        lines.append(f"  - {filename}: {url}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Registry: maps tool name → (schema, executor)
# ---------------------------------------------------------------------------

TOOL_EXECUTORS: Dict[str, Callable[..., Coroutine]] = {
    "get_weather": execute_get_weather,
    "generate_image": execute_generate_image,
    "edit_image": execute_edit_image,
    "roll_dice": execute_roll_dice,
    "search_gifs": execute_search_gifs,
}

# Tools that need the Discord channel reference passed as a second argument
CHANNEL_AWARE_TOOLS: set = {"generate_image", "edit_image"}


def get_tool_schemas_for_config(enabled_tools: List[str]) -> List[dict]:
    """Return the OpenAI tool schemas for the given list of enabled tool keys."""
    schemas = []
    for key in enabled_tools:
        if key in TOOL_SCHEMAS:
            schemas.append(TOOL_SCHEMAS[key])
    return schemas
