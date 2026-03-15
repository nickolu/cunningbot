"""Pytest configuration for trivia tests."""

import sys
from unittest.mock import MagicMock

# Create mock discord module before any imports
mock_discord = MagicMock()
mock_discord.ext = MagicMock()
mock_discord.ext.commands = MagicMock()
mock_discord.ext.commands.Bot = MagicMock()
mock_discord.Interaction = MagicMock()
mock_discord.Embed = MagicMock()

# Add to sys.modules before importing the bot code
sys.modules['discord'] = mock_discord
sys.modules['discord.ext'] = mock_discord.ext
sys.modules['discord.ext.commands'] = mock_discord.ext.commands
