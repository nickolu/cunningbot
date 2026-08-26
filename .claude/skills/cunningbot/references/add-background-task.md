# Adding a scheduled / recurring task

There is no in-process scheduler. A recurring job is a **standalone script** in
`bot/app/tasks/` plus a **container** in `docker-compose.yml` that re-runs it on
a sleep loop. Each tick is a fresh process: connect, do the work, exit.

This means the schedule granularity is the container's `sleep N`, and the script
itself must decide "is it time?" — see `is_time_to_post()` in `weather_poster.py`,
which matches the current local time against `HH:MM` slots within a window equal
to the poll interval.

## 1. The script

`bot/app/tasks/<name>.py`, modeled on `bot/app/tasks/weather_poster.py`:

```python
"""<name>.py

<What it does and when.>

Usage (inside Docker container):
    python -m bot.app.tasks.<name>

Wire up in docker-compose.yml:
    command: bash -c "while true; do python -m bot.app.tasks.<name>; sleep 300; done"
"""
from __future__ import annotations

import asyncio, logging, os
from zoneinfo import ZoneInfo

import discord
from dotenv import load_dotenv

load_dotenv()          # must come before the bot imports below

from bot.app.redis.client import close_redis, get_redis_client, initialize_redis
from bot.app.redis.exceptions import LockAcquisitionError
from bot.app.redis.locks import redis_lock
from bot.app.redis.<feature>_store import FeatureRedisStore

logger = logging.getLogger("MyPoster")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

DISCORD_ERROR_UNKNOWN_CHANNEL = 10003


async def run() -> None:
    await initialize_redis()
    redis_client = get_redis_client()
    store = FeatureRedisStore()

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.error("DISCORD_TOKEN not set")
        return

    intents = discord.Intents.none()
    client = discord.Client(intents=intents)

    @client.event  # type: ignore[misc]
    async def on_ready():
        for guild_id in await store.get_all_guilds_with_config(...):
            try:
                async with redis_lock(redis_client, f"feature:{guild_id}:tick", timeout=30):
                    ...  # check schedule, post, persist new state
            except LockAcquisitionError:
                logger.info(f"guild {guild_id} handled by another container, skipping")
                continue
            except Exception as e:
                logger.error(f"guild {guild_id}: {e}")
                continue
        await asyncio.sleep(0.5)
        await client.close()

    try:
        await client.start(token)
    finally:
        if not client.is_closed():
            await client.close()
        await close_redis()


if __name__ == "__main__":
    asyncio.run(run())
```

## 2. The container

```yaml
  my-poster:
    build: .
    restart: unless-stopped
    environment:
      - DISCORD_TOKEN=${DISCORD_TOKEN}
      - REDIS_HOST=redis
      - REDIS_PORT=6379
      - REDIS_DB=0
      - REDIS_PASSWORD=
    volumes:
      - ./bot/app:/app/bot/app
    depends_on:
      redis:
        condition: service_healthy
    command: bash -c "while true; do python -m bot.app.tasks.my_poster; sleep 300; done"
```

Add `OPENAI_API_KEY` only if the task actually calls OpenAI. A new service means
the deploy must rebuild (`docker compose up -d --build`), which the Pi's
auto-deploy already does.

## Rules

- **Take a lock.** `redis_lock` around each unit of work, keyed per guild or per
  channel. Without it, a restart overlap double-posts.
- **Record what you did, in Redis, before or with the send.** "Did I already post
  today's X?" must survive process exit — that's the entire state of the job.
- **Idempotent by design.** The process can die mid-tick and will be re-run.
- **Handle `discord.HTTPException` code 10003** (unknown channel) by clearing the
  stored channel rather than erroring every 5 minutes forever.
- **Close clients.** Workers leak connections otherwise; `weather_poster.py`
  explicitly closes the OpenAI client before `client.close()`.
- **Timezones:** store an IANA string in config (`America/Los_Angeles`) and use
  `zoneinfo.ZoneInfo`, falling back to UTC on a bad value. Never assume the Pi's
  local time — containers run UTC.
- Log a clear `=== <Name> Started/Finished ===` pair; `docker compose logs` is
  the only debugging surface on the Pi.

## Choosing the interval

The sleep is the resolution and the retry. A daily 9am post on a `sleep 300`
loop means a 5-minute matching window and 288 wake-ups a day — that's the
existing pattern and it's fine. Don't go below 60s.
