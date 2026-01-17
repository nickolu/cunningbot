"""
Feed Diversity Strategy - Ensures fair representation across RSS feeds.

This module implements algorithms to balance article selection across multiple feeds,
preventing high-volume feeds from dominating summaries while preserving article quality.
"""

from typing import Dict, List, Any, Optional
import logging

logger = logging.getLogger(__name__)

# Default configuration constants
DEFAULT_STRATEGY = "disabled"              # Opt-in, backwards compatible
DEFAULT_MAX_ARTICLES_PER_FEED = None       # No cap
DEFAULT_MIN_ARTICLES_PER_FEED = 0          # No guarantee

# Recommended user settings for typical channel (5 feeds)
RECOMMENDED_MAX_PER_FEED = 15              # Prevents >30% domination
RECOMMENDED_MIN_PER_FEED = 2               # 2 per feed = 10 slots reserved


def get_channel_feed_diversity(guild_id: int, channel_id: int) -> Dict[str, Any]:
    """
    Get feed diversity configuration for a specific channel.
    Returns defaults if no custom configuration exists.

    Args:
        guild_id: Discord guild ID
        channel_id: Discord channel ID

    Returns:
        Dictionary with keys: strategy, max_articles_per_feed, min_articles_per_feed
    """
    from bot.app.app_state import get_state_value

    guild_id_str = str(guild_id)
    all_diversity = get_state_value("channel_feed_diversity", guild_id_str) or {}
    channel_config = all_diversity.get(str(channel_id))

    if not channel_config:
        return {
            "strategy": DEFAULT_STRATEGY,
            "max_articles_per_feed": DEFAULT_MAX_ARTICLES_PER_FEED,
            "min_articles_per_feed": DEFAULT_MIN_ARTICLES_PER_FEED
        }

    return channel_config


def apply_feed_diversity_strategy(
    articles_by_feed: Dict[str, List[Dict[str, Any]]],
    initial_limit: int,
    strategy: str = "disabled",
    max_per_feed: Optional[int] = None,
    min_per_feed: int = 0
) -> List[Dict[str, Any]]:
    """
    Apply feed diversity strategy to article selection.

    Args:
        articles_by_feed: Dictionary mapping feed names to lists of articles.
                         Articles in each list should be pre-sorted by recency.
        initial_limit: Total number of articles to select
        strategy: One of "disabled", "balanced", "proportional"
        max_per_feed: Maximum articles allowed from any single feed (None = no limit)
        min_per_feed: Minimum articles to guarantee per feed (0 = no guarantee)

    Returns:
        List of selected articles, sorted by recency

    Strategies:
        - "disabled": Current behavior (no diversity enforcement)
        - "balanced": Apply max cap and min guarantee (RECOMMENDED)
        - "proportional": Allocate slots proportionally by feed size
    """
    if strategy == "disabled" or not articles_by_feed:
        # Flatten all articles and sort by recency
        all_articles = []
        for articles in articles_by_feed.values():
            all_articles.extend(articles)
        all_articles.sort(key=lambda x: x.get('collected_at', ''), reverse=True)
        return all_articles[:initial_limit]

    if strategy == "balanced":
        return _apply_balanced_strategy(
            articles_by_feed, initial_limit, max_per_feed, min_per_feed
        )
    elif strategy == "proportional":
        return _apply_proportional_strategy(
            articles_by_feed, initial_limit, max_per_feed, min_per_feed
        )
    else:
        logger.warning(f"Unknown diversity strategy '{strategy}', using 'disabled'")
        return apply_feed_diversity_strategy(
            articles_by_feed, initial_limit, "disabled", max_per_feed, min_per_feed
        )


def _apply_balanced_strategy(
    articles_by_feed: Dict[str, List[Dict[str, Any]]],
    initial_limit: int,
    max_per_feed: Optional[int],
    min_per_feed: int
) -> List[Dict[str, Any]]:
    """
    Balanced strategy: Guarantee minimum per feed, then fill remaining slots with max cap.

    Algorithm:
    1. Phase 1: Reserve min_per_feed slots for each feed
    2. Phase 2: Fill remaining slots by recency, respecting max_per_feed cap
    3. Phase 3: Backfill unused slots if feeds are inactive (relax max cap)
    """
    # Validate parameters
    if max_per_feed is not None and min_per_feed > max_per_feed:
        logger.warning(
            f"min_per_feed ({min_per_feed}) > max_per_feed ({max_per_feed}), "
            f"using max_per_feed as effective minimum"
        )
        min_per_feed = max_per_feed

    selected = []
    articles_used_per_feed = {}

    # Phase 1: Guarantee minimum per feed
    logger.debug(f"Phase 1: Guaranteeing {min_per_feed} articles per feed")
    for feed_name, articles in articles_by_feed.items():
        if not articles:
            articles_used_per_feed[feed_name] = 0
            continue

        take = min(min_per_feed, len(articles))
        selected.extend(articles[:take])
        articles_used_per_feed[feed_name] = take

    logger.debug(f"Phase 1 complete: {len(selected)} articles selected")

    # Phase 2: Fill remaining slots respecting max cap
    remaining_slots = initial_limit - len(selected)
    if remaining_slots > 0:
        logger.debug(f"Phase 2: Filling {remaining_slots} remaining slots with max_per_feed={max_per_feed}")
        pool = []

        for feed_name, articles in articles_by_feed.items():
            used = articles_used_per_feed.get(feed_name, 0)
            available = articles[used:]  # Skip already-selected

            if max_per_feed is not None:
                # Cap at (max_per_feed - already_used)
                cap = max(0, max_per_feed - used)
                pool.extend(available[:cap])
            else:
                pool.extend(available)

        # Sort pool by recency and take top N
        pool.sort(key=lambda x: x.get('collected_at', ''), reverse=True)
        selected.extend(pool[:remaining_slots])

        logger.debug(f"Phase 2 complete: {len(selected)} articles selected")

    # Phase 3: Backfill if slots remain (handles inactive feeds)
    # Use round-robin to prevent one feed from dominating backfill
    remaining_slots = initial_limit - len(selected)
    if remaining_slots > 0 and max_per_feed is not None:
        logger.debug(
            f"Phase 3: Backfilling {remaining_slots} unused slots "
            f"(using round-robin across feeds)"
        )

        # Build list of feeds that have articles beyond max_per_feed
        overflow_by_feed = {}
        for feed_name, articles in articles_by_feed.items():
            used = articles_used_per_feed.get(feed_name, 0)
            if len(articles) > max_per_feed:
                # Get articles beyond the max cap
                overflow_by_feed[feed_name] = articles[max_per_feed:]

        # Round-robin distribution of remaining slots
        backfill = []
        round_robin_index = 0
        feed_names_with_overflow = list(overflow_by_feed.keys())

        while len(backfill) < remaining_slots and feed_names_with_overflow:
            # Get next feed in round-robin
            feed_name = feed_names_with_overflow[round_robin_index]
            overflow_articles = overflow_by_feed[feed_name]

            if overflow_articles:
                # Take the most recent article from this feed
                backfill.append(overflow_articles.pop(0))
                round_robin_index = (round_robin_index + 1) % len(feed_names_with_overflow)
            else:
                # This feed is exhausted, remove it from rotation
                feed_names_with_overflow.pop(round_robin_index)
                if feed_names_with_overflow:
                    round_robin_index = round_robin_index % len(feed_names_with_overflow)

        selected.extend(backfill)
        logger.debug(f"Phase 3 complete: {len(selected)} articles selected (backfilled {len(backfill)})")

    # Final sort by recency
    selected.sort(key=lambda x: x.get('collected_at', ''), reverse=True)

    return selected[:initial_limit]


def _apply_proportional_strategy(
    articles_by_feed: Dict[str, List[Dict[str, Any]]],
    initial_limit: int,
    max_per_feed: Optional[int],
    min_per_feed: int
) -> List[Dict[str, Any]]:
    """
    Proportional strategy: Allocate slots proportionally based on feed sizes.

    Algorithm:
    1. Calculate total articles across all feeds
    2. Allocate slots to each feed proportionally to their size
    3. Respect min_per_feed guarantees and max_per_feed caps
    4. Distribute remaining slots from capped/empty feeds
    """
    # Count total articles
    total_articles = sum(len(articles) for articles in articles_by_feed.values())
    if total_articles == 0:
        return []

    # Calculate proportional allocation
    allocations = {}
    for feed_name, articles in articles_by_feed.items():
        feed_size = len(articles)
        if feed_size == 0:
            allocations[feed_name] = 0
        else:
            # Proportional share, rounded
            proportion = feed_size / total_articles
            allocated = max(min_per_feed, int(proportion * initial_limit))

            # Apply max cap
            if max_per_feed is not None:
                allocated = min(allocated, max_per_feed)

            # Can't allocate more than available
            allocated = min(allocated, feed_size)

            allocations[feed_name] = allocated

    # Select articles per allocation
    selected = []
    total_allocated = 0

    for feed_name, articles in articles_by_feed.items():
        count = allocations.get(feed_name, 0)
        selected.extend(articles[:count])
        total_allocated += count

    # Distribute remaining slots if we're under limit
    remaining = initial_limit - total_allocated
    if remaining > 0:
        logger.debug(f"Distributing {remaining} remaining slots")

        # Collect articles beyond allocations
        overflow_pool = []
        for feed_name, articles in articles_by_feed.items():
            allocated = allocations.get(feed_name, 0)
            available = articles[allocated:]

            # Only add if we haven't hit max_per_feed
            if max_per_feed is None or allocated < max_per_feed:
                cap = len(available) if max_per_feed is None else max_per_feed - allocated
                overflow_pool.extend(available[:cap])

        # Sort by recency and take remaining
        overflow_pool.sort(key=lambda x: x.get('collected_at', ''), reverse=True)
        selected.extend(overflow_pool[:remaining])

    # Final sort by recency
    selected.sort(key=lambda x: x.get('collected_at', ''), reverse=True)

    return selected[:initial_limit]
