"""
news_summary_service.py
Service for AI-powered news article ranking and summarization.
"""

from typing import List, Dict, Any
import re

from bot.api.openai.chat_completions_client import ChatCompletionsClient
from bot.app.utils.logger import get_logger

logger = get_logger()

# Article processing limit defaults
DEFAULT_INITIAL_LIMIT = 50
DEFAULT_TOP_ARTICLES_LIMIT = 18
DEFAULT_CLUSTER_LIMIT = 8

# Validation bounds
MIN_INITIAL_LIMIT = 10
MAX_INITIAL_LIMIT = 200
MIN_TOP_ARTICLES_LIMIT = 5
MAX_TOP_ARTICLES_LIMIT = 50
MIN_CLUSTER_LIMIT = 3
MAX_CLUSTER_LIMIT = 20


def get_channel_article_limits(guild_id: int, channel_id: int) -> Dict[str, int]:
    """
    Retrieve article processing limits for a specific channel.
    Returns defaults if no custom limits configured.
    """
    from bot.app.app_state import get_state_value

    guild_id_str = str(guild_id)
    all_limits = get_state_value("channel_article_limits", guild_id_str) or {}
    channel_limits = all_limits.get(str(channel_id), {})

    return {
        "initial_limit": channel_limits.get("initial_limit", DEFAULT_INITIAL_LIMIT),
        "top_articles_limit": channel_limits.get("top_articles_limit", DEFAULT_TOP_ARTICLES_LIMIT),
        "cluster_limit": channel_limits.get("cluster_limit", DEFAULT_CLUSTER_LIMIT)
    }


def validate_limit_value(value: int, min_val: int, max_val: int, name: str) -> None:
    """Validate that a limit value is within acceptable bounds."""
    if not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")

    if value < min_val or value > max_val:
        raise ValueError(f"{name} must be between {min_val} and {max_val}, got {value}")


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
    articles: List[Dict[str, Any]],
    max_clusters: int = 8
) -> List[Dict[str, Any]]:
    """
    Group similar articles into story clusters using LLM.

    Args:
        articles: List of articles to cluster
        max_clusters: Maximum number of clusters to return (default: 8)

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

        clusters = clusters[:max_clusters]
        logger.info(f"Clustered {len(articles)} articles into {len(clusters)} stories (max: {max_clusters})")
        return clusters

    except Exception as e:
        logger.error(f"Error clustering: {e}")
        # Fallback: each article is own cluster
        return [{"articles": [a], "theme": f"story_{i+1}"}
                for i, a in enumerate(articles[:max_clusters])]


async def generate_preliminary_title(articles: List[Dict[str, Any]]) -> str:
    """
    Generate a quick preliminary title for a story cluster.
    Used for duplicate detection before full summary generation.
    """
    if not articles:
        return "Untitled Story"

    if len(articles) == 1:
        return articles[0].get("title", "Untitled Story")

    try:
        # Quick prompt to get just the title
        article_titles = [a.get("title", "Untitled") for a in articles]
        prompt = f"""These articles cover the same story. Generate ONE unified title (max 80 chars):

{chr(10).join(f"{i+1}. {t}" for i, t in enumerate(article_titles))}

Return ONLY the title, nothing else."""

        llm = ChatCompletionsClient.factory("gpt-4o-mini")
        response = await llm.chat([
            {"role": "system", "content": "You are a news editor. Return only the title."},
            {"role": "user", "content": prompt}
        ])

        return response.strip()[:80]

    except Exception as e:
        logger.error(f"Error generating preliminary title: {e}")
        return articles[0].get("title", "Untitled Story")


async def check_story_similarity(
    new_title: str,
    new_articles: List[Dict[str, Any]],
    historical_titles: List[str],
    story_history: List[Dict[str, Any]]
) -> tuple[bool, Dict[str, Any] | None]:
    """
    Check if a new story is semantically similar to any story in today's history.

    Returns:
        (is_duplicate, similar_story_dict or None)
    """
    if not historical_titles:
        return False, None

    try:
        # Prepare comparison
        historical_list = "\n".join(f"{i+1}. {t}" for i, t in enumerate(historical_titles))

        prompt = f"""Compare this NEW story title against TODAY'S posted stories:

NEW STORY: "{new_title}"

TODAY'S POSTED STORIES:
{historical_list}

Is the NEW story about the same event/topic as any POSTED story?
- Consider: Same core event, same main subject, covering same news
- Ignore: Minor wording differences, different sources, different angles

Return JSON: {{"is_similar": true/false, "similar_to_index": 1-based number or null}}

If similar, return the index of the most similar posted story."""

        llm = ChatCompletionsClient.factory("gpt-4o-mini")
        response = await llm.chat([
            {"role": "system", "content": "You are a news editor detecting duplicate stories. Return only JSON."},
            {"role": "user", "content": prompt}
        ])

        # Parse JSON response
        import json
        json_match = re.search(r'\{[^}]+\}', response, re.DOTALL)
        if not json_match:
            logger.warning("Could not parse similarity check response")
            return False, None

        result = json.loads(json_match.group())

        if result.get("is_similar"):
            idx = result.get("similar_to_index")
            if idx and 0 < idx <= len(story_history):
                return True, story_history[idx - 1]

        return False, None

    except Exception as e:
        logger.error(f"Error checking story similarity: {e}")
        return False, None


async def check_significant_updates(
    new_articles: List[Dict[str, Any]],
    historical_story: Dict[str, Any]
) -> bool:
    """
    Check if new articles contain significant updates compared to historical story.

    Returns True if significant new information is present.
    """
    try:
        # Get article summaries
        new_content = "\n".join([
            f"- {a.get('title', '')}: {a.get('description', '')[:200]}"
            for a in new_articles[:3]  # Limit to 3 articles
        ])

        old_summary = historical_story.get("summary", "")

        prompt = f"""Compare NEW articles against PREVIOUS coverage of this story:

PREVIOUS COVERAGE:
{old_summary}

NEW ARTICLES:
{new_content}

Do the NEW articles contain SIGNIFICANT updates or developments?
- Yes if: Major new developments, breaking updates, important changes
- No if: Same information, minor details, repetitive coverage

Return JSON: {{"has_significant_updates": true/false, "reason": "brief explanation"}}"""

        llm = ChatCompletionsClient.factory("gpt-4o-mini")
        response = await llm.chat([
            {"role": "system", "content": "You are a news editor evaluating story updates. Return only JSON."},
            {"role": "user", "content": prompt}
        ])

        # Parse JSON response
        import json
        json_match = re.search(r'\{[^}]+\}', response, re.DOTALL)
        if not json_match:
            logger.warning("Could not parse update check response")
            return False  # Conservative: don't show if unsure

        result = json.loads(json_match.group())
        has_updates = result.get("has_significant_updates", False)

        if has_updates:
            reason = result.get("reason", "")
            logger.info(f"Significant updates detected: {reason}")

        return has_updates

    except Exception as e:
        logger.error(f"Error checking significant updates: {e}")
        return False  # Conservative: don't show if error


async def filter_duplicate_stories(
    story_clusters: List[Dict[str, Any]],
    story_history: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Filter duplicate stories using hybrid approach:
    1. Generate preliminary titles for each cluster
    2. Check semantic similarity against today's history
    3. If similar, check for significant updates
    4. Keep unique stories and stories with significant updates

    Returns filtered list of story clusters.
    """
    if not story_history:
        logger.info("No story history for today, keeping all clusters")
        return story_clusters

    historical_titles = [s["title"] for s in story_history]
    logger.info(f"Checking {len(story_clusters)} new clusters against {len(historical_titles)} historical stories")

    filtered_clusters = []

    for cluster in story_clusters:
        articles = cluster["articles"]

        # Generate preliminary title
        prelim_title = await generate_preliminary_title(articles)

        # Check similarity
        is_duplicate, similar_story = await check_story_similarity(
            prelim_title,
            articles,
            historical_titles,
            story_history
        )

        if is_duplicate and similar_story:
            # Similar story found, check for updates
            logger.info(f"Story '{prelim_title}' similar to '{similar_story.get('title')}' - checking for updates")

            has_updates = await check_significant_updates(articles, similar_story)

            if has_updates:
                logger.info(f"Significant updates found - keeping story")
                filtered_clusters.append(cluster)
            else:
                logger.info(f"No significant updates - filtering duplicate story")
                # Skip this cluster
        else:
            # Unique story, keep it
            filtered_clusters.append(cluster)

    logger.info(f"Story-level dedup: {len(story_clusters)} -> {len(filtered_clusters)} clusters")
    return filtered_clusters


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

            # Collect all source links and deduplicate by URL
            seen_urls = set()
            links = []
            for a in articles:
                url = a.get('link', '')
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    links.append({
                        "source": a.get('source', 'Unknown'),
                        "url": url
                    })

            summaries.append({
                "title": title,
                "summary": summary,
                "links": links
            })

        logger.info(f"Generated {len(summaries)} story summaries")
        return summaries

    except Exception as e:
        logger.error(f"Error generating summaries: {e}")
        # Fallback - deduplicate links here too
        fallback_summaries = []
        for cluster in story_clusters:
            seen_urls = set()
            links = []
            for a in cluster["articles"]:
                url = a.get('link', '')
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    links.append({
                        "source": a.get('source', 'Unknown'),
                        "url": url
                    })

            fallback_summaries.append({
                "title": cluster["articles"][0].get('title', 'News Update'),
                "summary": cluster["articles"][0].get('description', '')[:100],
                "links": links
            })
        return fallback_summaries


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

            # Named source links with bullet separators
            source_links = [
                f"[{link['source']}]({link['url']})"
                for link in story['links']
            ]
            summary_lines.append(f"{' • '.join(source_links)}\n")

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
    story_history: List[Dict[str, Any]] = None,
    edition: str = "Summary",
    initial_limit: int = 50,
    top_articles_limit: int = 18,
    cluster_limit: int = 8
) -> Dict[str, Any]:
    """
    Main orchestrator function for generating news summaries with filtering, clustering, and deduplication.

    Args:
        articles: All pending articles
        feed_names: List of feed names contributing articles
        filter_map: Optional dict mapping feed_name to filter_instructions
        story_history: Optional list of stories already posted today (for deduplication)
        edition: "Morning", "Evening", or "Summary"
        initial_limit: Max articles to process initially (default: 50)
        top_articles_limit: Max articles to rank and cluster (default: 18)
        cluster_limit: Max story clusters to generate (default: 8)

    Returns:
        Dictionary with:
        - summary_text: AI-generated summary with embedded links
        - story_summaries: List of story summary dicts (for history saving)
        - top_articles: List of top articles (for reference)
        - total_articles: Total number of articles processed
        - feed_count: Number of feeds
    """
    logger.info(f"Generating {edition} summary for {len(articles)} articles from {len(feed_names)} feeds")

    if not articles:
        return {
            "summary_text": "No articles to summarize.",
            "story_summaries": [],
            "top_articles": [],
            "total_articles": 0,
            "feed_count": len(feed_names)
        }

    # Limit to most recent N articles if there are too many
    if len(articles) > initial_limit:
        logger.info(f"Limiting from {len(articles)} to {initial_limit} most recent articles")
        articles = articles[:initial_limit]

    # Step 0: Filter articles by feed instructions
    if filter_map:
        articles = await filter_articles_by_instructions(articles, filter_map)
        logger.info(f"After feed filtering: {len(articles)} articles remain")

    # Step 0.5: Article-level deduplication (filter out URLs already used today)
    if story_history:
        used_urls = set()
        for story in story_history:
            used_urls.update(story.get("article_urls", []))

        original_count = len(articles)
        articles = [a for a in articles if a.get("link") not in used_urls]
        logger.info(f"Article-level dedup: {original_count} -> {len(articles)} articles ({len(used_urls)} URLs filtered)")

        if not articles:
            logger.info("All articles were already covered today")
            return {
                "summary_text": "All articles have been covered in earlier summaries today.",
                "story_summaries": [],
                "top_articles": [],
                "total_articles": 0,
                "feed_count": len(feed_names)
            }

    # Step 1: Rank articles by importance
    ranked_articles = await rank_articles_by_importance(articles)

    # Step 2: Take top N articles
    top_articles = ranked_articles[:top_articles_limit]
    logger.info(f"Selected top {len(top_articles)} articles from {len(ranked_articles)} ranked articles")

    # Step 3: Cluster articles into stories
    story_clusters = await cluster_articles_by_story(top_articles, max_clusters=cluster_limit)

    # Step 3.5: Story-level deduplication (filter duplicate stories)
    if story_history:
        story_clusters = await filter_duplicate_stories(story_clusters, story_history)

        if not story_clusters:
            logger.info("All stories were duplicates of earlier summaries")
            return {
                "summary_text": "All stories have been covered in earlier summaries today.",
                "story_summaries": [],
                "top_articles": [],
                "total_articles": len(articles),
                "feed_count": len(feed_names)
            }

    # Step 4: Generate unified summaries
    story_summaries = await generate_story_summaries(story_clusters)

    # Step 5: Format as Discord markdown
    summary_text = await generate_summary_text(story_summaries, edition)

    logger.info(f"Summary generated: {len(story_clusters)} unique stories from {len(articles)} articles")

    return {
        "summary_text": summary_text,
        "story_summaries": story_summaries,
        "top_articles": top_articles,
        "total_articles": len(articles),
        "feed_count": len(feed_names)
    }
