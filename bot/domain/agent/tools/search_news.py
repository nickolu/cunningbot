"""The `search_news` agent tool.

Searches the stories this server's RSS summary channels have posted in the
last week. The data is `story_history`, which the summary poster writes for
dedup; feeds that post items directly to a channel are not in it.

Matching: the query is split into words, and each word must appear in the
story's title or summary as the start of a word, case-insensitively ("tariff"
matches "Tariffs"). Stories containing every word are returned newest first.
If none contain every word, stories containing at least half of them are
returned instead, labelled as partial matches.
"""

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import discord

from bot.app.utils.logger import get_logger
from bot.domain.agent.tools.base import AgentTool

logger = get_logger()

WINDOW_HOURS = 168
MAX_RESULTS = 10
SUMMARY_CHARS = 200
MAX_LINKS = 3

# Filler the model tends to pass along from "is there any news about X?".
_STOPWORDS = {
    "a", "an", "and", "any", "about", "the", "of", "on", "in", "for", "to",
    "or", "is", "are", "there", "news", "latest", "recent", "what", "whats",
}

_ARCHIVE_NOTE = (
    "This only covers stories summarized in this server's news channels over "
    "about the last 7 days; feeds posted directly to a channel are not searchable."
)


SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "search_news",
        "description": (
            "Search the news stories this server's RSS news channels have "
            "posted in the last 7 days. Try this first when someone asks "
            "whether there's news about something."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keywords, e.g. 'fed interest rates'",
                },
            },
            "required": ["query"],
        },
    },
}


def query_terms(query: str) -> List[str]:
    """Lowercased, de-duplicated words of the query, minus filler words.

    If the query is nothing but filler ("latest news"), keep it as-is so the
    search still means something.
    """
    words = re.findall(r"\w+", (query or "").lower())
    seen: List[str] = []
    for w in words:
        if w not in seen:
            seen.append(w)
    meaningful = [w for w in seen if w not in _STOPWORDS]
    return meaningful or seen


def _story_summary(story: Dict[str, Any]) -> str:
    summary = story.get("summary")
    if summary:
        return str(summary)
    # Breaking-news entries store the raw article instead of a summary.
    for article in story.get("articles") or []:
        if isinstance(article, dict):
            text = article.get("summary") or article.get("description")
            if text:
                return str(text)
    return ""


def _story_links(story: Dict[str, Any]) -> List[str]:
    links = [u for u in (story.get("article_urls") or []) if u]
    if not links:
        for article in story.get("articles") or []:
            if isinstance(article, dict) and article.get("link"):
                links.append(article["link"])
    return links


def _posted_at(story: Dict[str, Any]) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(story.get("posted_at")))
    except (TypeError, ValueError):
        return None


def _sort_key(story: Dict[str, Any]) -> float:
    posted = _posted_at(story)
    if posted is None:
        return 0.0
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=timezone.utc)
    return posted.timestamp()


def _count_matches(terms: List[str], text: str) -> int:
    return sum(1 for t in terms if re.search(r"\b" + re.escape(t), text))


def match_stories(
    stories: List[Dict[str, Any]], query: str
) -> Tuple[List[Dict[str, Any]], bool]:
    """Return (matches newest first, whether they are only partial matches)."""
    terms = query_terms(query)
    if not terms:
        return [], False

    full: List[Dict[str, Any]] = []
    partial: List[Dict[str, Any]] = []
    needed_for_partial = max(1, (len(terms) + 1) // 2)
    seen_titles = set()

    # Newest first up front, so the de-duplication below keeps the newest copy.
    for story in sorted(stories, key=_sort_key, reverse=True):
        title = str(story.get("title") or "")
        text = (title + "\n" + _story_summary(story)).lower()
        count = _count_matches(terms, text)
        if count == 0:
            continue
        key = title.strip().lower()
        if key and key in seen_titles:
            continue  # same story posted to more than one channel
        if key:
            seen_titles.add(key)
        if count == len(terms):
            full.append(story)
        elif count >= needed_for_partial:
            partial.append(story)

    if full:
        return full, False
    return partial, True


def format_story(story: Dict[str, Any]) -> str:
    title = story.get("title") or "Untitled"
    posted = _posted_at(story)
    date = posted.strftime("%Y-%m-%d") if posted else "unknown date"
    summary = " ".join(_story_summary(story).split())
    if len(summary) > SUMMARY_CHARS:
        summary = summary[: SUMMARY_CHARS - 1].rstrip() + "…"
    lines = [f"- {title} ({date})"]
    if summary:
        lines.append(f"  {summary}")
    links = _story_links(story)[:MAX_LINKS]
    if links:
        lines.append("  " + " ".join(links))
    return "\n".join(lines)


async def execute_search_news(
    arguments: Dict[str, Any], channel: discord.TextChannel
) -> str:
    """Execute the search_news tool."""
    query = str(arguments.get("query") or "").strip()
    if not query:
        return "No search query was provided."

    guild = getattr(channel, "guild", None)
    if guild is None:
        return "News search only works in a server."

    try:
        from bot.app.redis.rss_store import RSSRedisStore
        from bot.app.redis.serialization import guild_id_to_str

        guild_id = guild_id_to_str(guild.id)
        store = RSSRedisStore()
        stories: List[Dict[str, Any]] = []
        for channel_id in await store.get_story_history_channels(guild_id):
            stories.extend(
                await store.get_stories_within_window(
                    guild_id, int(channel_id), WINDOW_HOURS
                )
            )
    except Exception as e:
        logger.error(f"search_news failed to load story history: {e}")
        return (
            "The news archive could not be read right now. "
            "Use web_search to look this up instead."
        )

    matches, partial = match_stories(stories, query)
    if not matches:
        return (
            f"No stories in this server's news feeds matched '{query}'. "
            f"{_ARCHIVE_NOTE} Use web_search to look for it instead."
        )

    shown = matches[:MAX_RESULTS]
    header = (
        f"{len(matches)} stor{'y' if len(matches) == 1 else 'ies'} "
        f"{'partially ' if partial else ''}matched '{query}'"
    )
    if len(matches) > len(shown):
        header += f" (showing the newest {len(shown)})"
    header += ", newest first."
    if partial:
        header += " None contained every search word, so these may be off-topic."
    return (
        header + "\n" + "\n".join(format_story(s) for s in shown)
        + f"\n({_ARCHIVE_NOTE})"
    )


TOOL = AgentTool(
    config_key="search_news",
    schema=SCHEMA,
    executor=execute_search_news,
    channel_aware=True,
)
