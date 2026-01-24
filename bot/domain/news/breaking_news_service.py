"""
breaking_news_service.py
Business logic for breaking news detection and validation.
"""

from typing import List, Dict, Any, Optional
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from bot.api.openai.chat_completions_client import ChatCompletionsClient
from bot.app.utils.logger import get_logger
from bot.domain.news.news_summary_service import check_story_similarity

logger = get_logger()

# Breaking news configuration
MAX_ARTICLE_AGE_HOURS = 2.0
MAX_CONSECUTIVE_LLM_FAILURES = 3


def matches_breaking_news_topics(entry: Dict[str, Any], topics: List[str]) -> Optional[str]:
    """
    Check if an RSS entry matches any breaking news topics.

    Args:
        entry: RSS feed entry with 'title' and 'description' fields
        topics: List of topic keywords (case-insensitive)

    Returns:
        Matched topic string (lowercase) or None if no match
    """
    if not topics:
        return None

    # Get title and description
    title = entry.get('title', '').lower()
    description = entry.get('description', '').lower()

    # Check each topic for substring match
    for topic in topics:
        topic_lower = topic.lower().strip()
        if not topic_lower:
            continue

        if topic_lower in title or topic_lower in description:
            logger.info(f"Breaking news match: '{topic}' found in article")
            return topic

    return None


async def validate_breaking_news_relevance(
    article: Dict[str, Any],
    matched_topic: str
) -> bool:
    """
    Use LLM to validate if an article is genuinely newsworthy breaking news
    or just a coincidental keyword match.

    Args:
        article: Article data with 'title' and 'description'
        matched_topic: The topic keyword that triggered the match

    Returns:
        True if newsworthy, False if coincidental or on LLM failure
    """
    title = article.get('title', 'Untitled')
    description = article.get('description', '')

    prompt = f"""This article matched the keyword "{matched_topic}".

Article Title: {title}
Article Description: {description[:500]}

Is this article about CURRENT, BREAKING NEWS related to "{matched_topic}", or is it:
- Historical/retrospective coverage
- Metaphorical usage of the keyword
- Commentary/opinion about past events
- Coincidental mention

Respond with ONLY ONE WORD:
- "newsworthy" if this is current breaking news about {matched_topic}
- "coincidence" if this is NOT current breaking news"""

    try:
        llm = ChatCompletionsClient.factory("gpt-4o-mini")
        response = await llm.chat([
            {
                "role": "system",
                "content": "You are a news editor determining if articles are current breaking news. Respond with only 'newsworthy' or 'coincidence'."
            },
            {
                "role": "user",
                "content": prompt
            }
        ])

        response_lower = response.strip().lower()

        if "newsworthy" in response_lower:
            logger.info(f"LLM validated: '{title}' is newsworthy for topic '{matched_topic}'")
            return True
        else:
            logger.info(f"LLM rejected: '{title}' is coincidental match for '{matched_topic}'")
            return False

    except Exception as e:
        logger.error(f"LLM validation failed: {e}")
        # Conservative approach: reject on failure
        return False


def is_article_fresh(article: Dict[str, Any], max_age_hours: float = MAX_ARTICLE_AGE_HOURS) -> bool:
    """
    Check if an article is fresh (published within max_age_hours).

    Args:
        article: Article data with 'published' or 'collected_at' timestamp
        max_age_hours: Maximum age in hours (default: 2.0)

    Returns:
        True if article is fresh, False if too old or no valid timestamp
    """
    # Try published field first
    timestamp_str = article.get('published')

    # Fallback to collected_at
    if not timestamp_str:
        timestamp_str = article.get('collected_at')

    # No valid timestamp
    if not timestamp_str:
        logger.warning(f"Article missing timestamp: {article.get('title', 'Unknown')}")
        return False

    try:
        # Parse ISO format timestamp
        article_time = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))

        # Ensure timezone-aware
        if article_time.tzinfo is None:
            article_time = article_time.replace(tzinfo=ZoneInfo("UTC"))

        # Get current time (UTC)
        now = datetime.now(ZoneInfo("UTC"))

        # Calculate age
        age = now - article_time
        age_hours = age.total_seconds() / 3600

        is_fresh = age_hours <= max_age_hours

        if is_fresh:
            logger.info(f"Article is fresh: {age_hours:.1f}h old")
        else:
            logger.info(f"Article rejected: {age_hours:.1f}h old (max {max_age_hours}h)")

        return is_fresh

    except Exception as e:
        logger.error(f"Error parsing timestamp '{timestamp_str}': {e}")
        return False


async def check_breaking_news_duplicate(
    article: Dict[str, Any],
    guild_id: str,
    channel_id: int,
    store
) -> bool:
    """
    Check if an article is a duplicate using URL and semantic similarity.

    Args:
        article: Article data with 'link' and 'title'
        guild_id: Guild ID string
        channel_id: Breaking news channel ID
        store: RSSRedisStore instance for accessing story history

    Returns:
        True if duplicate detected, False if unique
    """
    article_url = article.get('link', '')
    article_title = article.get('title', '')

    if not article_url or not article_title:
        logger.warning("Article missing URL or title for duplicate check")
        return False

    # Get recent stories from breaking news channel (24h window) from Redis
    try:
        recent_stories = await store.get_stories_within_window(guild_id, channel_id, window_hours=24)
    except Exception as e:
        logger.error(f"Error loading story history: {e}")
        return False

    if not recent_stories:
        return False

    # Check 1: URL-based duplicate detection
    for story in recent_stories:
        story_articles = story.get('articles', [])
        for story_article in story_articles:
            if story_article.get('link') == article_url:
                logger.info(f"Duplicate URL detected: {article_url}")
                return True

    # Check 2: Semantic similarity
    historical_titles = [story.get('title', '') for story in recent_stories]

    try:
        is_similar, similar_story = await check_story_similarity(
            new_title=article_title,
            new_articles=[article],
            historical_titles=historical_titles,
            story_history=recent_stories
        )

        if is_similar:
            logger.info(f"Semantic duplicate detected: '{article_title}' similar to '{similar_story.get('title', 'Unknown')}'")
            return True

    except Exception as e:
        logger.error(f"Semantic similarity check failed: {e}")
        # Continue processing if similarity check fails

    return False
