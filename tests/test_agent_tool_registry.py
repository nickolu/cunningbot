"""Contract tests for the agent tool registry.

The registry exists so that adding a tool is adding one module rather than
editing four parallel lists that nothing kept in sync. These tests pin the
invariants that used to be maintained by hand -- most importantly that a
channel-aware tool actually takes the channel argument, which previously only
surfaced as a TypeError the first time a user triggered the tool.
"""

import inspect

import pytest

from bot.domain.agent.tools.registry import (
    CHANNEL_AWARE_TOOLS,
    DEFAULT_ENABLED_TOOLS,
    TOOL_EXECUTORS,
    TOOL_SCHEMAS,
    TOOLS,
    get_tool_schemas_for_config,
)


def test_config_keys_and_function_names_are_unique():
    assert len({t.config_key for t in TOOLS}) == len(TOOLS)
    assert len({t.function_name for t in TOOLS}) == len(TOOLS)


def test_schema_shape_matches_openai_function_calling():
    for tool in TOOLS:
        assert tool.schema["type"] == "function"
        fn = tool.schema["function"]
        assert fn["name"] == tool.function_name
        assert fn["description"].strip()
        assert fn["parameters"]["type"] == "object"


@pytest.mark.parametrize("tool", TOOLS, ids=lambda t: t.config_key)
def test_executor_signature_matches_channel_awareness(tool):
    """A channel-aware tool takes (arguments, channel); others take (arguments)."""
    params = list(inspect.signature(tool.executor).parameters)
    assert inspect.iscoroutinefunction(tool.executor)
    assert params[0] == "arguments"
    if tool.channel_aware:
        assert params[1:2] == ["channel"], tool.config_key
        assert tool.function_name in CHANNEL_AWARE_TOOLS
    else:
        assert params == ["arguments"], tool.config_key
        assert tool.function_name not in CHANNEL_AWARE_TOOLS


def test_lookups_are_derived_from_the_same_tools():
    assert list(TOOL_SCHEMAS) == [t.config_key for t in TOOLS]
    assert list(TOOL_EXECUTORS) == [t.function_name for t in TOOLS]
    assert set(DEFAULT_ENABLED_TOOLS) <= set(TOOL_SCHEMAS)


def test_stored_default_config_tracks_the_registry():
    """The drift this refactor removed: two defaults that disagreed."""
    from bot.app.redis.agent_store import DEFAULT_AGENT_CONFIG

    assert DEFAULT_AGENT_CONFIG["tools"] == DEFAULT_ENABLED_TOOLS


def test_get_tool_schemas_for_config_ignores_unknown_keys():
    schemas = get_tool_schemas_for_config(["dice", "not_a_tool"])
    assert [s["function"]["name"] for s in schemas] == ["roll_dice"]


def test_legacy_import_path_still_resolves():
    """`agent_tools` is a façade now; existing imports must keep working."""
    from bot.domain.agent import agent_tools

    assert agent_tools.TOOL_SCHEMAS is TOOL_SCHEMAS
    assert agent_tools.CHANNEL_AWARE_TOOLS is CHANNEL_AWARE_TOOLS
    for name in agent_tools.__all__:
        assert getattr(agent_tools, name, None) is not None, name
