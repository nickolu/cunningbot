"""
news_summary_service.py
Service for AI-powered news article ranking and summarization.
"""

from typing import List, Dict, Any
import re

from bot.api.openai.chat_completions_client import ChatCompletionsClient
from bot.app.utils.logger import get_logger

logger = get_logger()


async def filter_articles_by_instructions(
    articles: List[Dict[str, Any]],
    filter_instructions_map: Dict[str, str]
) -> List[Dict[str, Any]]:
    """
    Filter articles based on feed-specific instructions.

    Args:
        articles: All articles with feed_name field
        filter_instructions_map: {feed_name: filter_instructions}

    Returns:
        Filtered article list
    """
    if not filter_instructions_map:
        return articles

    try:
        # Group articles by feed_name
        articles_by_feed = {}
        for article in articles:
            feed_name = article.get('feed_name', 'Unknown')
            if feed_name not in articles_by_feed:
                articles_by_feed[feed_name] = []
            articles_by_feed[feed_name].append(article)

        filtered_articles = []

        # Process each feed
        for feed_name, feed_articles in articles_by_feed.items():
            filter_instr = filter_instructions_map.get(feed_name)

            if not filter_instr:
                # No filter for this feed, keep all articles
                filtered_articles.extend(feed_articles)
                continue

            # Prepare article list for LLM
            article_list = [
                f"{i+1}. {a.get('title', 'Untitled')} - {a.get('description', '')[:200]}"
                for i, a in enumerate(feed_articles)
            ]

            prompt = f"""Filter these articles based on: "{filter_instr}"

Articles:
{chr(10).join(article_list)}

Return ONLY a JSON array of article numbers to KEEP (articles that match the filter criteria).
Example: [1, 3, 5, 8]

If none match, return: []"""

            llm = ChatCompletionsClient.factory("gpt-4o-mini")
            response = await llm.chat([
                {"role": "system", "content": "You are a news filter. Return only the JSON array."},
                {"role": "user", "content": prompt}
            ])

            # Parse JSON response
            import json

            json_match = re.search(r'\[[\d,\s]*\]', response)
            if not json_match:
                logger.warning(f"Could not parse filter response for {feed_name}, keeping all articles")
                filtered_articles.extend(feed_articles)
                continue

            keep_indices = json.loads(json_match.group())

            # Keep matching articles
            for idx in keep_indices:
                if 0 < idx <= len(feed_articles):
                    filtered_articles.append(feed_articles[idx - 1])

            logger.info(f"Filtered {feed_name}: {len(keep_indices)}/{len(feed_articles)} articles kept")

        logger.info(f"Total filtered: {len(filtered_articles)}/{len(articles)} articles")
        return filtered_articles

    except Exception as e:
        logger.error(f"Error filtering articles: {e}")
        # Fallback: return all articles
        return articles


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


async def cluster_articles_by_story(
    articles: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Group similar articles into story clusters using LLM.

    Returns:
        List of clusters: [{"articles": [...], "theme": "..."}, ...]
    """
    if not articles:
        return []

    if len(articles) == 1:
        return [{"articles": articles, "theme": "single story"}]

    try:
        # Prepare article list for LLM
        article_list = [
            f"{i+1}. {a.get('title', 'Untitled')} - {a.get('description', '')[:100]}"
            for i, a in enumerate(articles)
        ]

        prompt = f"""Group these articles into stories covering the same event or topic.
Articles about the same core story should be grouped together, even with different angles.

Articles:
{chr(10).join(article_list)}

Return JSON mapping cluster IDs to article numbers:
{{"cluster_1": [1, 5, 8], "cluster_2": [2, 4], "cluster_3": [3], ...}}

Aim for 5-8 total clusters. Single-article clusters are fine."""

        llm = ChatCompletionsClient.factory("gpt-4o-mini")
        response = await llm.chat([
            {"role": "system", "content": "You are a news editor grouping similar articles."},
            {"role": "user", "content": prompt}
        ])

        # Parse JSON response
        import json

        json_match = re.search(r'\{[^}]+\}', response, re.DOTALL)
        if not json_match:
            raise ValueError("No JSON found")

        clusters_dict = json.loads(json_match.group())

        # Convert to cluster list
        clusters = []
        for cluster_id, indices in clusters_dict.items():
            cluster_articles = []
            for idx in indices:
                if 0 < idx <= len(articles):
                    cluster_articles.append(articles[idx - 1])

            if cluster_articles:
                clusters.append({
                    "articles": cluster_articles,
                    "theme": cluster_id
                })

        clusters = clusters[:8]  # Limit to 8 clusters
        logger.info(f"Clustered {len(articles)} articles into {len(clusters)} stories")
        return clusters

    except Exception as e:
        logger.error(f"Error clustering: {e}")
        # Fallback: each article is own cluster
        return [{"articles": [a], "theme": f"story_{i+1}"}
                for i, a in enumerate(articles[:8])]


async def generate_story_summaries(
    story_clusters: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Generate unified summaries for each story cluster.

    Args:
        story_clusters: List of clusters with articles

    Returns:
        List of dicts with title, summary, links for each story
    """
    if not story_clusters:
        return []

    try:
        summaries = []

        for cluster in story_clusters:
            articles = cluster["articles"]

            if len(articles) > 1:
                # Multi-article: synthesize unified story
                article_details = [
                    f"{i+1}. Title: {a.get('title', 'Untitled')}\n"
                    f"   Source: {a.get('source', 'Unknown')}\n"
                    f"   Description: {a.get('description', '')[:300]}"
                    for i, a in enumerate(articles)
                ]

                prompt = f"""Create unified summary for story covered by multiple sources.

Story articles:
{chr(10).join(article_details)}

Create:
1. Neutral, informative story title (max 80 characters)
2. One-sentence summary capturing key information (max 25 words)

Format:
TITLE: [Your title here]
SUMMARY: [Your summary here]"""

            else:
                # Single article: refine presentation
                article = articles[0]
                prompt = f"""Refine this story for concise presentation.

Title: {article.get('title', 'Untitled')}
Description: {article.get('description', '')}

Create:
1. Refined story title (max 80 characters)
2. One-sentence summary (max 25 words)

Format:
TITLE: [Your title here]
SUMMARY: [Your summary here]"""

            # Generate title and summary
            llm = ChatCompletionsClient.factory("gpt-4o-mini")
            response = await llm.chat([
                {"role": "system", "content": "You are a news editor creating concise summaries."},
                {"role": "user", "content": prompt}
            ])

            # Parse response
            title_match = re.search(r'TITLE:\s*(.+)', response)
            summary_match = re.search(r'SUMMARY:\s*(.+)', response)

            title = title_match.group(1).strip() if title_match else articles[0].get('title', 'Breaking News')
            summary = summary_match.group(1).strip() if summary_match else articles[0].get('description', '')[:100]

            # Collect all source links
            links = [
                {"source": a.get('source', 'Unknown'), "url": a.get('link', '')}
                for a in articles
            ]

            summaries.append({
                "title": title,
                "summary": summary,
                "links": links
            })

        logger.info(f"Generated {len(summaries)} story summaries")
        return summaries

    except Exception as e:
        logger.error(f"Error generating summaries: {e}")
        # Fallback
        return [
            {
                "title": cluster["articles"][0].get('title', 'News Update'),
                "summary": cluster["articles"][0].get('description', '')[:100],
                "links": [{"source": a.get('source', 'Unknown'), "url": a.get('link', '')}
                         for a in cluster["articles"]]
            }
            for cluster in story_clusters
        ]


async def generate_summary_text(
    story_summaries: List[Dict[str, Any]],
    edition: str = "Summary"
) -> str:
    """
    Format story summaries with new plain text title layout.

    Args:
        story_summaries: List of story summaries with title, summary, links
        edition: "Morning", "Evening", or "Summary" for title

    Returns:
        Formatted summary text with Discord markdown links
    """
    if not story_summaries:
        return "No articles available for summary."

    try:
        summary_lines = ["**Top Stories:**\n"]

        for story in story_summaries:
            # Plain text title (user preference)
            summary_lines.append(f"**{story['title']}**")

            # One-sentence summary
            summary_lines.append(f"{story['summary']}")

            # Named source links
            source_links = [
                f"[{link['source']}]({link['url']})"
                for link in story['links']
            ]
            summary_lines.append(f"{' '.join(source_links)}\n")

        logger.info(f"Formatted {len(story_summaries)} stories")
        return "\n".join(summary_lines)

    except Exception as e:
        logger.error(f"Error formatting: {e}")
        # Fallback: simple list format
        fallback_articles = []
        for story in story_summaries:
            if story.get('links'):
                fallback_articles.append({
                    'title': story['title'],
                    'link': story['links'][0]['url'],
                    'source': story['links'][0]['source']
                })
        return generate_fallback_summary(fallback_articles)


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
    filter_map: Dict[str, str] = None,
    edition: str = "Summary"
) -> Dict[str, Any]:
    """
    Main orchestrator function for generating news summaries with filtering and clustering.

    Args:
        articles: All pending articles
        feed_names: List of feed names contributing articles
        filter_map: Optional dict mapping feed_name to filter_instructions
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

    # Step 0: Filter articles (NEW)
    if filter_map:
        articles = await filter_articles_by_instructions(articles, filter_map)
        logger.info(f"After filtering: {len(articles)} articles remain")

    # Step 1: Rank articles by importance
    ranked_articles = await rank_articles_by_importance(articles)

    # Step 2: Take top 18 (increased from 10)
    top_articles = ranked_articles[:18]

    # Step 3: Cluster articles (NEW)
    story_clusters = await cluster_articles_by_story(top_articles)

    # Step 4: Generate unified summaries
    story_summaries = await generate_story_summaries(story_clusters)

    # Step 5: Format as Discord markdown
    summary_text = await generate_summary_text(story_summaries, edition)

    logger.info(f"Summary generated: {len(story_clusters)} stories from {len(articles)} articles")

    return {
        "summary_text": summary_text,
        "top_articles": top_articles,
        "total_articles": len(articles),
        "feed_count": len(feed_names)
    }
