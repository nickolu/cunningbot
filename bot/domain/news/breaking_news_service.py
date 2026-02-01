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

# Few-shot examples for breaking news validation
FEW_SHOT_EXAMPLES = [
    # Example 1: NEWSWORTHY - San Diego wildfire emergency
    {
        "role": "user",
        "content": '''This article matched the keyword "fire".

Article Title: Massive wildfire erupts in Escondido, thousands evacuated
Article Description: A rapidly spreading wildfire has forced evacuation orders for 15,000 residents in northern San Diego County. Multiple neighborhoods are under mandatory evacuation as flames consume hillsides near residential areas...

Is this current breaking news about "fire" or a coincidental mention?'''
    },
    {
        "role": "assistant",
        "content": "newsworthy"
    },

    # Example 2: NEWSWORTHY - US-wide infrastructure attack
    {
        "role": "user",
        "content": '''This article matched the keyword "power".

Article Title: Major cyberattack causes power grid failures across 12 states
Article Description: Federal authorities are investigating coordinated attacks on power infrastructure affecting millions of Americans across the eastern United States. Emergency response teams are working to restore service...

Is this current breaking news about "power" or a coincidental mention?'''
    },
    {
        "role": "assistant",
        "content": "newsworthy"
    },

    # Example 3: NEWSWORTHY - Global pandemic threat
    {
        "role": "user",
        "content": '''This article matched the keyword "virus".

Article Title: WHO declares global health emergency as new virus spreads to 40 countries
Article Description: World Health Organization has activated emergency protocols following rapid international spread of a novel respiratory virus. Health systems worldwide are implementing containment measures...

Is this current breaking news about "virus" or a coincidental mention?'''
    },
    {
        "role": "assistant",
        "content": "newsworthy"
    },

    # Example 4: NEWSWORTHY - San Diego active crisis
    {
        "role": "user",
        "content": '''This article matched the keyword "shooting".

Article Title: Active shooter situation at San Diego shopping center, multiple casualties reported
Article Description: Police have cordoned off Fashion Valley Mall following reports of gunfire. SWAT teams are on scene and the public is urged to avoid the area as the situation remains active...

Is this current breaking news about "shooting" or a coincidental mention?'''
    },
    {
        "role": "assistant",
        "content": "newsworthy"
    },

    # Example 5: NEWSWORTHY - Major US natural disaster
    {
        "role": "user",
        "content": '''This article matched the keyword "earthquake".

Article Title: 7.8 magnitude earthquake strikes California, tsunami warnings issued
Article Description: A major earthquake centered near Los Angeles has triggered emergency response across the state. Tsunami warnings are in effect for coastal areas and widespread damage is being reported...

Is this current breaking news about "earthquake" or a coincidental mention?'''
    },
    {
        "role": "assistant",
        "content": "newsworthy"
    },

    # Example 6: NEWSWORTHY - Global conflict start
    {
        "role": "user",
        "content": '''This article matched the keyword "war".

Article Title: NATO declares Article 5 after attack on member nation
Article Description: Alliance members are mobilizing forces following unprecedented strike on Poland. Emergency session called as international crisis escalates into potential global conflict...

Is this current breaking news about "war" or a coincidental mention?'''
    },
    {
        "role": "assistant",
        "content": "newsworthy"
    },

    # Example 7: COINCIDENCE - Historical retrospective
    {
        "role": "user",
        "content": '''This article matched the keyword "fire".

Article Title: Documentary explores 2003 Cedar Fire, San Diego's deadliest wildfire
Article Description: A new film examines the devastating 2003 wildfire that killed 15 people and destroyed 2,800 homes in one of California's worst fire disasters. Survivors share their stories...

Is this current breaking news about "fire" or a coincidental mention?'''
    },
    {
        "role": "assistant",
        "content": "coincidence"
    },

    # Example 8: COINCIDENCE - Ongoing story update
    {
        "role": "user",
        "content": '''This article matched the keyword "trial".

Article Title: Day 12 of high-profile corruption trial continues with witness testimony
Article Description: Prosecutors presented additional evidence in the ongoing case against former city official. Trial is expected to continue for several more weeks as both sides present their arguments...

Is this current breaking news about "trial" or a coincidental mention?'''
    },
    {
        "role": "assistant",
        "content": "coincidence"
    },

    # Example 9: COINCIDENCE - Metaphorical usage
    {
        "role": "user",
        "content": '''This article matched the keyword "explosion".

Article Title: Housing market sees explosion of new construction permits
Article Description: San Diego County approved record number of residential building permits in Q4. Real estate developers are responding to continued demand in the housing market...

Is this current breaking news about "explosion" or a coincidental mention?'''
    },
    {
        "role": "assistant",
        "content": "coincidence"
    },

    # Example 10: COINCIDENCE - Opinion piece
    {
        "role": "user",
        "content": '''This article matched the keyword "disaster".

Article Title: Opinion: City's homelessness response is a policy disaster
Article Description: The council's latest approach to addressing homelessness continues ineffective strategies that have failed for years. Better solutions are needed...

Is this current breaking news about "disaster" or a coincidental mention?'''
    },
    {
        "role": "assistant",
        "content": "coincidence"
    },

    # Example 11: COINCIDENCE - Sports metaphor
    {
        "role": "user",
        "content": '''This article matched the keyword "battle".

Article Title: Padres battle Dodgers in crucial division matchup tonight
Article Description: San Diego looks to gain ground in the playoff race with tonight's game against division rivals. Both teams are fighting for postseason positioning...

Is this current breaking news about "battle" or a coincidental mention?'''
    },
    {
        "role": "assistant",
        "content": "coincidence"
    },

    # Example 12: COINCIDENCE - Routine weather event
    {
        "role": "user",
        "content": '''This article matched the keyword "storm".

Article Title: Coastal storm forecast for weekend, normal rainfall expected
Article Description: Meteorologists predict typical winter storm pattern bringing 1-2 inches of rain to San Diego County. Residents should prepare for wet weather this weekend...

Is this current breaking news about "storm" or a coincidental mention?'''
    },
    {
        "role": "assistant",
        "content": "coincidence"
    }
]

VALIDATION_SYSTEM_PROMPT = """You are a news editor determining if articles are current breaking news requiring immediate alerts.

NEWSWORTHY articles include:
- New emergencies happening NOW in San Diego
- Events impacting the entire United States
- World-threatening events: new pandemic, newly started war, major famine, massive natural disaster

DO NOT flag as newsworthy:
- Updates to ongoing stories
- Historical or retrospective coverage
- Metaphorical keyword usage
- Opinion pieces or commentary
- Routine mentions without emergency context

Respond with ONLY ONE WORD: "newsworthy" or "coincidence"
"""


def build_validation_messages(
    title: str,
    description: str,
    matched_topic: str
) -> List[Dict[str, str]]:
    """
    Build message list for LLM validation with few-shot examples.

    Args:
        title: Article title
        description: Article description (will be truncated to 500 chars)
        matched_topic: The keyword that triggered the match

    Returns:
        List of message dicts for ChatCompletionsClient
    """
    messages = [
        {
            "role": "system",
            "content": VALIDATION_SYSTEM_PROMPT
        }
    ]

    # Add few-shot examples
    messages.extend(FEW_SHOT_EXAMPLES)

    # Add the actual article to validate
    user_prompt = f"""This article matched the keyword "{matched_topic}".

Article Title: {title}
Article Description: {description[:500]}

Is this current breaking news about "{matched_topic}" or a coincidental mention?"""

    messages.append({
        "role": "user",
        "content": user_prompt
    })

    return messages


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
    Use LLM with few-shot examples to validate if an article is genuinely
    newsworthy breaking news or just a coincidental keyword match.

    Args:
        article: Article data with 'title' and 'description'
        matched_topic: The topic keyword that triggered the match

    Returns:
        True if newsworthy, False if coincidental or on LLM failure
    """
    title = article.get('title', 'Untitled')
    description = article.get('description', '')

    try:
        # Build messages with few-shot examples
        messages = build_validation_messages(title, description, matched_topic)

        # Call LLM
        llm = ChatCompletionsClient.factory("gpt-4o-mini")
        response = await llm.chat(messages)

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
