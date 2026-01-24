# RSS System Redis Migration Progress

## Overview

The RSS feed system currently uses JSON files (`app_state.json` and `pending_news.json`) to store feed configurations, seen items, and pending articles. With multiple containers (`rssfeed`, `rsssummary`) reading and writing simultaneously, race conditions can occur.

## Race Conditions Identified

### 1. Seen Items Corruption
**Problem**: `rss_feed_poster.py` updates `seen_items` list for each feed
- **Container A**: Reads feed, finds 5 new items, adds to seen_items
- **Container B**: Reads feed simultaneously, finds same 5 items
- **Container A**: Saves seen_items [1,2,3,4,5]
- **Container B**: Saves seen_items [1,2,3,4,5] → **OVERWRITES A's changes**

**Result**: Duplicate articles collected, duplicate posts

### 2. Pending Articles File Corruption
**Problem**: Multiple containers writing to `pending_news.json`
- **Container A**: Reads pending_news.json
- **Container B**: Reads pending_news.json
- **Container A**: Adds articles from Feed X, writes file
- **Container B**: Adds articles from Feed Y, writes file → **OVERWRITES A's articles**

**Result**: Lost articles, incomplete summaries

### 3. Feed Configuration Updates
**Problem**: User modifies feed settings while container is processing
- **User**: Updates feed URL via command
- **Container**: Reading old feed URL, updates seen_items
- **Container**: Saves state → **OVERWRITES user's URL change**

**Result**: Lost configuration changes

### 4. Summary Timing Coordination
**Problem**: Multiple `rsssummary` containers checking summary schedule
- **Container A**: Checks time, sees 8:00 AM, starts summary
- **Container B**: Checks time, sees 8:00 AM, starts summary
- **Both**: Generate and post duplicate summaries

**Result**: Duplicate summary posts, wasted OpenAI API calls

## Solution: Redis Migration

### Redis Key Structure

```
rss:{guild_id}:feeds                           → Hash (all feed configurations)
rss:{guild_id}:feed:{feed_name}:seen           → Set (seen item IDs)
rss:{guild_id}:pending:{channel_id}:{feed}     → List (pending articles)
rss:{guild_id}:summary:{channel_id}:last       → Hash (last summary times by edition)
lock:rss:{guild_id}:feed:{feed_name}           → String (distributed lock for feed processing)
lock:rss:{guild_id}:summary:{channel_id}       → String (distributed lock for summary generation)
```

### Why This Structure?

- **Hash for feeds**: Easy to get/update individual feed configs
- **Set for seen items**: O(1) membership checks, automatic deduplication
- **List for pending**: Natural queue structure, supports batch operations
- **Locks**: Prevent duplicate processing

## Implementation Tasks

### ✅ Prerequisites (Already Done)
1. Redis infrastructure deployed
2. Redis client and Lua script support implemented
3. Distributed lock pattern available

## ⏳ Pending Tasks

### 1. Create RSS Data Access Layer
**File**: `bot/app/redis/rss_store.py`

Methods needed:
- **Feeds**: `get_feeds()`, `get_feed()`, `save_feed()`, `delete_feed()`
- **Seen Items**: `is_seen()`, `mark_seen()`, `get_seen_count()`
- **Pending Articles**: `add_pending()`, `get_pending()`, `clear_pending()`
- **Summary Tracking**: `should_post_summary()`, `record_summary()`

### 2. Update RSS Feed Poster
**File**: `bot/app/tasks/rss_feed_poster.py`

Changes:
- Add distributed lock per feed (`lock:rss:{guild_id}:feed:{feed_name}`)
- Use `RSSRedisStore` for feed configs and seen items
- Use Redis Set for O(1) seen item checks
- Atomic pending article additions
- Feature flag pattern for rollback

### 3. Update RSS Summary Poster
**File**: `bot/app/tasks/rss_summary_poster.py`

Changes:
- Add distributed lock per channel (`lock:rss:{guild_id}:summary:{channel_id}`)
- Use `RSSRedisStore` for summary timing checks
- Prevent duplicate summaries across containers
- Feature flag pattern for rollback

### 4. RSS Management Commands
**Files**: Search for RSS slash commands

Changes:
- Update `/rss` commands to use Redis
- Register, enable, disable, delete operations
- List feeds from Redis

### 5. Migration Script
**File**: `bot/app/redis/migrations/migrate_rss.py`

Will migrate:
- RSS feed configurations from app_state.json
- Seen items lists → Redis Sets
- Pending articles from pending_news.json → Redis Lists
- Summary tracking data

### 6. Testing
- Concurrent feed processing (multiple rssfeed containers)
- Duplicate summary prevention (multiple rsssummary containers)
- Article collection during high load
- Feed configuration updates during processing

## Key Architectural Decisions

### Feed Processing Lock Strategy
```python
# Each feed gets its own lock to allow parallel processing
async with redis_lock(redis_client, f"rss:{guild_id}:feed:{feed_name}", timeout=300):
    # Fetch feed
    # Check for new items using Redis Set
    # Add to pending (atomic)
    # Update seen items (atomic)
```

### Seen Items as Redis Set
- Old: List with `item_id in seen_items` (O(n))
- New: Set with `SISMEMBER` (O(1))
- Supports atomic `SADD` for marking seen
- Auto-deduplication

### Pending Articles as Redis List
- Use `RPUSH` to add articles atomically
- Use `LRANGE` to get all pending
- Use `DEL` to clear after summary
- Can add TTL if needed

### Summary Coordination
```python
# Lock per channel to prevent duplicate summaries
async with redis_lock(redis_client, f"rss:{guild_id}:summary:{channel_id}", timeout=600):
    # Check if summary needed (time + last_summary check)
    # Generate summary
    # Post to Discord
    # Update last_summary
```

## Deployment Plan

1. **Deploy Code** (this work)
   - Feature flags set to `USE_REDIS=False` initially

2. **Run Migration Script**
   ```bash
   docker-compose exec cunningbot python -m bot.app.redis.migrations.migrate_rss
   ```

3. **Enable Redis** (set `USE_REDIS=True`)
   - Restart containers

4. **Monitor**
   - Check for duplicate articles
   - Verify no missed articles
   - Monitor Redis memory usage
   - Check distributed lock logs

5. **Cleanup** (after 1 week)
   - Remove JSON fallback code
   - Archive pending_news.json

## Benefits

- ✅ **No duplicate articles**: Atomic seen item checks prevent re-processing
- ✅ **No lost articles**: Atomic pending article additions
- ✅ **No duplicate summaries**: Distributed locks coordinate containers
- ✅ **Better performance**: O(1) seen checks vs O(n) list searches
- ✅ **Safer config updates**: Atomic feed configuration updates
- ✅ **Horizontal scaling**: Multiple containers can safely process different feeds

## Estimated Complexity

**Medium** - Simpler than trivia because:
- No complex game state transitions
- Straightforward data structures (feeds, seen items, pending articles)
- Fewer edge cases

**Challenges**:
- Two separate containers (rssfeed, rsssummary)
- Two separate JSON files to migrate
- Need to find RSS slash commands (may not exist yet)
