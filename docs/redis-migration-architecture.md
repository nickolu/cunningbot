# Redis Migration Architecture - CunningBot

## Executive Summary

This document establishes the architectural foundation for migrating CunningBot from JSON file-based state management to Redis. The bot consists of 7 Docker containers (1 main bot + 6 background task containers) that currently share state via JSON files, causing race conditions. This plan defines consistent patterns, conventions, and standards that all migration efforts must follow.

## Current State Analysis

### Existing JSON Files
- **app_state.json** (~16KB): Guild configs, RSS feeds, trivia games, schedules, daily games, personas
- **pending_news.json** (~2.3MB): Queued articles awaiting summary posting
- **pending_breaking_news.json**: Breaking news validation queue
- **story_history.json** (~34KB): Deduplication tracking with time windows

### Current Architecture Issues
1. **Race Conditions**: Multiple containers read-modify-write the same JSON files concurrently
2. **No Atomicity**: File I/O operations are not atomic, leading to data corruption risks
3. **Reload Every Access**: Each access reloads entire file from disk (performance overhead)
4. **Large File Contention**: pending_news.json at 2.3MB becomes a bottleneck
5. **No Locking**: No mechanism to prevent concurrent modifications

### Current Access Patterns
- **High-frequency reads**: trivia submissions, RSS feed checks, state queries
- **Moderate writes**: Adding pending articles, updating trivia games
- **Bulk operations**: Summary generation clearing thousands of articles
- **Background tasks**: 6 containers polling state every 60-600 seconds

---

## 1. Redis Key Naming Convention

### Standard Pattern
```
{namespace}:{resource_type}:{guild_id}[:subkey][:identifier]
```

### Namespace Organization

#### Global Namespace (`global:`)
For data shared across all guilds:
```
global:config:default_persona                    # String
global:stats:bot_version                         # String
```

#### Guild Namespace (`guild:{guild_id}:`)
All guild-specific data:
```
guild:{guild_id}:config:current_personality      # String
guild:{guild_id}:config:default_persona          # String
```

#### RSS Feed Namespace (`rss:{guild_id}:`)
```
rss:{guild_id}:feeds                             # Hash: feed_name -> JSON config
rss:{guild_id}:feed:{feed_name}:seen_items       # Set: item IDs
rss:{guild_id}:pending:{channel_id}              # Hash: feed_name -> JSON array of articles
```

#### Trivia Namespace (`trivia:{guild_id}:`)
```
trivia:{guild_id}:registrations                  # Hash: reg_id -> JSON config
trivia:{guild_id}:games:active                   # Hash: game_id -> JSON game data
trivia:{guild_id}:games:history                  # Sorted Set: score=timestamp, member=game_id
trivia:{guild_id}:game:{game_id}:submissions     # Hash: user_id -> JSON submission
```

#### Breaking News Namespace (`breaking:{guild_id}:`)
```
breaking:{guild_id}:pending                      # List: JSON pending items (FIFO queue)
breaking:{guild_id}:topics                       # Set: configured topic keywords
breaking:{guild_id}:channels                     # Hash: topic -> channel_id
```

#### Story History Namespace (`history:{guild_id}:`)
```
history:{guild_id}:stories:{channel_id}          # Sorted Set: score=timestamp, member=story_hash
history:{guild_id}:story:{story_hash}            # String: JSON story data with TTL
history:{guild_id}:config:dedup_window           # Hash: channel_id -> hours
```

#### Daily Games Namespace (`daily:{guild_id}:`)
```
daily:{guild_id}:games                           # Hash: game_name -> JSON config
daily:{guild_id}:schedule                        # Sorted Set: score=hour*60+minute, member=game_name
```

### Key Naming Rules

1. **Always use colons** as separators (Redis convention)
2. **Use lowercase** for namespace and resource types
3. **IDs as strings**: Guild/channel/user IDs are strings (e.g., "844003671334977607")
4. **Descriptive names**: `feeds` not `f`, `submissions` not `sub`
5. **Singular for individual items**, plural for collections
6. **No spaces or special characters** in key segments

### Special Keys

```
# Locks for critical sections
lock:{resource_type}:{guild_id}:{identifier}     # String with TTL

# Metadata
meta:migration:version                           # String
meta:last_backup                                 # String (ISO timestamp)
```

---

## 2. Data Structure Guidelines

### Decision Matrix: Which Redis Type to Use

| Use Case | Redis Type | Reasoning |
|----------|-----------|-----------|
| Simple config value (string, number, bool) | String | Direct get/set, minimal overhead |
| Multiple related config fields | Hash | Atomic field updates, efficient for objects |
| Collection of unique items (seen_items) | Set | O(1) membership testing, automatic dedup |
| Time-ordered data (game history, stories) | Sorted Set | Range queries by time, automatic ordering |
| FIFO queue (breaking news) | List | LPUSH/RPOP for queue semantics |
| Complex nested objects | String (JSON) | When structure is opaque and updated atomically |

### Detailed Structure Patterns

#### Pattern 1: Configuration Objects (Use Hash)
**When**: Multiple related fields that may be updated independently
```redis
# Good: Individual fields can be updated atomically
HSET rss:844003671334977607:feeds "San Diego News" '{"url":"...","enabled":true}'
HGET rss:844003671334977607:feeds "San Diego News"
HDEL rss:844003671334977607:feeds "Old Feed"
```

#### Pattern 2: Large Opaque Objects (Use String + JSON)
**When**: Object is always read/written as a whole unit
```redis
# Good: Game data is complex and updated atomically
SET trivia:844003671334977607:game:abc123 '{"question":"...","options":[...],"submissions":{...}}'
GET trivia:844003671334977607:game:abc123
```

#### Pattern 3: Deduplication Sets (Use Set)
**When**: Need to check "have we seen this before?"
```redis
# Good: O(1) membership check
SADD rss:844003671334977607:feed:san_diego:seen_items "article_id_12345"
SISMEMBER rss:844003671334977607:feed:san_diego:seen_items "article_id_12345"
SCARD rss:844003671334977607:feed:san_diego:seen_items  # Count
# Trim to max size
SPOP rss:844003671334977607:feed:san_diego:seen_items 50  # Remove oldest
```

#### Pattern 4: Time-Based History (Use Sorted Set)
**When**: Need to query by time range or maintain order
```redis
# Good: Efficient time-based queries and cleanup
ZADD history:844003671334977607:stories:123456 1737590400 "story_hash_abc"
ZRANGEBYSCORE history:844003671334977607:stories:123456 1737500000 +inf  # Get recent
ZREMRANGEBYSCORE history:844003671334977607:stories:123456 -inf 1737400000  # Cleanup old
```

#### Pattern 5: Queues (Use List)
**When**: Need FIFO processing
```redis
# Good: Natural queue semantics
LPUSH breaking:844003671334977607:pending '{"article":{...},"topic":"hurricane"}'
RPOP breaking:844003671334977607:pending  # Process oldest
LLEN breaking:844003671334977607:pending  # Queue depth
```

### Nested JSON Data Guidelines

**Keep as JSON strings when**:
- Structure is deeply nested (>2 levels)
- Object is always read/written as a unit
- Fields are rarely updated independently
- Size is moderate (<100KB)

**Decompose into Redis structures when**:
- Need to update individual fields frequently
- Need to query/filter by specific fields
- Structure is naturally a collection (list, set)
- Need atomic operations on subsets

### TTL/Expiration Strategy

```python
# Ephemeral data (auto-cleanup)
SETEX lock:trivia:844003671334977607:game:abc123 30 "locked"  # 30 second lock

# Story history with configurable window
ZADD history:{guild_id}:stories:{channel_id} {timestamp} {story_hash}
# Cleanup via Lua script or background task

# Game results (keep for 7 days)
SET trivia:{guild_id}:game:{game_id}:result '{...}' EX 604800

# Active games (expire if stale)
SET trivia:{guild_id}:game:{game_id} '{...}' EX 86400

# Pending articles (expire after 48 hours)
SET rss:{guild_id}:pending:{channel_id}:{feed_name}:{timestamp} '{...}' EX 172800
```

**TTL Rules**:
1. **Locks**: 10-60 seconds (prevent deadlocks)
2. **Active games**: 24 hours (auto-cleanup abandoned games)
3. **History data**: Based on dedup window config (6-168 hours)
4. **Results/archives**: 7-30 days
5. **Configuration**: No TTL (persistent)

---

## 3. Common Atomic Operation Patterns

### Pattern A: Simple Read-Modify-Write (WATCH + MULTI)

**Use for**: Infrequent conflicts, simple logic
```python
async def increment_retry_count(guild_id: str, item_index: int) -> int:
    """Increment retry count with optimistic locking."""
    key = f"breaking:{guild_id}:pending"

    while True:
        await redis.watch(key)

        # Read current state
        items = await redis.lrange(key, 0, -1)
        if item_index >= len(items):
            await redis.unwatch()
            return -1

        item_data = json.loads(items[item_index])
        item_data['retry_count'] = item_data.get('retry_count', 0) + 1

        # Atomic update
        pipe = redis.pipeline()
        pipe.lset(key, item_index, json.dumps(item_data))

        try:
            await pipe.execute()
            return item_data['retry_count']
        except redis.WatchError:
            # Retry on conflict
            continue
```

### Pattern B: Lua Scripts (Complex Atomic Operations)

**Use for**: Complex logic, multiple keys, high contention
```lua
-- trivia_submit_answer.lua
-- Atomically check game state, validate timing, and record submission
local game_key = KEYS[1]
local submission_key = KEYS[2]
local user_id = ARGV[1]
local answer_data = ARGV[2]
local current_time = tonumber(ARGV[3])

-- Load game data
local game_json = redis.call('GET', game_key)
if not game_json then
    return {err = "Game not found"}
end

local game = cjson.decode(game_json)

-- Check if game is still open
if current_time > game.ends_at then
    return {err = "Game closed"}
end

-- Record submission
redis.call('HSET', submission_key, user_id, answer_data)

-- Update submission count in game
game.submission_count = (game.submission_count or 0) + 1
redis.call('SET', game_key, cjson.encode(game))

return {ok = "Submitted"}
```

**Loading Lua scripts**:
```python
class RedisClient:
    def __init__(self):
        self._lua_scripts = {}

    async def load_scripts(self):
        """Load Lua scripts on initialization."""
        script_dir = Path(__file__).parent / "redis_scripts"

        for script_file in script_dir.glob("*.lua"):
            script_name = script_file.stem
            script_content = script_file.read_text()
            self._lua_scripts[script_name] = await self.redis.script_load(script_content)

    async def execute_script(self, script_name: str, keys: list, args: list):
        """Execute a loaded Lua script."""
        script_hash = self._lua_scripts[script_name]
        return await self.redis.evalsha(script_hash, len(keys), *keys, *args)
```

### Pattern C: Distributed Locks

**Use for**: Critical sections, preventing duplicate processing
```python
from contextlib import asynccontextmanager
import uuid

@asynccontextmanager
async def redis_lock(redis_client, resource: str, timeout: int = 10):
    """Distributed lock with automatic release."""
    lock_key = f"lock:{resource}"
    lock_value = str(uuid.uuid4())
    acquired = False

    try:
        # Acquire lock with SET NX EX
        acquired = await redis_client.set(
            lock_key,
            lock_value,
            nx=True,  # Only set if not exists
            ex=timeout
        )

        if not acquired:
            raise LockAcquisitionError(f"Could not acquire lock: {resource}")

        yield
    finally:
        if acquired:
            # Lua script to ensure we only delete our own lock
            lua_script = """
            if redis.call("get", KEYS[1]) == ARGV[1] then
                return redis.call("del", KEYS[1])
            else
                return 0
            end
            """
            await redis_client.eval(lua_script, 1, lock_key, lock_value)

# Usage
async with redis_lock(redis_client, f"trivia:game:{game_id}"):
    # Critical section - only one container can execute this
    game_data = await get_game_data(game_id)
    game_data['status'] = 'closed'
    await save_game_data(game_id, game_data)
```

### Pattern D: Pipeline for Bulk Operations

**Use for**: Multiple independent operations, reduce round-trips
```python
async def clear_pending_articles(guild_id: str, channel_id: int) -> int:
    """Clear all pending articles for a channel using pipeline."""
    pattern = f"rss:{guild_id}:pending:{channel_id}:*"

    # Get all keys matching pattern
    keys = []
    async for key in redis_client.scan_iter(match=pattern):
        keys.append(key)

    if not keys:
        return 0

    # Delete all in a pipeline
    pipe = redis_client.pipeline()
    for key in keys:
        pipe.delete(key)

    results = await pipe.execute()
    return sum(results)
```

### When to Use Which Pattern

| Scenario | Pattern | Reason |
|----------|---------|--------|
| Single key increment | INCR/HINCRBY | Built-in atomic |
| Simple field update | HSET/SET | Native atomic |
| Check-then-set with retry acceptable | WATCH + MULTI | Simple, handles rare conflicts |
| Complex logic across multiple keys | Lua Script | True atomicity, no network overhead |
| Prevent duplicate processing | Distributed Lock | Coordination between containers |
| Many independent operations | Pipeline | Reduce network latency |

---

## 4. Redis Client/Connection Management

### Module Structure

```
bot/app/redis/
├── __init__.py
├── client.py                 # Main Redis client singleton
├── connection_pool.py        # Connection pool configuration
├── exceptions.py             # Custom exceptions
├── locks.py                  # Distributed lock utilities
├── scripts/                  # Lua scripts
│   ├── trivia_submit.lua
│   ├── cleanup_history.lua
│   └── batch_add_seen.lua
└── migrations/               # Data migration utilities
    ├── json_to_redis.py
    └── verify_migration.py
```

### Client Implementation

```python
# bot/app/redis/client.py
import os
import logging
from typing import Optional
import redis.asyncio as redis
from redis.asyncio.connection import ConnectionPool
from pathlib import Path

logger = logging.getLogger("RedisClient")

class RedisClient:
    """Singleton Redis client with connection pooling."""

    _instance: Optional['RedisClient'] = None
    _redis: Optional[redis.Redis] = None
    _pool: Optional[ConnectionPool] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def initialize(self):
        """Initialize Redis connection pool."""
        if self._redis is not None:
            return  # Already initialized

        redis_host = os.getenv("REDIS_HOST", "redis")
        redis_port = int(os.getenv("REDIS_PORT", "6379"))
        redis_db = int(os.getenv("REDIS_DB", "0"))
        redis_password = os.getenv("REDIS_PASSWORD")

        # Connection pool configuration
        self._pool = ConnectionPool(
            host=redis_host,
            port=redis_port,
            db=redis_db,
            password=redis_password,
            encoding="utf-8",
            decode_responses=True,
            max_connections=20,  # Tune based on container count
            socket_timeout=5,
            socket_connect_timeout=5,
            retry_on_timeout=True,
            health_check_interval=30,
        )

        self._redis = redis.Redis(connection_pool=self._pool)

        # Test connection
        try:
            await self._redis.ping()
            logger.info(f"Redis connected: {redis_host}:{redis_port} (DB {redis_db})")
        except Exception as e:
            logger.error(f"Redis connection failed: {e}")
            raise

        # Load Lua scripts
        await self._load_lua_scripts()

    async def _load_lua_scripts(self):
        """Load Lua scripts for atomic operations."""
        self._scripts = {}
        script_dir = Path(__file__).parent / "scripts"

        if not script_dir.exists():
            logger.warning(f"Lua scripts directory not found: {script_dir}")
            return

        for script_file in script_dir.glob("*.lua"):
            script_name = script_file.stem
            script_content = script_file.read_text()

            try:
                script_sha = await self._redis.script_load(script_content)
                self._scripts[script_name] = script_sha
                logger.info(f"Loaded Lua script: {script_name}")
            except Exception as e:
                logger.error(f"Failed to load Lua script {script_name}: {e}")

    async def close(self):
        """Close Redis connection pool."""
        if self._redis:
            await self._redis.close()
            await self._pool.disconnect()
            logger.info("Redis connection closed")

    @property
    def redis(self) -> redis.Redis:
        """Get Redis client instance."""
        if self._redis is None:
            raise RuntimeError("Redis client not initialized. Call initialize() first.")
        return self._redis

    async def execute_script(self, script_name: str, keys: list, args: list):
        """Execute a loaded Lua script."""
        if script_name not in self._scripts:
            raise ValueError(f"Lua script not loaded: {script_name}")

        script_sha = self._scripts[script_name]
        return await self._redis.evalsha(script_sha, len(keys), *keys, *args)

# Global singleton instance
_redis_client: Optional[RedisClient] = None

def get_redis_client() -> RedisClient:
    """Get the global Redis client instance."""
    global _redis_client
    if _redis_client is None:
        _redis_client = RedisClient()
    return _redis_client

async def initialize_redis():
    """Initialize Redis client (call on bot startup)."""
    client = get_redis_client()
    await client.initialize()

async def close_redis():
    """Close Redis client (call on bot shutdown)."""
    client = get_redis_client()
    await client.close()
```

### Error Handling & Retry Strategy

```python
# bot/app/redis/exceptions.py
class RedisOperationError(Exception):
    """Base exception for Redis operations."""
    pass

class LockAcquisitionError(RedisOperationError):
    """Failed to acquire distributed lock."""
    pass

class RetryableRedisError(RedisOperationError):
    """Redis error that can be retried."""
    pass

# bot/app/redis/decorators.py
import asyncio
from functools import wraps
from redis.exceptions import ConnectionError, TimeoutError

def redis_retry(max_retries: int = 3, backoff: float = 0.5):
    """Retry decorator for Redis operations."""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None

            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except (ConnectionError, TimeoutError) as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        wait_time = backoff * (2 ** attempt)
                        logger.warning(
                            f"Redis operation failed (attempt {attempt + 1}/{max_retries}), "
                            f"retrying in {wait_time}s: {e}"
                        )
                        await asyncio.sleep(wait_time)
                    else:
                        logger.error(f"Redis operation failed after {max_retries} attempts")

            raise RetryableRedisError(f"Max retries exceeded") from last_exception
        return wrapper
    return decorator
```

### Environment Configuration

```bash
# .env
REDIS_HOST=redis               # Docker service name or localhost
REDIS_PORT=6379
REDIS_DB=0                     # Use different DBs for dev/test/prod if needed
REDIS_PASSWORD=                # Optional password
REDIS_MAX_CONNECTIONS=20       # Per container
```

---

## 5. Migration Strategy Framework

### Overall Migration Phases

**Phase 0: Preparation (Week 1)**
- Add Redis to docker-compose.yml
- Implement redis_client.py module
- Create data access layer abstractions
- Write migration scripts (JSON → Redis)
- Set up local Redis for development

**Phase 1: Low-Risk Reads (Week 2)**
- Migrate read-only queries to Redis
- Keep writes to JSON (dual-write)
- Verify Redis data correctness
- No functional changes yet

**Phase 2: Write Migration (Week 3)**
- Migrate writes to Redis
- Keep reading from both (validation)
- Log discrepancies
- Fix any data inconsistencies

**Phase 3: Full Cutover (Week 4)**
- Remove JSON file I/O
- Redis as single source of truth
- Monitor for errors
- Keep JSON files as backup (read-only)

**Phase 4: Cleanup (Week 5)**
- Remove JSON fallback code
- Archive JSON files
- Performance optimization
- Documentation updates

### Migration Order (By Subsystem)

1. **Story History** (Lowest Risk)
   - Self-contained, time-based cleanup
   - No critical real-time dependencies
   - Good test case for Sorted Sets

2. **Pending News** (High Impact)
   - Largest file (2.3MB), most contention
   - Clear queue semantics
   - Immediate performance benefit

3. **Breaking News** (Medium Complexity)
   - Queue-based, natural Redis List
   - Lower volume than pending news

4. **RSS Feed Configuration** (Low Risk)
   - Configuration data (infrequent writes)
   - Good test for Hash structures

5. **Trivia Games** (Highest Complexity)
   - Real-time submissions
   - Critical atomicity requirements
   - Multiple concurrent writers
   - Requires Lua scripts

6. **Daily Games & Other Config** (Lowest Complexity)
   - Simple configuration
   - Infrequent updates

### One-Time Migration Script Strategy

```python
# bot/app/redis/migrations/json_to_redis.py
import json
import asyncio
from pathlib import Path
from bot.app.redis.client import get_redis_client, initialize_redis

async def migrate_app_state():
    """Migrate app_state.json to Redis."""
    json_path = Path(__file__).parent.parent / "app_state.json"

    if not json_path.exists():
        print("No app_state.json found, skipping")
        return

    with open(json_path) as f:
        data = json.load(f)

    redis_client = get_redis_client()
    redis = redis_client.redis

    for guild_id, guild_data in data.items():
        if guild_id == "global":
            # Migrate global config
            if "default_persona" in guild_data:
                await redis.set("global:config:default_persona", guild_data["default_persona"])
            continue

        # Migrate guild-specific data
        if "rss_feeds" in guild_data:
            await migrate_rss_feeds(redis, guild_id, guild_data["rss_feeds"])

        if "trivia_registrations" in guild_data:
            await migrate_trivia_registrations(redis, guild_id, guild_data["trivia_registrations"])

        # ... etc

async def migrate_pending_news():
    """Migrate pending_news.json to Redis."""
    # Similar structure

async def verify_migration():
    """Verify all data migrated correctly."""
    # Compare JSON files to Redis data
    # Log any discrepancies

if __name__ == "__main__":
    asyncio.run(migrate_all())
```

### Rollback Plan

1. **Keep JSON files** as backup during migration
2. **Feature flag**: `USE_REDIS=true/false` environment variable
3. **Dual-read mode**: Read from both, compare, log differences
4. **Quick rollback**: Set `USE_REDIS=false`, restart containers
5. **Data snapshot**: Redis SAVE/BGSAVE before major changes

---

## 6. Consistency Patterns

### Error Handling Conventions

```python
# Standard error handling pattern
from bot.app.redis.client import get_redis_client
from bot.app.redis.exceptions import RedisOperationError
from bot.app.utils.logger import get_logger

logger = get_logger()

async def get_trivia_game(guild_id: str, game_id: str) -> Optional[Dict[str, Any]]:
    """Get trivia game data with error handling."""
    try:
        redis_client = get_redis_client()
        redis = redis_client.redis

        key = f"trivia:{guild_id}:game:{game_id}"
        data = await redis.get(key)

        if data is None:
            logger.warning(f"Trivia game not found: {game_id}")
            return None

        return json.loads(data)

    except json.JSONDecodeError as e:
        logger.error(f"Failed to decode trivia game {game_id}: {e}")
        # Consider alerting or fallback
        return None

    except RedisOperationError as e:
        logger.error(f"Redis error fetching trivia game {game_id}: {e}")
        raise  # Propagate Redis errors

    except Exception as e:
        logger.error(f"Unexpected error fetching trivia game {game_id}: {e}")
        raise
```

### Logging Standards

**Log Format**:
```python
# Include key information in every log
logger.info(f"[Redis:GET] trivia:{guild_id}:game:{game_id}")
logger.info(f"[Redis:HSET] rss:{guild_id}:feeds {feed_name} -> {len(config)} bytes")
logger.error(f"[Redis:ERROR] Failed to acquire lock: lock:trivia:game:{game_id}")
```

**What to Log**:
- All Redis operations (INFO level in dev, DEBUG in prod)
- Lock acquisitions/releases (INFO)
- Migration operations (INFO)
- Data inconsistencies (WARNING)
- Redis errors (ERROR)
- Lua script executions (DEBUG)

### Type Conversion Standards

```python
# bot/app/redis/serialization.py
import json
from typing import Any, Optional
from datetime import datetime
from zoneinfo import ZoneInfo

def serialize_to_redis(value: Any) -> str:
    """Convert Python object to Redis string."""
    if isinstance(value, (str, int, float, bool)):
        return json.dumps(value)
    elif isinstance(value, datetime):
        return value.isoformat()
    elif isinstance(value, dict) or isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    else:
        raise TypeError(f"Cannot serialize type {type(value)} to Redis")

def deserialize_from_redis(value: Optional[str], expected_type: type = dict) -> Any:
    """Convert Redis string to Python object."""
    if value is None:
        return None

    try:
        data = json.loads(value)

        # Type validation
        if expected_type and not isinstance(data, expected_type):
            logger.warning(f"Expected {expected_type}, got {type(data)}")

        return data
    except json.JSONDecodeError as e:
        logger.error(f"Failed to deserialize Redis value: {e}")
        return None

# Guild ID conversion
def guild_id_to_str(guild_id: Optional[int]) -> str:
    """Convert Discord guild ID to string for Redis keys."""
    if guild_id is None:
        return "global"
    return str(guild_id)

def channel_id_to_str(channel_id: int) -> str:
    """Convert Discord channel ID to string."""
    return str(channel_id)
```

### Null/Missing Key Handling

**Standard Pattern**:
```python
# Option 1: Return None (Pythonic)
result = await redis.get(key)
if result is None:
    return None  # Caller handles missing data

# Option 2: Return default value
result = await redis.get(key)
return json.loads(result) if result else {}

# Option 3: Raise exception for required data
result = await redis.get(key)
if result is None:
    raise KeyError(f"Required key not found: {key}")

# Hash fields - use HGET with default
value = await redis.hget(key, field) or "default_value"
```

**Guidelines**:
- Configuration: Return sensible defaults
- Required data: Raise exception
- Optional data: Return None
- Lists/Sets: Return empty collection

---

## 7. Development & Deployment

### Docker Compose Setup

```yaml
# docker-compose.yml
services:
  redis:
    image: redis:7-alpine
    restart: unless-stopped
    command: redis-server --appendonly yes --appendfsync everysec
    ports:
      - "6379:6379"  # Expose for local development
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 3s
      retries: 3

  cunningbot:
    build: .
    restart: unless-stopped
    environment:
      - DISCORD_TOKEN=${DISCORD_TOKEN}
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - REDIS_HOST=redis
      - REDIS_PORT=6379
      - REDIS_DB=0
    depends_on:
      redis:
        condition: service_healthy
    volumes:
      - ./bot/app:/app/bot/app
      - ./logs:/app/logs

  # All background task containers
  dailygame:
    build: .
    restart: unless-stopped
    environment:
      - DISCORD_TOKEN=${DISCORD_TOKEN}
      - REDIS_HOST=redis
      - REDIS_PORT=6379
    depends_on:
      redis:
        condition: service_healthy
    volumes:
      - ./bot/app:/app/bot/app
    command: bash -c "while true; do python -m bot.app.tasks.daily_game_poster; sleep 600; done"

  # ... (rssfeed, rsssummary, breaking-news-validator, trivia-poster, trivia-closer)
  # All with same Redis environment variables and dependencies

volumes:
  redis_data:
```

### Local Development Approach

**Option 1: Redis in Docker, Bot on Host**
```bash
# Start only Redis
docker-compose up -d redis

# Run bot on host (connect to localhost:6379)
export REDIS_HOST=localhost
python -m bot.main
```

**Option 2: Full Docker Environment**
```bash
# Start everything
docker-compose up -d

# View logs
docker-compose logs -f cunningbot
docker-compose logs -f redis
```

**Option 3: Redis CLI for Debugging**
```bash
# Connect to Redis
docker exec -it cunningbot_redis_1 redis-cli

# Inspect keys
KEYS trivia:*
GET trivia:844003671334977607:game:abc123
HGETALL rss:844003671334977607:feeds

# Monitor operations in real-time
MONITOR
```

### Production Considerations

#### Persistence Configuration
```
# redis.conf
appendonly yes
appendfsync everysec       # Balance between safety and performance
save 900 1                 # Snapshot if 1 key changed in 15 min
save 300 10                # Snapshot if 10 keys changed in 5 min
save 60 10000              # Snapshot if 10k keys changed in 1 min
```

#### Backup Strategy
```bash
# Automated backup script (daily)
#!/bin/bash
BACKUP_DIR=/backups/redis
DATE=$(date +%Y%m%d_%H%M%S)

# Trigger Redis BGSAVE
docker exec cunningbot_redis_1 redis-cli BGSAVE

# Wait for BGSAVE to complete
sleep 10

# Copy RDB file
docker cp cunningbot_redis_1:/data/dump.rdb $BACKUP_DIR/dump_$DATE.rdb

# Keep last 7 days
find $BACKUP_DIR -name "dump_*.rdb" -mtime +7 -delete
```

#### Memory Management
```
# redis.conf
maxmemory 2gb
maxmemory-policy allkeys-lru  # Evict least recently used keys if memory full
```

#### Monitoring
```python
# bot/app/redis/health.py
async def check_redis_health() -> Dict[str, Any]:
    """Health check for Redis connection."""
    redis_client = get_redis_client()
    redis = redis_client.redis

    try:
        # Test connection
        await redis.ping()

        # Get stats
        info = await redis.info()

        return {
            "status": "healthy",
            "connected_clients": info.get("connected_clients"),
            "used_memory_human": info.get("used_memory_human"),
            "total_keys": await redis.dbsize(),
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e)
        }
```

---

## Summary & Next Steps

This architecture plan provides:

1. **Consistent key naming** across all subsystems
2. **Clear data structure guidelines** (when to use each Redis type)
3. **Atomic operation patterns** for concurrency safety
4. **Standardized client management** with connection pooling
5. **Phased migration strategy** with rollback capability
6. **Error handling and logging conventions**
7. **Production-ready deployment configuration**

### Implementation Checklist

- [ ] Add Redis to docker-compose.yml
- [ ] Implement `bot/app/redis/client.py`
- [ ] Create Lua scripts directory and initial scripts
- [ ] Set up environment variables
- [ ] Write migration scripts for each data type
- [ ] Create test suite (unit + integration)
- [ ] Document data access patterns for each subsystem
- [ ] Implement monitoring and health checks
- [ ] Set up backup automation
- [ ] Plan rollback procedures
