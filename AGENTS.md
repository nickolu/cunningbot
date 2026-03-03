# CunningBot — Agent Instructions

## Project Overview

CunningBot is a Discord bot written in Python using discord.py. It runs via Docker Compose on a Raspberry Pi. All slash commands are implemented as discord.py Cogs in `bot/app/commands/`. Background worker tasks live in `bot/app/tasks/`. State is persisted in Redis.

## Key Conventions

- Commands are auto-loaded from `bot/app/commands/` — any `.py` file in a subdirectory is picked up automatically. Each file must expose a `setup(bot)` coroutine.
- Use `discord.Embed` for structured responses and `ephemeral=True` for user-only responses.
- Queued commands (`/chat`, `/image`) go through `bot/app/task_queue.py`.
- Use `bot/app/utils/logger.py` (`get_logger()`) for structured logging — not the standard `logging` module.
- All guild-specific state belongs in Redis (see `bot/app/redis/`).

## IMPORTANT: Keep `/help` Up to Date

**After adding, removing, or changing any bot command or feature, you MUST update `bot/app/commands/help.py`.**

The `/help` command is the primary user-facing documentation for the bot. The `HELP_PAGES` list in `help.py` contains five paginated Discord embeds covering every command. When making changes:

1. If you add a new command → add it to the appropriate page in `HELP_PAGES`.
2. If you remove a command → remove it from `HELP_PAGES`.
3. If you change a command's name, options, or behavior → update the corresponding description.
4. If a new page is needed (many new commands), add another embed to `HELP_PAGES` and update the `(N/5)` page counters in all embed titles.

The pages are currently organized as:
- **Page 1** — `/chat`, `/image`, `/image-json`, Edit Images context menu
- **Page 2** — `/trivia` group, `/answer`
- **Page 3** — `/news` group, `/weather` group
- **Page 4** — `/poll`, `/roll`, `/af`, `/r`, `/persona`
- **Page 5** — `/lunchboyz`, `/bot-updates`, `/queue`, `/help`

## Running Tests

```bash
make test
# or directly:
pytest tests/
```

## Deployment

Use the `deploy-to-pi` skill (`.opencode/skills/deploy-to-pi/SKILL.md`) to deploy changes to the Raspberry Pi.
