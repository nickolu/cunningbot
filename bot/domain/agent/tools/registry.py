"""Collects every agent tool module into the lookups the bot consumes.

Adding a tool means adding a module with a `TOOL = AgentTool(...)` and listing
it in `_MODULES` below. Nothing else. The schema map, executor map,
channel-aware set, and per-channel defaults all fall out of that.
"""

from typing import Dict, List, Callable, Coroutine, Tuple

from bot.domain.agent.tools.base import AgentTool
from bot.domain.agent.tools import (
    dice,
    edit_image,
    host_image,
    image,
    publish_page,
    read_channel,
    search_gifs,
    weather,
    web_search,
)

# Order is user-visible: it drives the `/agent` tool picker and the default
# tool list, so keep it stable and append new tools at the end.
_MODULES = (
    weather,
    image,
    dice,
    edit_image,
    search_gifs,
    web_search,
    read_channel,
    publish_page,
    host_image,
)

TOOLS: Tuple[AgentTool, ...] = tuple(m.TOOL for m in _MODULES)


def _check_unique() -> None:
    """A duplicate key would silently shadow a tool. Fail at import instead."""
    for label, values in (
        ("config key", [t.config_key for t in TOOLS]),
        ("function name", [t.function_name for t in TOOLS]),
    ):
        seen = set()
        for value in values:
            if value in seen:
                raise RuntimeError("Duplicate agent tool %s: %r" % (label, value))
            seen.add(value)


_check_unique()

# Keyed by config key — what a channel's stored config lists.
TOOL_SCHEMAS: Dict[str, dict] = {t.config_key: t.schema for t in TOOLS}

# Keyed by function name — what the model emits in a tool call.
TOOL_EXECUTORS: Dict[str, Callable[..., Coroutine]] = {
    t.function_name: t.executor for t in TOOLS
}

# Function names whose executor takes the Discord channel as a second argument.
CHANNEL_AWARE_TOOLS = {t.function_name for t in TOOLS if t.channel_aware}

# Config keys a newly registered channel agent starts with.
DEFAULT_ENABLED_TOOLS: List[str] = [t.config_key for t in TOOLS if t.default_enabled]


def get_tool_schemas_for_config(enabled_tools: List[str]) -> List[dict]:
    """Return the OpenAI tool schemas for the given list of enabled tool keys."""
    schemas = []
    for key in enabled_tools:
        if key in TOOL_SCHEMAS:
            schemas.append(TOOL_SCHEMAS[key])
    return schemas
