"""The `search_gifs` agent tool."""

from typing import Any, Dict

from bot.api.animation_factory.client import AnimationFactoryClient
from bot.app.utils.logger import get_logger
from bot.domain.agent.tools.base import AgentTool

logger = get_logger()


SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "search_gifs",
        "description": (
            "Search for animated GIFs by keyword. Returns a list of matching GIF URLs "
            "from the Animation Factory database."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search keyword(s) for finding GIFs",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return (1-12). Default 5.",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
}


async def execute_search_gifs(arguments: Dict[str, Any]) -> str:
    """Execute the search_gifs tool."""
    query = arguments.get("query", "")
    limit = min(max(arguments.get("limit", 5), 1), 12)

    if not query:
        return "No search query provided."

    client = AnimationFactoryClient()
    try:
        results = await client.search(query, limit=limit)
    except RuntimeError as e:
        return f"GIF search error: {e}"

    if not results:
        return f"No GIFs found for '{query}'."

    lines = [f"Found {len(results)} GIF(s) for '{query}':"]
    for r in results:
        filename = r.get("filename", "unknown")
        # Build the clear-background URL
        url = f"https://manchat.men/af/clear/{filename}"
        lines.append(f"  - {filename}: {url}")

    return "\n".join(lines)


TOOL = AgentTool(
    config_key="search_gifs",
    schema=SCHEMA,
    executor=execute_search_gifs,
    channel_aware=False,
)
