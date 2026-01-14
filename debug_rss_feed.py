#!/usr/bin/env python3
"""Debug script for RSS feed poster.

Usage:
    python3 debug_rss_feed.py
"""
import sys
import json
from pathlib import Path

# Add the bot directory to path
sys.path.insert(0, str(Path(__file__).parent))

import feedparser
from bot.app.app_state import get_all_guild_states, STATE_FILE_PATH


def main():
    print("=" * 80)
    print("RSS Feed Poster Debug Tool")
    print("=" * 80)

    # Check state file location
    print(f"\n1. State file location: {STATE_FILE_PATH}")
    print(f"   Exists: {Path(STATE_FILE_PATH).exists()}")

    # Load and display state
    print("\n2. Current state:")
    all_states = get_all_guild_states()

    if not all_states:
        print("   ⚠️  No state data found!")
        return

    # Look for RSS feeds
    found_feeds = False
    for guild_id, guild_state in all_states.items():
        if guild_id == "global":
            continue

        if "rss_feeds" in guild_state and guild_state["rss_feeds"]:
            found_feeds = True
            print(f"\n   Guild: {guild_id}")
            print(f"   RSS Feeds: {len(guild_state['rss_feeds'])}")

            for feed_name, feed_info in guild_state["rss_feeds"].items():
                print(f"\n   Feed: '{feed_name}'")
                print(f"     URL: {feed_info.get('url')}")
                print(f"     Channel ID: {feed_info.get('channel_id')}")
                print(f"     Enabled: {feed_info.get('enabled')}")
                print(f"     Last check: {feed_info.get('last_check', 'Never')}")
                print(f"     Seen items: {len(feed_info.get('seen_items', []))} items")

                # Test fetching the feed
                feed_url = feed_info.get('url')
                if feed_url:
                    print(f"\n     Testing feed fetch...")
                    try:
                        feed = feedparser.parse(feed_url)
                        print(f"     ✓ Feed fetched successfully")
                        print(f"     ✓ Found {len(feed.entries)} entries in feed")

                        # Check for new items
                        seen_items = feed_info.get('seen_items', [])
                        new_count = 0
                        for entry in feed.entries:
                            # Use same ID logic as poster
                            if hasattr(entry, 'id') and entry.id:
                                item_id = str(entry.id)
                            elif hasattr(entry, 'guid') and entry.guid:
                                item_id = str(entry.guid)
                            else:
                                import hashlib
                                content = f"{entry.get('title', '')}{entry.get('link', '')}{entry.get('published', '')}"
                                item_id = hashlib.sha256(content.encode()).hexdigest()[:16]

                            if item_id not in seen_items:
                                new_count += 1
                                if new_count <= 3:
                                    print(f"       • New: {entry.get('title', 'No title')[:70]}")

                        if new_count == 0:
                            print(f"     ℹ️  No new items (all {len(feed.entries)} items already seen)")
                        else:
                            print(f"     ✓ Found {new_count} NEW items that should be posted")

                        # Show first few seen IDs
                        if seen_items:
                            print(f"\n     First 3 seen item IDs:")
                            for i, item_id in enumerate(seen_items[:3]):
                                print(f"       {i+1}. {item_id}")

                    except Exception as e:
                        print(f"     ✗ Error fetching feed: {e}")

    if not found_feeds:
        print("\n   ⚠️  No RSS feeds configured in any guild!")
        print("\n   Troubleshooting:")
        print("   1. Make sure you registered the feed using /news add")
        print("   2. If using Docker, ensure volume mounts are correct")
        print("   3. Check that the bot has permission to save state")


if __name__ == "__main__":
    main()
