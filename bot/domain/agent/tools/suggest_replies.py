"""The `suggest_replies` agent tool: buttons under the reply."""

from typing import Any, Dict

from bot.domain.agent.suggestions import (
    MAX_OPTION_CHARS,
    MAX_OPTIONS,
    MIN_OPTIONS,
    clean_options,
    offer_suggestions,
)
from bot.domain.agent.tools.base import AgentTool

SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "suggest_replies",
        "description": (
            "Show 2-5 buttons under your reply, each a short next message the "
            "user can send with one click (e.g. 'Summarize the last week', "
            "'Make it shorter'). Use it only at a real fork in the conversation, "
            "not on every reply. Don't also list the options in your text."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": MIN_OPTIONS,
                    "maxItems": MAX_OPTIONS,
                    "description": (
                        f"The messages, written as the user would say them. "
                        f"At most {MAX_OPTION_CHARS} characters each."
                    ),
                },
            },
            "required": ["options"],
        },
    },
}


async def execute_suggest_replies(arguments: Dict[str, Any]) -> str:
    options = clean_options(arguments.get("options"))
    if len(options) < MIN_OPTIONS:
        return f"Give at least {MIN_OPTIONS} different options."
    if len(options) > MAX_OPTIONS:
        return f"Give at most {MAX_OPTIONS} options."
    too_long = [o for o in options if len(o) > MAX_OPTION_CHARS]
    if too_long:
        return (
            f"These are over {MAX_OPTION_CHARS} characters; shorten them and "
            f"call again: {'; '.join(too_long)}"
        )
    if not offer_suggestions(options):
        return "Buttons can't be shown here. Don't mention them."
    return (
        f"{len(options)} buttons will appear under your reply. Finish your "
        f"reply without listing them."
    )


TOOL = AgentTool(
    config_key="suggest_replies",
    schema=SCHEMA,
    executor=execute_suggest_replies,
)
