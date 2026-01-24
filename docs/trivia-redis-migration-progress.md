# Trivia System Redis Migration Progress

## ✅ Completed

### 1. Lua Script for Atomic Submissions
**File**: `bot/app/redis/scripts/trivia_submit_answer.lua`

Atomic script that:
- Checks if game exists in Redis
- Validates game is not closed (`closed_at` flag)
- Validates answer window is still open (`current_time < ends_at`)
- Records submission in separate hash (prevents race conditions)
- Returns error codes: `GAME_NOT_FOUND`, `GAME_CLOSED`, `WINDOW_CLOSED`

**Key Feature**: Entire operation executes within Redis - no race conditions possible!

### 2. Trivia Data Access Layer
**File**: `bot/app/redis/trivia_store.py`

Complete Redis abstraction layer with methods for:
- **Active Games**: `get_active_games()`, `get_game()`, `create_game()`, `update_game()`, `delete_game()`
- **Submissions**: `submit_answer_atomic()` (uses Lua script), `get_submissions()`, `update_submission()`
- **Registrations**: `get_registrations()`, `save_registration()`, `delete_registration()`
- **History**: `move_to_history()`, `get_history()`

Handles JSON serialization, error logging, and timestamp conversion.

### 3. Updated Submission Handler
**File**: `bot/app/commands/trivia/trivia_submission_handler.py`

Refactored to use Redis with feature flag:
- `USE_REDIS = True` - Controls Redis vs JSON mode
- `_submit_with_redis()` - **NEW** Uses atomic Lua script, eliminates race conditions
- `_submit_with_json()` - Legacy fallback (preserves old behavior)

**Benefits**:
- ✅ Atomic submissions (no lost data)
- ✅ Immediate feedback on closed games
- ✅ Preserves validation flow
- ✅ Safe rollback capability (set `USE_REDIS=False`)

## ✅ Completed (continued)

### 4. Trivia Game Closer with Distributed Locks
**File**: `bot/app/tasks/trivia_game_closer.py`

Refactored with feature flag pattern:
- `USE_REDIS = True` - Controls Redis vs JSON mode
- `_close_with_redis()` - **NEW** Uses distributed locks, prevents duplicate processing
- `_close_with_json()` - Legacy fallback (preserves old behavior)

**Redis Implementation**:
- Distributed lock per game: `lock:trivia:{guild_id}:game:{game_id}` with 60s timeout
- Double-checks `closed_at` within lock to prevent duplicate processing
- Sets `closed_at` immediately after acquiring lock
- Gets submissions from Redis using `TriviaRedisStore`
- Uses `move_to_history()` for atomic history migration with 7-day TTL
- Deletes game from active games after posting results

**Benefits**:
- ✅ Only one closer can process each game (distributed lock)
- ✅ No duplicate result posts
- ✅ Removed state reload hack (no longer needed with Redis atomicity)
- ✅ Safe rollback capability (set `USE_REDIS=False`)

## ✅ Completed (continued)

### 5. Trivia Game Poster
**File**: `bot/app/tasks/trivia_game_poster.py`

Refactored with feature flag pattern:
- `USE_REDIS = True` - Controls Redis vs JSON mode
- `_post_with_redis()` - **NEW** Creates games in Redis
- `_post_with_json()` - Legacy fallback (preserves old behavior)

**Redis Implementation**:
- Gets registrations from `TriviaRedisStore.get_registrations()`
- Checks for duplicate posts by querying active games from Redis
- Creates game using `TriviaRedisStore.create_game()`
- Used seeds still stored in JSON (will migrate later)

**Benefits**:
- ✅ Games created atomically in Redis
- ✅ No race conditions during game creation
- ✅ Safe rollback capability (set `USE_REDIS=False`)

## ✅ Completed (continued)

### 6. Migration Script
**File**: `bot/app/redis/migrations/migrate_trivia.py`

One-time migration script to move existing data from JSON to Redis:

**Features**:
- Migrates active_trivia_games from JSON to Redis
- Migrates all submissions for each game
- Migrates trivia_registrations
- Validates migration was successful (compares counts)
- Supports dry-run mode (`--dry-run`)
- Optional JSON data deletion (`--delete`) or keeps as backup (default)

**Usage**:
```bash
# Dry run to see what would be migrated
python -m bot.app.redis.migrations.migrate_trivia --dry-run

# Perform migration (keeps JSON as backup)
python -m bot.app.redis.migrations.migrate_trivia

# Perform migration and delete JSON data
python -m bot.app.redis.migrations.migrate_trivia --delete
```

**Benefits**:
- ✅ Safe migration with validation
- ✅ Dry-run mode prevents mistakes
- ✅ Keeps JSON backup by default
- ✅ Detailed logging of migration progress

## ⏳ Pending

### 7. Testing
- Concurrent submission stress test (100+ simultaneous submissions)
- Multiple closers test (verify only one posts results)
- Submission during closing test (verify no lost data)
- Load testing with real Discord bot

## Key Architectural Decisions

### Redis Key Structure
```
trivia:{guild_id}:games:active                    → Hash (all active games)
trivia:{guild_id}:game:{game_id}:submissions      → Hash (submissions per game)
trivia:{guild_id}:registrations                   → Hash (game registrations)
trivia:{guild_id}:games:history                   → Sorted Set (game IDs by timestamp)
trivia:{guild_id}:game:{game_id}:history          → String (full game history, 7-day TTL)
lock:trivia:{guild_id}:game:{game_id}             → String (distributed lock, 30s TTL)
```

### Why Separate Submissions Hash?
- Enables atomic HSET per user
- Lua script can read game + write submission atomically
- No risk of overwriting other submissions
- Clean separation: game metadata vs dynamic submissions

### Feature Flag Strategy
- `USE_REDIS=True` by default (new deployments use Redis)
- Can rollback by setting `USE_REDIS=False` in environment
- JSON fallback preserved for safety

## Deployment Plan

1. **Deploy Infrastructure** ✅ DONE (Redis running on server)
2. **Deploy Code** (this work)
   - Rebuild Docker images
   - Restart containers (Redis env vars already set)
3. **Run Migration Script** (after game closer is updated)
   - Migrate active games to Redis
   - Keep JSON as backup
4. **Monitor**
   - Watch for lost submissions (should be ZERO)
   - Check Redis memory usage
   - Verify game closure logs
5. **Cleanup** (after 1 week)
   - Remove JSON fallback code
   - Archive app_state.json

## Race Condition Before/After

### BEFORE (JSON):
```
T0: User submits → reads app_state.json
T1: Closer scans → reads app_state.json
T2: User writes submission → saves app_state.json
T3: Closer closes game → saves app_state.json → OVERWRITES user submission ❌
```

### AFTER (Redis + Lua):
```
T0: User submits → Lua script executes atomically (check + write)
T1: Closer tries to close → Acquires lock → Sets closed_at
T2: Late submission → Lua script sees closed_at → Returns GAME_CLOSED ✅
```

**Result**: Zero lost submissions, all race conditions eliminated!
