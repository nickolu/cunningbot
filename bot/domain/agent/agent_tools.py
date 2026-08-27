"""Backwards-compatible surface for the agent tool registry.

The definitions moved to `bot/domain/agent/tools/`, one module per tool, so
that adding a tool is adding a file rather than editing four parallel lists.
This module re-exports what the rest of the bot and the test suite already
import by this path. New code should import from
`bot.domain.agent.tools.registry` instead.
"""

from bot.domain.agent.tools.base import AgentTool
from bot.domain.agent.tools.registry import (
    CHANNEL_AWARE_TOOLS,
    DEFAULT_ENABLED_TOOLS,
    TOOL_EXECUTORS,
    TOOL_SCHEMAS,
    TOOLS,
    get_tool_schemas_for_config,
)

from bot.domain.agent.tools._shared import _send_image_and_describe
from bot.domain.agent.tools.dice import execute_roll_dice
from bot.domain.agent.tools.edit_image import (
    DEFAULT_IMAGE_EDIT_MODEL,
    IMAGE_EDIT_MODELS,
    execute_edit_image,
)
from bot.domain.agent.tools.host_image import execute_host_image
from bot.domain.agent.tools.image import (
    _validate_gpt_image_2_size,
    execute_generate_image,
)
from bot.domain.agent.tools.publish_page import (
    UNPUBLISHABLE_IMAGE_HOSTS,
    _unpublishable_image_refs,
    execute_publish_page,
)
from bot.domain.agent.tools.read_channel import execute_read_channel
from bot.domain.agent.tools.search_gifs import execute_search_gifs
from bot.domain.agent.tools.weather import WMO_CODES, execute_get_weather
from bot.domain.agent.tools.web_search import execute_web_search

__all__ = [
    "AgentTool",
    "CHANNEL_AWARE_TOOLS",
    "DEFAULT_ENABLED_TOOLS",
    "DEFAULT_IMAGE_EDIT_MODEL",
    "IMAGE_EDIT_MODELS",
    "TOOLS",
    "TOOL_EXECUTORS",
    "TOOL_SCHEMAS",
    "UNPUBLISHABLE_IMAGE_HOSTS",
    "WMO_CODES",
    "execute_edit_image",
    "execute_generate_image",
    "execute_get_weather",
    "execute_host_image",
    "execute_publish_page",
    "execute_read_channel",
    "execute_roll_dice",
    "execute_search_gifs",
    "execute_web_search",
    "get_tool_schemas_for_config",
]
