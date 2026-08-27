"""The `web_search` agent tool."""

from typing import Any, Dict

from bot.app.utils.logger import get_logger
from bot.domain.agent.tools.base import AgentTool

logger = get_logger()


SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the web for current information on any topic. "
            "Returns relevant results with titles, snippets, and source URLs. "
            "Use this when someone asks about recent events, current facts, "
            "or anything that benefits from up-to-date information."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query",
                },
            },
            "required": ["query"],
        },
    },
}


async def execute_web_search(arguments: Dict[str, Any]) -> str:
    """Execute the web_search tool."""
    query = arguments.get("query", "")

    if not query:
        return "No search query provided."

    try:
        from bot.api.perplexity.client import PerplexityClient

        client = PerplexityClient()
        results = await client.search(query, max_results=5)
    except EnvironmentError:
        return "Web search is not available (PERPLEXITY_API_KEY not configured)."
    except RuntimeError as e:
        return f"Web search error: {e}"

    if not results:
        return f"No web results found for '{query}'."

    lines = [f"Found {len(results)} result(s) for '{query}':\n"]
    for r in results:
        title = r.get("title", "Untitled")
        snippet = r.get("snippet", "")
        url = r.get("url", "")
        date = r.get("date", "")
        date_str = f" ({date})" if date else ""
        lines.append(f"**{title}**{date_str}")
        if snippet:
            lines.append(snippet)
        if url:
            lines.append(url)
        lines.append("")  # blank line between results

    return "\n".join(lines).strip()


TOOL = AgentTool(
    config_key="web_search",
    schema=SCHEMA,
    executor=execute_web_search,
    channel_aware=False,
)
