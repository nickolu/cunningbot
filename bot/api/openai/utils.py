
from typing import List

from bot.app.utils.logger import get_logger
import re

logger = get_logger()

# Helper function to sanitize names for OpenAI API
def sanitize_name(name: str) -> str:
    """Sanitizes a name to conform to OpenAI's required pattern and length."""
    if not name: # Handle empty input name
        return "unknown_user" # Default for empty name

    # Replace disallowed characters (whitespace, <, |, \, /, >) with underscore
    sanitized = re.sub(r"[\s<|\\/>]+", "_", name)
    
    # If sanitization results in an empty string (e.g., name was only disallowed chars), provide a default
    if not sanitized:
        return "unknown_user"
        
    # Ensure name is not longer than 64 characters
    return sanitized[:64]

def transform_messages_to_openai(messages: List[dict[str, str]]) -> List[dict[str, str]]:
    # Passes through message dicts, ensuring OpenAI-compatible keys
    result = []
    for msg in messages:
        entry = {"role": msg["role"], "content": msg["content"]}
        if "name" in msg and msg["name"]:
            entry["name"] = msg["name"]
        result.append(entry)
    return result