# Architecture — how a message becomes a response

## Processes

`docker-compose.yml` runs **nine** containers off one image and one Redis:

| Service | What it does | Cadence |
|---|---|---|
| `redis` | AOF-persisted state, 2gb cap, `allkeys-lru` | — |
| `cunningbot` | The gateway connection: all slash commands + the agent listener | long-running |
| `rssfeed` | `bot.app.tasks.rss_feed_poster` | 600s |
| `rsssummary` | `bot.app.tasks.rss_summary_poster` | 600s |
| `breaking-news-validator` | `bot.app.tasks.breaking_news_validator` | 120s |
| `trivia-poster` | `bot.app.tasks.trivia_game_poster` | 600s |
| `trivia-closer` | `bot.app.tasks.trivia_game_closer` | 60s |
| `trivia-weekly-reset` | `bot.app.tasks.trivia_weekly_reset` | 600s |
| `weather-poster` | `bot.app.tasks.weather_poster` | 300s |

Workers are `bash -c "while true; do python -m ...; sleep N; done"` — each tick is
a fresh process that connects to Discord, does its work, and exits. They share
Redis with the main bot, which is how a worker's post shows up as bot state.

`./bot/app` is bind-mounted into every container; `bot/domain` and `bot/api` are
baked into the image. Code changes under `domain`/`api` need `--build`.

## Path A — slash command

```
Discord → discord.py app_command → Cog in bot/app/commands/<feature>/
        → service in bot/domain/<feature>/
        → client in bot/api/<vendor>/  and/or  store in bot/app/redis/
        → interaction.followup.send(embed=...)
```

`/chat` and `/image` additionally go through `bot/app/task_queue.py` (a
single-worker asyncio queue, max 10 deep) so long LLM calls don't block the
gateway. Decorate with `@queued_task` or enqueue explicitly; `/queue` shows depth.

Cogs are discovered by scanning `bot/app/commands/` in `bot/main.py` — every
`.py` file inside a subdirectory is loaded as an extension and must define
`async def setup(bot)`. Load failures are logged, not fatal.

## Path B — channel agent

`bot/app/commands/agent/agent_listener.py` is an `on_message` cog. Per message:

1. Ignore bots and non-`TextChannel`s.
2. Redis lookup `agent:{guild}:{channel}` — bail if absent or disabled.
3. Cooldown (default 5s) — bypassed if @mentioned or replied to.
4. Rate limit (default 10/min per channel).
5. **Should we respond?** `response_mode`:
   - `always` — yes.
   - `strict` — only @mention or reply.
   - `smart` (default) — @mention/reply always wins; otherwise
     `bot/domain/agent/intent_classifier.py` asks `gpt-4o-mini` for
     `RESPOND` / `IGNORE` / `ASK_CLARIFY` given the last 8 messages. It is
     tuned to lean RESPOND.
6. Per-channel `asyncio.Lock` — a second message while the agent is thinking is
   dropped, not queued.
7. Fetch `context_window` (default 30) messages, flatten each with
   `flatten_discord_message()`, annotate image attachments as
   `[Image: filename | URL]`, reverse to chronological.
8. `run_agent()` → OpenAI tool-calling loop, max **5** rounds
   (`MAX_TOOL_ROUNDS`). Tools that produce rich output (images) send to the
   channel themselves and return a text summary to the model.
9. Final text is chunked by `split_message()` and sent.

Configuration is per channel via `/agent register|configure|status|pause|resume|
unregister`, stored by `bot/app/redis/agent_store.py`.

## Layer rules

- `bot/api/<vendor>/` — knows the vendor's wire format, nothing about Discord or
  the bot's features. One client class per capability.
- `bot/domain/<feature>/` — the actual logic. Must not import `discord`. If you
  find yourself needing a `discord.Message` here, pass plain data instead.
  (`agent_tools.py` is the deliberate exception: channel-aware tools take a
  `discord.TextChannel` so they can upload attachments.)
- `bot/app/` — everything that knows about Discord or persistence.

## Known doc/code mismatches

- `agent_listener.py`'s docstring says agent work "runs through the existing
  TaskQueue". It does not — `run_agent` is awaited directly.
- `AGENTS.md` says `make test`; there is no such target.
- `AGENTS.md`'s `/help` page listing lags the actual `HELP_PAGES`.
