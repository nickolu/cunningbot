"""backfill_agent_tools.py

Adds newly-shipped agent tools to channels that were registered before the tool
existed.

`DEFAULT_AGENT_CONFIG["tools"]` in `bot/app/redis/agent_store.py` is only read
when a channel is first registered. Every already-registered channel keeps the
tool list stored in its own `agent:{guild_id}:{channel_id}` record, so shipping
a new tool leaves existing channels unable to use it — and `/agent configure`
has no `tools` option, so the only in-Discord remedy is unregister plus
re-register, which discards that channel's model, persona, and other settings.

Run this after adding a tool. It only ever adds the keys you name, never
removes or reorders anything else, and is safe to run repeatedly.

Usage (inside Docker container):
    python -m bot.app.redis.migrations.backfill_agent_tools [--dry-run] [TOOL ...]

Examples:
    python -m bot.app.redis.migrations.backfill_agent_tools --dry-run
    python -m bot.app.redis.migrations.backfill_agent_tools publish_page
"""
import argparse
import asyncio
import json
import logging
from typing import List

from bot.app.redis.client import close_redis, get_redis_client, initialize_redis

logger = logging.getLogger("AgentToolsBackfill")
logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")

# Tools added since the earliest registrations. Extend when shipping a new one.
DEFAULT_TOOLS_TO_ADD = ["publish_page"]

KEY_PATTERN = "agent:*"


async def backfill(tools: List[str], dry_run: bool = False) -> None:
    await initialize_redis()
    redis = get_redis_client().redis

    scanned = updated = skipped = failed = 0
    cursor = 0

    try:
        while True:
            cursor, keys = await redis.scan(cursor, match=KEY_PATTERN, count=100)
            for key in keys:
                scanned += 1
                raw = await redis.get(key)
                if not raw:
                    continue

                try:
                    config = json.loads(raw)
                except json.JSONDecodeError as e:
                    logger.error(f"{key}: could not parse config ({e}), skipping")
                    failed += 1
                    continue

                current = config.get("tools")
                if not isinstance(current, list):
                    logger.error(f"{key}: 'tools' is not a list, skipping")
                    failed += 1
                    continue

                missing = [t for t in tools if t not in current]
                if not missing:
                    skipped += 1
                    continue

                config["tools"] = current + missing
                if dry_run:
                    logger.info(f"{key}: would add {missing}")
                else:
                    await redis.set(key, json.dumps(config))
                    logger.info(f"{key}: added {missing}")
                updated += 1

            if cursor == 0:
                break
    finally:
        await close_redis()

    verb = "would update" if dry_run else "updated"
    logger.info(
        f"Done. scanned={scanned} {verb}={updated} already_current={skipped} failed={failed}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Add agent tools to channels registered before the tool shipped."
    )
    parser.add_argument(
        "tools", nargs="*", default=None,
        help=f"Tool keys to add (default: {' '.join(DEFAULT_TOOLS_TO_ADD)})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would change without writing",
    )
    args = parser.parse_args()

    asyncio.run(backfill(args.tools or DEFAULT_TOOLS_TO_ADD, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
