# Adding an agent tool

An agent tool is what lets someone write `@CunningBot can you ...` and have it
happen. Everything lives in `bot/domain/agent/agent_tools.py` except two
registration points outside it.

## The five places to touch

Miss any one and the tool silently doesn't exist. Work down this list:

1. **`TOOL_SCHEMAS`** in `agent_tools.py` — keyed by **config key**, value is an
   OpenAI function-calling schema whose `function.name` is the **function name**.
   These two names are deliberately different for some tools:

   | config key | function name |
   |---|---|
   | `weather` | `get_weather` |
   | `image` | `generate_image` |
   | `dice` | `roll_dice` |
   | `edit_image` | `edit_image` |
   | `search_gifs` | `search_gifs` |
   | `web_search` | `web_search` |
   | `read_channel` | `read_channel` |

   Pick one and use it consistently; matching the two is simplest for new tools.

2. **`async def execute_<function_name>(arguments: Dict[str, Any]) -> str`** —
   the executor. Add `channel: discord.TextChannel` as a second parameter if it
   needs to post to the channel.

3. **`TOOL_EXECUTORS`** — maps **function name** → executor. (Not the config key.)

4. **`CHANNEL_AWARE_TOOLS`** — add the function name here if and only if the
   executor takes the channel argument. Getting this wrong is a `TypeError` at
   call time, surfaced to the model as `Tool error:`.

5. **`DEFAULT_AGENT_CONFIG["tools"]`** in `bot/app/redis/agent_store.py` — add the
   **config key**, or the tool is off for every newly registered channel.

   **Then run the backfill, or the tool is invisible in every existing channel.**
   `DEFAULT_AGENT_CONFIG` is read *only* at registration time; already-registered
   channels keep the tool list stored in their own Redis record. `/agent configure`
   has **no `tools` option**, so the only in-Discord remedy is unregister plus
   re-register, which discards that channel's model, persona, and window.

   ```bash
   ssh dad@192.168.1.182 'cd /home/dad/cunningbot && \
     docker compose exec -T -e PYTHONPATH=/app -w /app cunningbot \
     python -m bot.app.redis.migrations.backfill_agent_tools --dry-run'
   ```
   Drop `--dry-run` to apply. It is idempotent and only ever adds the keys named
   in `DEFAULT_TOOLS_TO_ADD` (extend that list when you ship a tool).

   This is the single easiest way to ship a tool that tests green, registers
   correctly, and still does nothing in production.

Then: add a one-line bullet to `AGENT_SYSTEM_PROMPT` in
`bot/domain/agent/agent_service.py` describing when to reach for it. The model
follows those bullets closely; without one, a correctly registered tool goes
unused. And update `/help` if the capability is user-visible.

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
# 1. schema
TOOL_SCHEMAS: Dict[str, dict] = {
    ...,
    "restaurant_list": {
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
    },
}

# 2. executor — channel-aware because it needs guild_id
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

# 3. + 4. registration
TOOL_EXECUTORS["restaurant_list"] = execute_restaurant_list
CHANNEL_AWARE_TOOLS.add("restaurant_list")   # written inline in the literals
```

## Getting the guild

Executors get a `discord.TextChannel`, not a guild id. Use
`channel.guild.id` → `guild_id_to_str()`. Non-channel-aware tools cannot scope
to a guild at all — if a tool touches per-server state, it must be channel-aware.

## Testing

There is no test file for `agent_tools.py` yet. Test the executor directly with
a stub channel — it is a plain async function returning a string, so it needs no
Discord fixtures:

```python
class FakeChannel:
    def __init__(self, guild_id): self.guild = SimpleNamespace(id=guild_id)
```

Follow the mocking style in `tests/test_chat_command_integration.py`.
