# bot/core/settings/personality_service.py
from typing import Optional
from bot.domain.app_state import get_state_value_from_interaction, set_state_value_from_interaction

PERSONALITY_KEY = "current_personality"
MAX_PERSONALITY_LENGTH = 200

def get_personality(interaction_guild_id: Optional[int] = None) -> Optional[str]:
    """
    Retrieves the current personality from the app state for the specified guild.
    
    Args:
        interaction_guild_id: The guild_id from a Discord interaction (can be None for DMs)
                             If None, uses global personality.
    
    Returns:
        The guild's personality, falling back to global personality if not set, or empty string
    """
    return get_state_value_from_interaction(PERSONALITY_KEY, interaction_guild_id) or ""

def set_personality(personality_text: Optional[str], interaction_guild_id: Optional[int] = None) -> None:
    """
    Sets the current personality in the app state for the specified guild.

    Args:
        personality_text: The personality description. Max 200 chars. 
                          None to clear the personality.
        interaction_guild_id: The guild_id from a Discord interaction (can be None for DMs)
                             If None, sets global personality.

    Raises:
        ValueError: If personality_text exceeds the maximum length.
    """
    if personality_text:
        personality_text = personality_text.strip()
        if personality_text.startswith('"') and personality_text.endswith('"'):
            personality_text = personality_text[1:-1]
    
    if personality_text and len(personality_text) > MAX_PERSONALITY_LENGTH:
        raise ValueError(f"Personality text is too long (max {MAX_PERSONALITY_LENGTH} characters).")
    
    set_state_value_from_interaction(PERSONALITY_KEY, personality_text, interaction_guild_id)
