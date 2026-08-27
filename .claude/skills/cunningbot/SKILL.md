---
name: cunningbot
description: Work on CunningBot — the Discord bot in this repo. Use for adding or changing slash commands, agent tools, background tasks, Redis state, or personas; for understanding how a request flows from Discord to a response; and for testing and deploying to the Raspberry Pi. Load before editing anything under bot/.
---

# CunningBot

A Discord bot (Python, discord.py 2.5, OpenAI) running under Docker Compose on a
Raspberry Pi at `dad@192.168.1.182:/home/dad/cunningbot`. State lives in Redis.
Merging a PR to `main` auto-deploys within ~2 minutes.

## Two ways users reach the bot

Every feature is exposed through one or both of these. Decide which before you write code.

1. **Slash command** — `/trivia`, `/weather`, `/image`. Explicit, discoverable,
   parameter-validated by Discord. Cogs in `bot/app/commands/<feature>/`.
2. **Channel agent** — plain English at the bot (`@CunningBot can you...`). An
   `on_message` listener decides whether the bot is being addressed, then runs an
   OpenAI tool-calling loop. One module per tool in `bot/domain/agent/tools/`,
   collected by `registry.py`.

A capability that should work both ways needs a cog *and* an agent tool, both
delegating to one service in `bot/domain/<feature>/`. Do not duplicate logic
between them.

## Repo map

| Path | Holds | Rule |
|---|---|---|
| `bot/api/<vendor>/` | Outbound clients (openai, google, perplexity, openmeteo, opentdb, animation_factory) | No discord.py, no business logic |
| `bot/domain/<feature>/` | Business logic and services | No discord.py imports, no Redis keys inline |
| `bot/app/commands/<feature>/` | discord.py Cogs (slash commands) | Thin — parse, call domain, format embed |
| `bot/app/tasks/` | Standalone worker scripts run on a loop | Each is a `python -m` entry point, not a cog |
| `bot/app/redis/*_store.py` | One store class per feature; owns its key schema | All persistence goes through a store |
| `bot/app/utils/` | logger, zip lookup, feed fetch | |
| `tests/` | pytest suite | 8 failures are pre-existing on `main` (af, chat, image, google) |
| `web/` | Vercel app that hosts published pages — deploys separately | |

## Non-negotiables

1. **Update `/help`.** Any command added, removed, or changed → edit `HELP_PAGES`
   in `bot/app/commands/help.py`. Five paginated embeds; titles carry `(N/5)`
   counters that must all change if you add a page. This is the only user-facing
   documentation of the bot.
2. **No direct Redis calls outside `bot/app/redis/`.** Add a method to the
   feature's store instead. See `references/redis-stores.md`.
3. **Guild-scoped everything.** Keys are `feature:{guild_id}:...`. Use
   `guild_id_to_str()` / `channel_id_to_str()` from `bot/app/redis/serialization.py`
   — never raw ints.
4. **`get_logger()` from `bot/app/utils/logger.py`** for new code (structured
   JSONL into `logs/`). Older stores use stdlib `logging`; don't propagate that.
5. **Slash commands cap at 25 options.** `/image-json` is already at the limit.
6. **Never push to `main`.** It is protected and it auto-deploys. Feature branch → PR.

## Task router

| Doing this | Read |
|---|---|
| New or changed slash command | `references/add-slash-command.md` |
| New agent capability (`@CunningBot do X`) | `references/add-agent-tool.md` |
| Recurring / scheduled post | `references/add-background-task.md` |
| New persisted state | `references/redis-stores.md` |
| Publishing content as a web page | `references/publishing.md` |
| Shipping it | `references/deploy.md` |
| How a message becomes a response | `references/architecture.md` |

## Verify before shipping

```bash
python3 -m pytest tests/          # 183 tests, ~45s, no network or keys needed
python3 -m pytest tests/test_trivia_points.py -q   # single file
```

- Use **`python3`**, not `.venv/bin/python` — the checked-in `.venv` is broken
  (dyld error). System Python is 3.9; the Docker image is 3.11, so 3.10+ syntax
  (`X | Y` unions at runtime, `match`) passes in the container but breaks local
  tests. Stick to `Optional[X]` / `Dict[...]` from `typing`.
- `make test` does **not** exist despite what `AGENTS.md` says. `make` targets are
  `up/down/build/start/stop/restart/rebuild/logs`.
- `mypy.ini` is `strict = True` but the codebase is not clean under it; don't
  chase unrelated mypy errors.

## Gotchas

- **Chat models live in one table.** `bot/domain/llm/models.py` feeds the
  `/chat` and `/agent` pickers, the API client's request kwargs, and the role
  defaults (`DEFAULT_CHAT_MODEL`, `DEFAULT_AGENT_MODEL`, `UTILITY_MODEL`).
  Add a row, add the id to `PermittedModelType`, then run
  `python3 scripts/check_models.py` -- appearing in the account's
  `/v1/models` listing does **not** mean a model works. `-pro` and `-codex`
  variants are listed but only serve `/v1/responses`, which this bot does not
  use; two of them shipped in the `/chat` picker as guaranteed errors.

- **Cogs autoload by directory scan** (`bot/main.py`). Any `.py` in a
  `bot/app/commands/*/` subdirectory is loaded and must expose
  `async def setup(bot)`. A file without it fails to load — the bot still starts
  and only logs the error, so check startup logs.
- **`bot/app` is bind-mounted** in `docker-compose.yml`, so state files persist
  across rebuilds. `bot/domain` and `bot/api` are baked into the image — changes
  there need a rebuild, not a restart.
- **Nine containers share one Redis.** Anything a worker does on a timer needs
  `redis_lock()` from `bot/app/redis/locks.py` or it fires once per container.
- `bot/app/commands/baseball/` and `daily_game/` contain only stale
  `__pycache__` — leftovers from unmerged branches, not features.
- `/lunchboyz` was removed in PR #32. If someone asks for a rotation feature,
  it is a rebuild, not a revival — `git show 9a9da2c` has the old implementation
  worth cribbing from.
