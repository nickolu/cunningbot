# bot/core/app_state.py
import json
import os
from typing import Any, Dict, Optional

# Define the path for the state file
STATE_FILE_PATH = os.path.join(os.path.dirname(__file__), "app_state.json")

# Initialize the application state (default)
_app_state: Dict[str, Any] = {
    "current_personality": None, # Retain for initial state setup
}

def _save_state_to_file() -> None:
    """Saves the current application state to the JSON file."""
    try:
        with open(STATE_FILE_PATH, 'w') as f:
            json.dump(_app_state, f, indent=4)
    except IOError as e:
        print(f"Error saving app state to {STATE_FILE_PATH}: {e}")
        # Optionally, add more robust error handling or logging

def _load_state_from_file() -> None:
    """Loads the application state from the JSON file if it exists."""
    global _app_state
    if os.path.exists(STATE_FILE_PATH):
        try:
            with open(STATE_FILE_PATH, 'r') as f:
                loaded_state = json.load(f)
                # Ensure default keys exist if not in loaded_state
                for key, default_value in _app_state.items():
                    if key not in loaded_state:
                        loaded_state[key] = default_value
                _app_state = loaded_state # Replace _app_state with loaded, ensuring defaults are present
        except (IOError, json.JSONDecodeError) as e:
            print(f"Error loading app state from {STATE_FILE_PATH}: {e}. Using default state and attempting to save.")
            # If loading fails, initialize with defaults and save
            _app_state = { "current_personality": None } # Reset to known good default
            _save_state_to_file()
    else:
        print(f"State file {STATE_FILE_PATH} not found. Initializing with default state.")
        # If the file doesn't exist, save the initial default state
        _save_state_to_file() 

def get_state_value(key: str) -> Optional[Any]:
    """Retrieves a generic value from the app state."""
    return _app_state.get(key)

def set_state_value(key: str, value: Any) -> None:
    """Sets a generic value in the app state and saves to file."""
    _app_state[key] = value
    _save_state_to_file() # Auto-save after changing state

# Load the state from file when the module is first imported
_load_state_from_file()
