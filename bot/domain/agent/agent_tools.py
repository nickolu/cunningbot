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
from bot.api.openai.image_edit_client import ImageEditClient
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
                    "model": {
                        "type": "string",
                        "enum": [
                            "gemini-2.5-flash",
                            "gemini-3-pro",
                            "gpt-image-1",
                        ],
                        "description": (
                            "Image model to use. "
                            "gemini-2.5-flash (default, fast and cheap), "
                            "gemini-3-pro (higher quality), "
                            "gpt-image-1 (OpenAI)."
                        ),
                        "default": "gemini-2.5-flash",
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
    "web_search": {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web for current information on any topic. "
                "Returns relevant results with titles, snippets, and source URLs. "
                "Use this when someone asks about recent events, current facts, "
                "or anything that benefits from up-to-date information."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query",
                    },
                },
                "required": ["query"],
            },
        },
    },
    "read_channel": {
        "type": "function",
        "function": {
            "name": "read_channel",
            "description": (
                "Read recent messages from another text channel in this Discord server. "
                "Use this when someone asks about what's being discussed in another channel, "
                "or when you need context from a different channel."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "channel_name": {
                        "type": "string",
                        "description": "Name or ID of the text channel to read (e.g. 'general', 'announcements', or a numeric channel ID)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Number of recent messages to fetch (1-50). Default 25.",
                        "default": 25,
                    },
                },
                "required": ["channel_name"],
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


# Friendly model names → actual model identifiers for image editing
IMAGE_EDIT_MODELS = {
    "gemini-2.5-flash": "gemini-2.5-flash-image",
    "gemini-3-pro": "gemini-3-pro-image-preview",
    "gpt-image-1": "gpt-image-1",
}

DEFAULT_IMAGE_EDIT_MODEL = "gemini-2.5-flash"


async def execute_edit_image(
    arguments: Dict[str, Any],
    channel: discord.TextChannel,
) -> str:
    """Execute the edit_image tool. Downloads the source image, edits it via the selected model, and sends the result."""
    image_url = arguments.get("image_url", "")
    prompt = arguments.get("prompt", "")
    size = arguments.get("size", "1024x1024")
    model_key = arguments.get("model", DEFAULT_IMAGE_EDIT_MODEL)

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

    actual_model = IMAGE_EDIT_MODELS.get(model_key, IMAGE_EDIT_MODELS[DEFAULT_IMAGE_EDIT_MODEL])
    is_openai = model_key == "gpt-image-1"

    # Edit the image
    try:
        if is_openai:
            client = ImageEditClient.factory()
            # OpenAI edit_image is synchronous — run in thread
            import asyncio
            result_images, error_msg = await asyncio.to_thread(
                client.edit_image,
                image=image_bytes,
                prompt=prompt,
                size=size,
            )
        else:
            client = GeminiImageEditClient.factory(model=actual_model)
            result_images, error_msg = await client.edit_image(
                image=image_bytes,
                prompt=prompt,
                size=size,
            )
    except EnvironmentError:
        key_name = "OPENAI_API_KEY" if is_openai else "GOOGLE_API_KEY"
        return f"Image editing is not available ({key_name} not configured)."
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

    return f"Image edited using {model_key} and sent to channel. Edit prompt: '{prompt[:80]}'"


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


async def execute_web_search(arguments: Dict[str, Any]) -> str:
    """Execute the web_search tool."""
    query = arguments.get("query", "")

    if not query:
        return "No search query provided."

    try:
        from bot.api.perplexity.client import PerplexityClient

        client = PerplexityClient()
        results = await client.search(query, max_results=5)
    except EnvironmentError:
        return "Web search is not available (PERPLEXITY_API_KEY not configured)."
    except RuntimeError as e:
        return f"Web search error: {e}"

    if not results:
        return f"No web results found for '{query}'."

    lines = [f"Found {len(results)} result(s) for '{query}':\n"]
    for r in results:
        title = r.get("title", "Untitled")
        snippet = r.get("snippet", "")
        url = r.get("url", "")
        date = r.get("date", "")
        date_str = f" ({date})" if date else ""
        lines.append(f"**{title}**{date_str}")
        if snippet:
            lines.append(snippet)
        if url:
            lines.append(url)
        lines.append("")  # blank line between results

    return "\n".join(lines).strip()


async def execute_read_channel(
    arguments: Dict[str, Any],
    channel: discord.TextChannel,
) -> str:
    """Execute the read_channel tool. Reads messages from another channel in the same guild."""
    channel_name = arguments.get("channel_name", "").strip().lstrip("#")
    limit = min(max(arguments.get("limit", 25), 1), 50)

    if not channel_name:
        return "No channel name provided."

    guild = channel.guild
    if guild is None:
        return "Cannot read channels: not in a server."

    # Find the target channel: ID → exact name → case-insensitive → substring
    target: Optional[discord.TextChannel] = None
    text_channels = guild.text_channels

    # Try as a channel ID first
    if channel_name.isdigit():
        target = guild.get_channel(int(channel_name))
        if target is not None and not isinstance(target, discord.TextChannel):
            target = None  # Not a text channel

    if target is None:
        for ch in text_channels:
        if ch.name == channel_name:
            target = ch
            break

    if target is None:
        lower_name = channel_name.lower()
        for ch in text_channels:
            if ch.name.lower() == lower_name:
                target = ch
                break

    if target is None:
        lower_name = channel_name.lower()
        for ch in text_channels:
            if lower_name in ch.name.lower():
                target = ch
                break

    if target is None:
        available = [ch.name for ch in text_channels[:20]]
        return (
            f"Could not find a channel matching '{channel_name}'. "
            f"Available channels: {', '.join(available)}"
        )

    # Check bot permissions
    perms = target.permissions_for(guild.me)
    if not perms.read_message_history:
        return f"I don't have permission to read messages in #{target.name}."

    # Fetch messages
    messages = []
    try:
        async for msg in target.history(limit=limit, oldest_first=False):
            author = msg.author.display_name
            timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M")
            content = msg.content or ""
            if msg.attachments:
                att_names = [a.filename for a in msg.attachments]
                content += f" [Attachments: {', '.join(att_names)}]"
            if msg.embeds:
                content += f" [+{len(msg.embeds)} embed(s)]"
            if content.strip():
                messages.append(f"[{timestamp}] {author}: {content}")
    except discord.Forbidden:
        return f"I don't have permission to read #{target.name}."
    except Exception as e:
        logger.error(f"read_channel error: {e}")
        return f"Error reading #{target.name}: {e}"

    messages.reverse()  # Chronological order

    if not messages:
        return f"No recent messages found in #{target.name}."

    header = f"Recent messages from #{target.name} ({len(messages)} messages):\n"
    return header + "\n".join(messages)


# ---------------------------------------------------------------------------
# Registry: maps tool name → (schema, executor)
# ---------------------------------------------------------------------------

TOOL_EXECUTORS: Dict[str, Callable[..., Coroutine]] = {
    "get_weather": execute_get_weather,
    "generate_image": execute_generate_image,
    "edit_image": execute_edit_image,
    "roll_dice": execute_roll_dice,
    "search_gifs": execute_search_gifs,
    "web_search": execute_web_search,
    "read_channel": execute_read_channel,
}

# Tools that need the Discord channel reference passed as a second argument
CHANNEL_AWARE_TOOLS: set = {"generate_image", "edit_image", "read_channel"}


def get_tool_schemas_for_config(enabled_tools: List[str]) -> List[dict]:
    """Return the OpenAI tool schemas for the given list of enabled tool keys."""
    schemas = []
    for key in enabled_tools:
        if key in TOOL_SCHEMAS:
            schemas.append(TOOL_SCHEMAS[key])
    return schemas
