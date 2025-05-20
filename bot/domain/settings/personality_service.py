# bot/core/settings/personality_service.py
from typing import Optional
from bot.domain.app_state import get_state_value, set_state_value

PERSONALITY_KEY = "current_personality"
MAX_PERSONALITY_LENGTH = 200

def get_personality() -> Optional[str]:
    """Retrieves the current personality from the app state."""
    return get_state_value(PERSONALITY_KEY)

def set_personality(personality_text: Optional[str]) -> None:
    """Sets the current personality in the app state.

    Args:
        personality_text: The personality description. Max 200 chars. 
                          None to clear the personality.

    Raises:
        ValueError: If personality_text exceeds the maximum length.
    """
    if personality_text:
        personality_text = personality_text.strip()
        if personality_text.startswith('"') and personality_text.endswith('"'):
            personality_text = personality_text[1:-1]
    
    if personality_text and len(personality_text) > MAX_PERSONALITY_LENGTH:
        raise ValueError(f"Personality text is too long (max {MAX_PERSONALITY_LENGTH} characters).")
    
    set_state_value(PERSONALITY_KEY, personality_text)
