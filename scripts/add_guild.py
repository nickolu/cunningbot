#!/usr/bin/env python3
"""
add_guild.py
Script to register a new Discord guild in both .guild_config.json and app_state.json

Usage:
    python3 scripts/add_guild.py <guild_id> <guild_name> [--no-restart]

Example:
    python3 scripts/add_guild.py 1383940238140772434 "News Test Server"
    python3 scripts/add_guild.py 1383940238140772434 "News Test Server" --no-restart

The script will:
1. Add the guild to .guild_config.json with default permissions
2. Add the guild to bot/app/app_state.json with empty state
3. Restart the bot container (unless --no-restart is specified)
"""

import json
import os
import sys
import subprocess

# Paths
GUILD_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".guild_config.json")
APP_STATE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "bot", "app", "app_state.json")

# Default permissions for new guilds
DEFAULT_GUILD_CONFIG = {
    "allowed_commands": ["chat", "image", "summarize", "personality:default"],
    "available_models": ["gpt-4o-mini", "gpt-4o"]
}


def load_json(file_path):
    """Load JSON file."""
    try:
        with open(file_path, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Error: File not found: {file_path}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in {file_path}: {e}")
        sys.exit(1)


def save_json(file_path, data):
    """Save JSON file with pretty formatting."""
    try:
        with open(file_path, 'w') as f:
            json.dump(data, f, indent=4)
        print(f"✓ Saved {file_path}")
    except IOError as e:
        print(f"Error: Could not write to {file_path}: {e}")
        sys.exit(1)


def add_to_guild_config(guild_id, guild_name):
    """Add guild to .guild_config.json."""
    config = load_json(GUILD_CONFIG_PATH)

    if guild_id in config:
        print(f"⚠ Guild {guild_id} already exists in .guild_config.json")
        print(f"  Current name: {config[guild_id].get('name', 'Unknown')}")
        response = input("  Overwrite? (y/n): ")
        if response.lower() != 'y':
            print("  Skipping .guild_config.json update")
            return False

    config[guild_id] = {
        "id": guild_id,
        "name": guild_name,
        **DEFAULT_GUILD_CONFIG
    }

    save_json(GUILD_CONFIG_PATH, config)
    print(f"✓ Added guild {guild_id} ({guild_name}) to .guild_config.json")
    return True


def add_to_app_state(guild_id):
    """Add guild to app_state.json."""
    state = load_json(APP_STATE_PATH)

    if guild_id in state:
        print(f"⚠ Guild {guild_id} already exists in app_state.json")
        if state[guild_id]:
            print(f"  Current state: {len(state[guild_id])} keys")
            response = input("  Overwrite with empty state? (y/n): ")
            if response.lower() != 'y':
                print("  Skipping app_state.json update")
                return False
        else:
            print("  State is already empty, updating anyway")

    state[guild_id] = {}

    save_json(APP_STATE_PATH, state)
    print(f"✓ Added guild {guild_id} to app_state.json")
    return True


def restart_bot():
    """Restart the bot container using docker-compose."""
    print("\nRestarting bot container...")
    try:
        # Check if docker-compose is available
        result = subprocess.run(
            ["docker-compose", "restart", "cunningbot"],
            cwd=os.path.dirname(os.path.dirname(__file__)),
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode == 0:
            print("✓ Bot container restarted successfully")
            return True
        else:
            print(f"⚠ Failed to restart bot: {result.stderr}")
            return False
    except FileNotFoundError:
        print("⚠ docker-compose not found - please restart manually:")
        print("  cd /path/to/cunningbot && docker-compose restart cunningbot")
        return False
    except subprocess.TimeoutExpired:
        print("⚠ Restart command timed out - please check container status")
        return False


def main():
    """Main entry point."""
    # Parse arguments
    if len(sys.argv) < 3:
        print("Usage: python3 scripts/add_guild.py <guild_id> <guild_name> [--no-restart]")
        print("\nExample:")
        print("  python3 scripts/add_guild.py 1383940238140772434 'News Test Server'")
        sys.exit(1)

    guild_id = sys.argv[1]
    guild_name = sys.argv[2]
    no_restart = "--no-restart" in sys.argv

    # Validate guild_id is numeric
    if not guild_id.isdigit():
        print(f"Error: Guild ID must be numeric, got: {guild_id}")
        sys.exit(1)

    print(f"Adding guild: {guild_id} ({guild_name})\n")

    # Add to both files
    config_updated = add_to_guild_config(guild_id, guild_name)
    state_updated = add_to_app_state(guild_id)

    if not config_updated and not state_updated:
        print("\n⚠ No changes made")
        sys.exit(0)

    print("\n✓ Guild registration complete!")

    # Restart bot
    if not no_restart:
        restart_bot()
    else:
        print("\n⚠ Skipping bot restart (--no-restart flag)")
        print("  Remember to restart manually:")
        print("  docker-compose restart cunningbot")

    print("\n" + "="*60)
    print(f"Guild {guild_id} is now registered!")
    print(f"You can now use bot commands in this server.")
    print("="*60)


if __name__ == "__main__":
    main()
