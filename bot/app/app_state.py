# bot/core/app_state.py
from typing import Optional, Any, Dict
import json
import os

# Define the path for the state file
STATE_FILE_PATH = os.path.join(os.path.dirname(__file__), "app_state.json")
GUILD_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".guild_config.json")

# Initialize the application state (default structure)
_app_state: Dict[str, Dict[str, Any]] = {
    "global": {
        "current_personality": None,  # Global default personality
        "default_persona": "discord_user",  # Global default persona
    }
}

def _load_guild_config() -> Dict[str, Any]:
    """Load guild configuration from .guild_config.json"""
    try:
        if os.path.exists(GUILD_CONFIG_PATH):
            with open(GUILD_CONFIG_PATH, 'r') as f:
                return json.load(f)
        else:
            print(f"Guild config file {GUILD_CONFIG_PATH} not found. Using default global only.")
            return {"global": {"guild_id": "global", "guild_name": "Global (no guild)"}}
    except (IOError, json.JSONDecodeError) as e:
        print(f"Error loading guild config from {GUILD_CONFIG_PATH}: {e}. Using default global only.")
        return {"global": {"guild_id": "global", "guild_name": "Global (no guild)"}}

def _get_guild_id_from_interaction_guild_id(interaction_guild_id: Optional[int]) -> str:
    """Convert Discord interaction guild_id to string format used in our config"""
    if interaction_guild_id is None:
        return "global"
    return str(interaction_guild_id)

def _save_state_to_file() -> None:
    """Saves the current application state to the JSON file."""
    try:
        with open(STATE_FILE_PATH, 'w') as f:
            json.dump(_app_state, f, indent=4)
    except IOError as e:
        print(f"Error saving app state to {STATE_FILE_PATH}: {e}")

def _load_state_from_file() -> None:
    """Loads the application state from the JSON file if it exists."""
    global _app_state
    if os.path.exists(STATE_FILE_PATH):
        try:
            with open(STATE_FILE_PATH, 'r') as f:
                loaded_state = json.load(f)
                
                # Ensure the loaded state has the proper structure
                if not isinstance(loaded_state, dict):
                    raise ValueError("State file must contain a dictionary")
                
                # Initialize with default structure if needed
                if "global" not in loaded_state:
                    loaded_state["global"] = {"current_personality": None, "default_persona": "discord_user"}
                
                # Ensure each guild state has required default keys
                for guild_id, guild_state in loaded_state.items():
                    if not isinstance(guild_state, dict):
                        loaded_state[guild_id] = {"current_personality": None}
                    # Ensure global state has default_persona
                    if guild_id == "global" and "default_persona" not in guild_state:
                        guild_state["default_persona"] = "discord_user"
                
                _app_state = loaded_state
                
        except (IOError, json.JSONDecodeError, ValueError) as e:
            print(f"Error loading app state from {STATE_FILE_PATH}: {e}. Using default state and attempting to save.")
            # Reset to known good default
            _app_state = {"global": {"current_personality": None, "default_persona": "discord_user"}}
            _save_state_to_file()
    else:
        print(f"State file {STATE_FILE_PATH} not found. Initializing with default state.")
        _save_state_to_file()

def _ensure_guild_state_exists(guild_id: str) -> None:
    """Ensure that a guild's state dictionary exists"""
    if guild_id not in _app_state:
        _app_state[guild_id] = {}

def _is_valid_guild(guild_id: str) -> bool:
    """Check if a guild ID is valid according to guild config"""
    guild_config = _load_guild_config()
    return guild_id == "global" or guild_id in guild_config

def get_state_value(key: str, guild_id: Optional[str] = None) -> Optional[Any]:
    """
    Retrieves a value from the app state.

    Args:
        key: The state key to retrieve
        guild_id: The guild ID. If None, uses global state.
                 If provided, checks guild state first, then falls back to global.

    Returns:
        The value from guild state, global state (if not found in guild), or None
    """
    # Reload state from disk to catch updates from other processes (e.g., trivia poster)
    _load_state_from_file()
    """
    # Default to global if no guild_id provided
    if guild_id is None:
        guild_id = "global"
    
    # Validate guild
    if not _is_valid_guild(guild_id):
        print(f"Warning: Unknown guild ID '{guild_id}', falling back to global state")
        guild_id = "global"
    
    # Check if guild state exists and has the key
    if guild_id in _app_state and key in _app_state[guild_id]:
        return _app_state[guild_id][key]
    
    # Fall back to global state if not found in guild state (and not already global)
    if guild_id != "global" and "global" in _app_state and key in _app_state["global"]:
        return _app_state["global"][key]
    
    return None

def set_state_value(key: str, value: Any, guild_id: Optional[str] = None) -> None:
    """
    Sets a value in the app state.
    
    Args:
        key: The state key to set
        value: The value to set
        guild_id: The guild ID. If None, uses global state.
                 Guilds cannot write to global state directly.
    
    Raises:
        ValueError: If trying to write to global state from a non-global guild
    """
    # Default to global if no guild_id provided
    if guild_id is None:
        guild_id = "global"
    
    # Validate guild
    if not _is_valid_guild(guild_id):
        raise ValueError(f"No app state configured for guild: {guild_id}")
    
    # Prevent non-global guilds from writing to global state
    if guild_id != "global":
        # Ensure the guild state exists
        _ensure_guild_state_exists(guild_id)
        _app_state[guild_id][key] = value
    else:
        # Only allow writing to global state if explicitly global
        _ensure_guild_state_exists("global")
        _app_state["global"][key] = value
    
    _save_state_to_file()  # Auto-save after changing state

def get_state_value_from_interaction(key: str, interaction_guild_id: Optional[int]) -> Optional[Any]:
    """
    Convenience function to get state value from a Discord interaction's guild_id.
    
    Args:
        key: The state key to retrieve
        interaction_guild_id: The guild_id from a Discord interaction (can be None for DMs)
    
    Returns:
        The value from appropriate guild state with global fallback
    """
    guild_id = _get_guild_id_from_interaction_guild_id(interaction_guild_id)
    return get_state_value(key, guild_id)

def set_state_value_from_interaction(key: str, value: Any, interaction_guild_id: Optional[int]) -> None:
    """
    Convenience function to set state value from a Discord interaction's guild_id.
    
    Args:
        key: The state key to set
        value: The value to set
        interaction_guild_id: The guild_id from a Discord interaction (can be None for DMs)
    """
    guild_id = _get_guild_id_from_interaction_guild_id(interaction_guild_id)
    set_state_value(key, value, guild_id)

def get_default_persona(interaction_guild_id: Optional[int] = None) -> str:
    """
    Gets the default persona for the specified guild, falling back to global default.
    
    Args:
        interaction_guild_id: The guild_id from a Discord interaction (can be None for DMs)
    
    Returns:
        The default persona key (defaults to "discord_user")
    """
    return get_state_value_from_interaction("default_persona", interaction_guild_id) or "discord_user"

def set_default_persona(persona_key: str, interaction_guild_id: Optional[int] = None) -> None:
    """
    Sets the default persona for the specified guild.
    
    Args:
        persona_key: The persona key to set as default
        interaction_guild_id: The guild_id from a Discord interaction (can be None for DMs)
    """
    set_state_value_from_interaction("default_persona", persona_key, interaction_guild_id)

def get_all_guild_states() -> Dict[str, Dict[str, Any]]:
    """
    Returns a copy of all guild states for read-only access.
    
    Returns:
        Dictionary mapping guild_id -> guild_state
    """
    return _app_state.copy()

# Load the state from file when the module is first imported
_load_state_from_file()
