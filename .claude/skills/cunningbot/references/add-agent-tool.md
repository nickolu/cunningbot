# Adding an agent tool

An agent tool is what lets someone write `@CunningBot can you ...` and have it
happen. Each tool is one module in `bot/domain/agent/tools/`, and adding one is
adding a file plus a line in `registry.py`.

## The recipe

1. **Create `bot/domain/agent/tools/<config_key>.py`** holding three things: a
   module-level `SCHEMA`, an `async def execute_<function_name>(...)`, and a
   `TOOL = AgentTool(...)` at the bottom. Copy the nearest existing tool.

2. **Add the module to `_MODULES` in `bot/domain/agent/tools/registry.py`.** That
   list is ordered and user-visible — it drives the `/agent` tool picker — so
   append at the end.

Everything else is derived: `TOOL_SCHEMAS`, `TOOL_EXECUTORS`,
`CHANNEL_AWARE_TOOLS`, and the default tool list for new channels.
`tests/test_agent_tool_registry.py` fails if a tool's `channel_aware` flag and
its executor signature disagree, which used to be a `TypeError` discovered in
production.

### The two names

`config_key` is what a channel's stored config lists and what `/agent` shows;
the schema's `function.name` is what the model emits. They differ for historical
reasons on some tools:

| config key | function name |
|---|---|
| `weather` | `get_weather` |
| `image` | `generate_image` |
| `dice` | `roll_dice` |
| `edit_image` | `edit_image` |

Match the two for anything new.

### Reaching existing channels

`default_enabled=True` (the default) puts a tool in `DEFAULT_ENABLED_TOOLS`,
which covers newly registered channels and channels with no registration at all.
Set `default_enabled=False` for anything that should be opt-in — a tool that
writes somewhere public, for instance.

**Already-registered channels still need the backfill.** They keep the tool list
stored in their own Redis record, so a default-on tool does not reach them on
merge. `/agent tool <name> enable` turns one on in one channel; the backfill
below adds one to every channel in bulk. Do not backfill a `default_enabled=False`
tool -- that hands it to everyone, which is the thing opting in was for.

```bash
ssh dad@192.168.1.182 'cd /home/dad/cunningbot && \
  docker compose exec -T -e PYTHONPATH=/app -w /app cunningbot \
  python -m bot.app.redis.migrations.backfill_agent_tools --dry-run'
```

Drop `--dry-run` to apply. It is idempotent and only ever adds the keys named in
`DEFAULT_TOOLS_TO_ADD` (extend that list when you ship a tool). This is the
single easiest way to ship a tool that tests green, registers correctly, and
still does nothing in production.

Then: add a one-line bullet to `AGENT_SYSTEM_PROMPT` in
`bot/domain/agent/agent_service.py` describing when to reach for it. The model
follows those bullets closely; without one, a correctly registered tool goes
unused. And update `/help` if the capability is user-visible.

`bot/domain/agent/agent_tools.py` is now a thin re-export façade kept for the
existing imports in the test suite. Import from
`bot.domain.agent.tools.registry` in new code.

## Executor contract

- **Return a string.** It goes back to the model as the tool result, and the
  model paraphrases it. Return something a model can read — a short formatted
  summary, not a JSON dump.
- **Never raise.** `run_agent` catches and feeds `Tool error: {e}` to the model,
  which will apologize in-channel. Catch expected failures and return a plain
  sentence: `"Web search is not available (PERPLEXITY_API_KEY not configured)."`
- **Side effects go out through `channel`.** Images, files, and embeds are sent
  by the executor directly; the returned string just tells the model what
  happened so it can comment on it.
- **Budget: 5 tool rounds per message.** A tool that needs three calls to be
  useful will starve the conversation.
- **Keep the schema small.** Every schema is sent on every LLM call for every
  message in every agent channel. Few parameters, tight enums, real defaults.

## Skeleton

```python
"""The `restaurant_list` agent tool."""

from typing import Any, Dict

import discord

from bot.app.utils.logger import get_logger
from bot.domain.agent.tools.base import AgentTool

logger = get_logger()


SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "restaurant_list",
        "description": (
            "Read or modify this server's list of restaurants to visit. "
            "Use action='add' when someone mentions a place worth trying."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "add", "remove"]},
                "name": {"type": "string", "description": "Restaurant name"},
                "note": {"type": "string", "description": "Neighborhood or why it came up"},
            },
            "required": ["action"],
        },
    },
}


async def execute_restaurant_list(
    arguments: Dict[str, Any], channel: discord.TextChannel
) -> str:
    store = ListRedisStore()
    guild_id = guild_id_to_str(channel.guild.id)
    action = arguments.get("action", "list")

    if action == "add":
        name = (arguments.get("name") or "").strip()
        if not name:
            return "No restaurant name was provided."
        await store.add_item(guild_id, "restaurants", name, arguments.get("note"))
        return f"Added '{name}' to the restaurant list."

    items = await store.get_items(guild_id, "restaurants")
    if not items:
        return "The restaurant list is empty."
    return "Restaurants to visit:\n" + "\n".join(f"- {i['name']}" for i in items)


TOOL = AgentTool(
    config_key="restaurant_list",
    schema=SCHEMA,
    executor=execute_restaurant_list,
    channel_aware=True,
)
```

Then add `restaurant_list` to `_MODULES` in `registry.py`. That is the whole
registration.

## Getting the guild

Executors get a `discord.TextChannel`, not a guild id. Use
`channel.guild.id` → `guild_id_to_str()`. Non-channel-aware tools cannot scope
to a guild at all — if a tool touches per-server state, it must be channel-aware.

## Testing

`tests/test_agent_tool_registry.py` already covers registration for every
tool, so a new tool needs no wiring test. Test the executor directly with
a stub channel — it is a plain async function returning a string, so it needs no
Discord fixtures:

```python
class FakeChannel:
    def __init__(self, guild_id): self.guild = SimpleNamespace(id=guild_id)
```

Follow the mocking style in `tests/test_chat_command_integration.py`.
