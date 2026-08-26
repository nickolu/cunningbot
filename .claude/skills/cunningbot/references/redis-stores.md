# Redis state

Redis is the only durable store. It is AOF-persisted (`appendfsync everysec`),
capped at 2gb with `allkeys-lru` — **eviction is possible**, so treat Redis as
durable-enough state, not as a system of record for anything irreplaceable.

## Key schema

```
<feature>:<guild_id>:<thing>
```

Existing prefixes: `agent:`, `trivia:`, `rss:`, `weather:`, `bot_updates:`,
`lock:`. Values are JSON strings (`json.dumps`), read back with `json.loads`
guarded by `try/except JSONDecodeError` returning `None`.

`agent:` is the one keyed per channel: `agent:{guild_id}:{channel_id}`.

Always stringify ids through `bot/app/redis/serialization.py`
(`guild_id_to_str`, `channel_id_to_str`) so keys can never diverge between a
cog that had an `int` and a worker that had a `str`.

## Store class

One file per feature, `bot/app/redis/<feature>_store.py`. Document the key
schema in the module docstring — that docstring is the schema of record.

```python
"""Redis storage layer for <feature>.

Key schema:
    <feature>:{guild_id}:config   # JSON: {...}
    <feature>:{guild_id}:items    # JSON: [...]
"""

import json
import logging
from typing import Any, Dict, List, Optional

from bot.app.redis.client import get_redis_client

logger = logging.getLogger("FeatureRedisStore")


class FeatureRedisStore:
    def __init__(self):
        self.redis_client = get_redis_client()
        self.redis = self.redis_client.redis

    def _key(self, guild_id: str, suffix: str) -> str:
        return f"feature:{guild_id}:{suffix}"

    async def get_items(self, guild_id: str) -> List[Dict[str, Any]]:
        data = await self.redis.get(self._key(guild_id, "items"))
        if not data:
            return []
        try:
            return json.loads(data)
        except json.JSONDecodeError as e:
            logger.error(f"Bad items JSON for guild {guild_id}: {e}")
            return []

    async def save_items(self, guild_id: str, items: List[Dict[str, Any]]) -> None:
        await self.redis.set(self._key(guild_id, "items"), json.dumps(items))
```

Instantiate the store lazily (a module-level instance would connect at import
time, before `initialize_redis()` has run). Cogs typically build it inside the
command; the agent listener caches it behind a property.

## Guild discovery

Workers need "every guild that has this configured". The convention is a `SCAN`
over `feature:*:config` — copy `get_all_guilds_with_config` from any existing
store. Use `SCAN`, never `KEYS`.

## Concurrency

Nine containers share this Redis. Read-modify-write on a JSON blob is a lost
update waiting to happen.

- **Cross-container coordination** — `redis_lock` from `bot/app/redis/locks.py`:
  ```python
  from bot.app.redis.locks import redis_lock
  async with redis_lock(redis_client, f"feature:{guild_id}:advance", timeout=30):
      ...
  ```
  It is `SET NX EX` plus a compare-and-delete Lua release, and raises
  `LockAcquisitionError` when the lock is held — catch it and skip the tick.
- **Genuinely atomic multi-step updates** — a Lua script in
  `bot/app/redis/scripts/*.lua`, auto-loaded at startup by `RedisClient` and
  called by stem name. Trivia's answer submission is the worked example.

## Migrations

`bot/app/redis/migrations/` holds one-shot scripts (`migrate_rss.py`,
`migrate_trivia.py`) for changing a key's shape. If you change an existing
value's schema, either write one or make the reader tolerate both shapes —
production Redis on the Pi has live data.
