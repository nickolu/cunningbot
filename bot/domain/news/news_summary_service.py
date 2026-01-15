"""
news_summary_service.py
Service for AI-powered news article ranking and summarization.
"""

from typing import List, Dict, Any
import re

from bot.api.openai.chat_completions_client import ChatCompletionsClient
from bot.app.utils.logger import get_logger

logger = get_logger()


async def rank_articles_by_importance(articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Use LLM to rank articles by importance and newsworthiness.

    Args:
        articles: List of article dictionaries with title, description, link, etc.

    Returns:
        List of articles sorted by importance (highest first)
    """
    if not articles:
        return []

    if len(articles) == 1:
        return articles

    try:
        # Prepare article list for LLM (limit description length)
        article_summaries = [
            f"{i+1}. {a.get('title', 'Untitled')} - {a.get('description', '')[:200]}"
            for i, a in enumerate(articles)
        ]

        prompt = f"""You are a news editor. Rank these articles by importance and newsworthiness.

Consider:
- Timeliness and relevance
- Impact on readers
- Uniqueness of information
- Source credibility

Articles:
{chr(10).join(article_summaries)}

Return ONLY a comma-separated list of article numbers in order of importance (most important first).
For example: "3,1,7,2,5"
Do not include any other text or explanation."""

        llm = ChatCompletionsClient.factory("gpt-4o-mini")
        response = await llm.chat([
            {"role": "system", "content": "You are a professional news editor who ranks articles by importance."},
            {"role": "user", "content": prompt}
        ])

        # Parse the response
        ranking = parse_ranking_response(response)

        # Reorder articles based on ranking
        ranked_articles = reorder_articles(articles, ranking)

        logger.info(f"Ranked {len(articles)} articles using AI")
        return ranked_articles

    except Exception as e:
        logger.error(f"Error ranking articles with AI: {e}")
        logger.info("Falling back to chronological order")
        # Fallback: return articles in original order (presumably chronological)
        return articles


def parse_ranking_response(response: str) -> List[int]:
    """
    Parse the LLM ranking response to extract article numbers.

    Args:
        response: LLM response string like "3,1,7,2,5"

    Returns:
        List of article indices (0-based)
    """
    # Extract numbers from response
    numbers = re.findall(r'\d+', response)

    if not numbers:
        raise ValueError(f"Could not parse ranking from response: {response}")

    # Convert to 0-based indices
    ranking = [int(n) - 1 for n in numbers]

    return ranking


def reorder_articles(articles: List[Dict[str, Any]], ranking: List[int]) -> List[Dict[str, Any]]:
    """
    Reorder articles based on ranking indices.

    Args:
        articles: Original article list
        ranking: List of indices in desired order

    Returns:
        Reordered article list
    """
    try:
        reordered = []
        for idx in ranking:
            if 0 <= idx < len(articles):
                reordered.append(articles[idx])

        # Add any articles that weren't in the ranking (shouldn't happen, but safety)
        ranked_indices = set(ranking)
        for i, article in enumerate(articles):
            if i not in ranked_indices:
                reordered.append(article)

        return reordered
    except Exception as e:
        logger.error(f"Error reordering articles: {e}")
        return articles


async def generate_summary_text(
    top_articles: List[Dict[str, Any]],
    edition: str = "Summary"
) -> str:
    """
    Generate natural language summary with embedded article links.

    Args:
        top_articles: List of top-ranked articles (typically top 10)
        edition: "Morning", "Evening", or "Summary" for title

    Returns:
        AI-generated summary text with Discord markdown links
    """
    if not top_articles:
        return "No articles available for summary."

    try:
        # Prepare article details for LLM
        article_details = []
        for i, a in enumerate(top_articles, 1):
            title = a.get('title', 'Untitled')
            desc = a.get('description', '')[:300]
            link = a.get('link', '')
            source = a.get('source', 'Unknown')

            article_details.append(
                f"{i}. [{title}]({link}) (Source: {source})\n   {desc}"
            )

        prompt = f"""You are a news summarizer. Create a concise, engaging summary of today's top stories.

Requirements:
- Write 2-3 paragraphs (max 500 words)
- Naturally incorporate article links using Discord markdown format: [Title](URL)
- Group related stories together when possible
- Use engaging, conversational tone
- Focus on the most important/interesting aspects
- Do NOT include a title or header (it will be added separately)

Top Articles:
{chr(10).join(article_details)}

Write the summary:"""

        llm = ChatCompletionsClient.factory("gpt-4o-mini")
        response = await llm.chat([
            {"role": "system", "content": "You are a professional news summarizer."},
            {"role": "user", "content": prompt}
        ])

        logger.info(f"Generated summary text from {len(top_articles)} articles")
        return response.strip()

    except Exception as e:
        logger.error(f"Error generating summary text: {e}")
        # Fallback: simple list format
        return generate_fallback_summary(top_articles)


def generate_fallback_summary(articles: List[Dict[str, Any]]) -> str:
    """
    Generate a simple list-based summary as fallback.

    Args:
        articles: List of articles

    Returns:
        Simple markdown list of article titles and links
    """
    lines = ["Here are today's top stories:"]

    for i, article in enumerate(articles, 1):
        title = article.get('title', 'Untitled')
        link = article.get('link', '')
        source = article.get('source', 'Unknown')

        lines.append(f"{i}. [{title}]({link}) - {source}")

    return "\n".join(lines)


async def generate_news_summary(
    articles: List[Dict[str, Any]],
    feed_names: List[str],
    edition: str = "Summary"
) -> Dict[str, Any]:
    """
    Main orchestrator function for generating news summaries.

    Args:
        articles: All pending articles
        feed_names: List of feed names contributing articles
        edition: "Morning", "Evening", or "Summary"

    Returns:
        Dictionary with:
        - summary_text: AI-generated summary with embedded links
        - top_articles: List of top articles (for reference)
        - total_articles: Total number of articles processed
        - feed_count: Number of feeds
    """
    logger.info(f"Generating {edition} summary for {len(articles)} articles from {len(feed_names)} feeds")

    if not articles:
        return {
            "summary_text": "No articles to summarize.",
            "top_articles": [],
            "total_articles": 0,
            "feed_count": len(feed_names)
        }

    # Limit to most recent 50 articles if there are too many
    if len(articles) > 50:
        logger.info(f"Limiting from {len(articles)} to 50 most recent articles")
        articles = articles[:50]

    # Step 1: Rank articles by importance
    ranked_articles = await rank_articles_by_importance(articles)

    # Step 2: Take top 10 for summary
    top_articles = ranked_articles[:10]

    # Step 3: Generate summary text
    summary_text = await generate_summary_text(top_articles, edition)

    logger.info(f"Summary generated successfully: {len(summary_text)} characters")

    return {
        "summary_text": summary_text,
        "top_articles": top_articles,
        "total_articles": len(articles),
        "feed_count": len(feed_names)
    }
