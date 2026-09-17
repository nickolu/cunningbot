# Architecture — how a message becomes a response

## Processes

`docker-compose.yml` runs **ten** containers off one image and one Redis:

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
| `framed-sync` | `bot.app.tasks.framed_sync` — reads finished days of Framed results, posts the recap; connects to Discord only when there is work | 600s |

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

1. Ignore bots and anything that is not a `TextChannel` or `Thread` (forum
   posts are threads). A thread uses its own registration, else its parent's.
2. Is the bot summoned? An @mention of the bot or of its managed role
   (`<@&role>`), a reply to it, or its name in the text.
3. Redis lookup `agent:{guild}:{channel}`. **Absent:** answer only if summoned,
   using `UNREGISTERED_AGENT_CONFIG` (logs `agent_summoned_unregistered`).
   **Disabled (paused):** stay silent, even when summoned.
4. Cooldown (default 5s) — bypassed when summoned.
5. Rate limit (default 10/min per channel).
6. **Should we respond?** `response_mode`:
   - `always` — yes.
   - `strict` — only @mention or reply.
   - `smart` (default) — @mention/reply always wins; otherwise
     `bot/domain/agent/intent_classifier.py` asks `gpt-4o-mini` for
     `RESPOND` / `IGNORE` / `ASK_CLARIFY` given the last 8 messages. It is
     tuned to lean RESPOND.
7. Per-channel `asyncio.Lock` — a second message while the agent is thinking is
   dropped, not queued. The locks live in `AGENT_CHANNEL_LOCKS` in
   `bot/app/agent_runtime.py`, shared with `/bot`.
8. `fetch_agent_history()` (also in `agent_runtime.py`) fetches
   `context_window` (default 30) messages, flattens each with
   `flatten_discord_message()`, annotate image attachments as
   `[Image: filename | URL]`, reverse to chronological. **Embed text is not
   read** — the flattener only sees `message.content`, so RSS posts, summaries,
   and other bot embeds are invisible to the agent (`read_channel` shows them
   as `[+N embed(s)]`). News lives in Redis `story_history` instead.
9. `run_agent()` → OpenAI tool-calling loop, max **5** rounds
   (`MAX_TOOL_ROUNDS`). Tools that produce rich output (images) send to the
   channel themselves and return a text summary to the model.
10. Final text is chunked by `split_message()` and sent.

Configuration is per channel via `/agent register|configure|status|pause|resume|
unregister`, stored by `bot/app/redis/agent_store.py`.

### `/bot` — the agent from a slash command

`bot/app/commands/agent/bot_command.py` runs the same agent once, in any channel,
without registration. It takes the channel's lock (busy → ephemeral "already
working" reply, before deferring), defers, picks the channel's stored config if
it is registered **and enabled**, otherwise `UNREGISTERED_AGENT_CONFIG`, then
fetches history with the same helper, appends the prompt, and calls `run_agent`
with `interaction.channel`. The reply quotes the prompt's first line, since a
slash invocation leaves no visible message, and sends with `@everyone` and role
pings disabled.

`agent_runtime.py` sits outside `bot/app/commands/` on purpose: every module in
a command directory is loaded as an extension, and discord.py re-executes an
extension's module on load, so module-level state there (like a lock dict) can
exist twice.

## Layer rules

- `bot/api/<vendor>/` — knows the vendor's wire format, nothing about Discord or
  the bot's features. One client class per capability.
- `bot/domain/<feature>/` — the actual logic. Must not import `discord`. If you
  find yourself needing a `discord.Message` here, pass plain data instead.
  (`bot/domain/agent/tools/` is the deliberate exception: channel-aware tools take a
  `discord.TextChannel` so they can upload attachments.)
- `bot/app/` — everything that knows about Discord or persistence.

## Known doc/code mismatches

- `agent_listener.py`'s docstring says agent work "runs through the existing
  TaskQueue". It does not — `run_agent` is awaited directly.
