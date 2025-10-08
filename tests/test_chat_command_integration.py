"""Integration tests for /chat command"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, Mock, call
from typing import List
import discord

from bot.app.commands.chat.chat import ChatCog
from bot.domain.chat.chat_personas import CHAT_PERSONAS


class TestChatCommandQueueManagement:
    """Tests for chat command queue management"""

    @pytest.mark.asyncio
    @patch("bot.app.commands.chat.chat.get_task_queue")
    async def test_queues_task_when_queue_empty(self, mock_get_queue: Mock) -> None:
        """Test that task is queued when queue is empty"""
        mock_bot = MagicMock()
        cog = ChatCog(mock_bot)
        
        # Mock task queue
        mock_queue = MagicMock()
        mock_queue.get_queue_status.return_value = {"queue_size": 0}
        mock_queue.enqueue_task = AsyncMock(return_value="task-123")
        mock_get_queue.return_value = mock_queue
        
        mock_interaction = AsyncMock(spec=discord.Interaction)
        mock_interaction.user.id = 12345
        mock_interaction.response.is_done.return_value = False
        mock_interaction.response.defer = AsyncMock()
        
        # Call the callback directly
        await cog.chat.callback(cog, mock_interaction, msg="Hello, bot!")
        
        # Should defer immediately when queue is empty
        mock_interaction.response.defer.assert_called_once()
        
        # Should enqueue the task
        mock_queue.enqueue_task.assert_called_once()

    @pytest.mark.asyncio
    @patch("bot.app.commands.chat.chat.get_task_queue")
    async def test_shows_queue_position_when_queue_not_empty(self, mock_get_queue: Mock) -> None:
        """Test that user is informed of queue position"""
        mock_bot = MagicMock()
        cog = ChatCog(mock_bot)
        
        # Mock task queue with items
        mock_queue = MagicMock()
        mock_queue.get_queue_status.return_value = {"queue_size": 5}
        mock_queue.enqueue_task = AsyncMock(return_value="task-123")
        mock_get_queue.return_value = mock_queue
        
        mock_interaction = AsyncMock(spec=discord.Interaction)
        mock_interaction.user.id = 12345
        mock_interaction.response.is_done.return_value = False
        mock_interaction.response.send_message = AsyncMock()
        
        await cog.chat.callback(cog, mock_interaction, msg="Hello")
        
        # Should inform user of queue position
        mock_interaction.response.send_message.assert_called_once()
        call_args = mock_interaction.response.send_message.call_args
        message = call_args[0][0]
        assert "queued" in message.lower()
        assert "5" in message
        assert call_args[1]["ephemeral"] is True

    @pytest.mark.asyncio
    @patch("bot.app.commands.chat.chat.get_task_queue")
    async def test_handles_queue_full_error(self, mock_get_queue: Mock) -> None:
        """Test that queue full error is handled gracefully"""
        mock_bot = MagicMock()
        cog = ChatCog(mock_bot)
        
        # Mock task queue that raises error
        mock_queue = MagicMock()
        mock_queue.get_queue_status.return_value = {"queue_size": 0}
        mock_queue.enqueue_task = AsyncMock(side_effect=Exception("Queue is full"))
        mock_get_queue.return_value = mock_queue
        
        mock_interaction = AsyncMock(spec=discord.Interaction)
        mock_interaction.user.id = 12345
        mock_interaction.response.is_done.return_value = False
        mock_interaction.response.defer = AsyncMock()
        mock_interaction.response.send_message = AsyncMock()
        
        await cog.chat.callback(cog, mock_interaction, msg="Hello")
        
        # Should send error message about capacity
        assert mock_interaction.response.send_message.called
        call_args = mock_interaction.response.send_message.call_args
        message = call_args[0][0]
        assert "maximum capacity" in message.lower()


class TestChatHandlerPersonas:
    """Tests for persona handling in chat"""

    @pytest.mark.asyncio
    @patch("bot.app.commands.chat.chat.chat_service")
    @patch("bot.app.commands.chat.chat.get_default_persona")
    async def test_uses_default_persona_when_none_specified(
        self, 
        mock_get_default: Mock,
        mock_chat_service: AsyncMock
    ) -> None:
        """Test that default persona is used when none specified"""
        mock_bot = MagicMock()
        cog = ChatCog(mock_bot)
        
        # Mock default persona
        mock_get_default.return_value = "discord_user"
        mock_chat_service.return_value = "Hello! I'm a Discord user."
        
        mock_interaction = AsyncMock(spec=discord.Interaction)
        mock_interaction.user.id = 12345
        mock_interaction.user.display_name = "TestUser"
        mock_interaction.guild_id = 999
        mock_interaction.channel = AsyncMock(spec=discord.TextChannel)
        mock_interaction.channel.history.return_value.__aiter__.return_value = []
        mock_interaction.response = AsyncMock()
        mock_interaction.response.is_done = Mock(return_value=False)
        mock_interaction.response.defer = AsyncMock()
        mock_interaction.response.send_message = AsyncMock()
        mock_interaction.followup = AsyncMock()
        
        await cog._chat_handler(
            mock_interaction,
            msg="Hello",
            already_responded=False
        )
        
        # Should call get_default_persona
        mock_get_default.assert_called_once_with(999)
        
        # Should call chat_service with default persona's instructions
        mock_chat_service.assert_called_once()
        call_args = mock_chat_service.call_args
        personality_arg = call_args[0][3]  # 4th positional arg
        
        # Should have discord_user personality
        assert personality_arg == CHAT_PERSONAS["discord_user"]["instructions"]

    @pytest.mark.asyncio
    @patch("bot.app.commands.chat.chat.chat_service")
    @patch("bot.app.commands.chat.chat.get_default_persona")
    async def test_uses_selected_persona_over_default(
        self,
        mock_get_default: Mock,
        mock_chat_service: AsyncMock
    ) -> None:
        """Test that explicitly selected persona overrides default"""
        mock_bot = MagicMock()
        cog = ChatCog(mock_bot)
        
        # Mock default persona (should be ignored)
        mock_get_default.return_value = "discord_user"
        mock_chat_service.return_value = "Meow! Purr purr."
        
        mock_interaction = AsyncMock(spec=discord.Interaction)
        mock_interaction.user.id = 12345
        mock_interaction.user.display_name = "TestUser"
        mock_interaction.guild_id = 999
        mock_interaction.channel = AsyncMock(spec=discord.TextChannel)
        mock_interaction.channel.history.return_value.__aiter__.return_value = []
        mock_interaction.response = AsyncMock()
        mock_interaction.response.is_done = Mock(return_value=False)
        mock_interaction.response.defer = AsyncMock()
        mock_interaction.response.send_message = AsyncMock()
        mock_interaction.followup = AsyncMock()
        
        await cog._chat_handler(
            mock_interaction,
            msg="Hello",
            persona="cat",  # Explicitly select cat persona
            already_responded=False
        )
        
        # Should use cat persona instead of default
        mock_chat_service.assert_called_once()
        call_args = mock_chat_service.call_args
        personality_arg = call_args[0][3]
        
        # Should have cat personality
        assert personality_arg == CHAT_PERSONAS["cat"]["personality"]

    @pytest.mark.asyncio
    @patch("bot.app.commands.chat.chat.chat_service")
    @patch("bot.app.commands.chat.chat.get_default_persona")
    async def test_handles_invalid_persona_gracefully(
        self,
        mock_get_default: Mock,
        mock_chat_service: AsyncMock
    ) -> None:
        """Test that invalid persona falls back to default"""
        mock_bot = MagicMock()
        cog = ChatCog(mock_bot)
        
        mock_get_default.return_value = "discord_user"
        mock_chat_service.return_value = "Response"
        
        mock_interaction = AsyncMock(spec=discord.Interaction)
        mock_interaction.user.id = 12345
        mock_interaction.user.display_name = "TestUser"
        mock_interaction.guild_id = 999
        mock_interaction.channel = AsyncMock(spec=discord.TextChannel)
        mock_interaction.channel.history.return_value.__aiter__.return_value = []
        mock_interaction.response = AsyncMock()
        mock_interaction.response.is_done = Mock(return_value=False)
        mock_interaction.response.defer = AsyncMock()
        mock_interaction.response.send_message = AsyncMock()
        mock_interaction.followup = AsyncMock()
        
        await cog._chat_handler(
            mock_interaction,
            msg="Hello",
            persona="invalid_persona",  # Invalid persona
            already_responded=False
        )
        
        # Should fall back to default persona
        mock_get_default.assert_called_once()
        mock_chat_service.assert_called_once()


class TestChatHandlerMessageHistory:
    """Tests for message history retrieval"""

    @pytest.mark.asyncio
    @patch("bot.app.commands.chat.chat.chat_service")
    @patch("bot.app.commands.chat.chat.flatten_discord_message")
    async def test_retrieves_channel_history(
        self,
        mock_flatten: Mock,
        mock_chat_service: AsyncMock
    ) -> None:
        """Test that channel history is retrieved and processed"""
        mock_bot = MagicMock()
        cog = ChatCog(mock_bot)
        
        mock_chat_service.return_value = "Response"
        mock_flatten.side_effect = lambda msg: msg.content
        
        # Create mock messages
        mock_msg1 = MagicMock()
        mock_msg1.content = "User message 1"
        mock_msg1.author.display_name = "User1"
        mock_msg1.author.bot = False
        
        mock_msg2 = MagicMock()
        mock_msg2.content = "Bot response"
        mock_msg2.author.display_name = "Bot"
        mock_msg2.author.bot = True
        
        mock_msg3 = MagicMock()
        mock_msg3.content = "User message 2"
        mock_msg3.author.display_name = "User2"
        mock_msg3.author.bot = False
        
        # Setup mock channel with history
        mock_interaction = AsyncMock(spec=discord.Interaction)
        mock_interaction.user.id = 12345
        mock_interaction.user.display_name = "TestUser"
        mock_interaction.guild_id = 999
        mock_interaction.channel = AsyncMock(spec=discord.TextChannel)
        
        # Mock history generator
        async def mock_history(*args, **kwargs):
            for msg in [mock_msg3, mock_msg2, mock_msg1]:  # Newest first
                yield msg
        
        mock_interaction.channel.history.return_value = mock_history()
        mock_interaction.response = AsyncMock()
        mock_interaction.response.is_done.return_value = False
        mock_interaction.followup = AsyncMock()
        
        await cog._chat_handler(
            mock_interaction,
            msg="Current message",
            message_count=3,
            already_responded=False
        )
        
        # Should call chat_service with history
        mock_chat_service.assert_called_once()
        call_args = mock_chat_service.call_args
        history_arg = call_args[0][4]  # 5th positional arg
        
        # History should be oldest first
        assert len(history_arg) == 3
        assert history_arg[0]["content"] == "User message 1"
        assert history_arg[0]["role"] == "user"
        assert history_arg[1]["content"] == "Bot response"
        assert history_arg[1]["role"] == "assistant"
        assert history_arg[2]["content"] == "User message 2"
        assert history_arg[2]["role"] == "user"

    @pytest.mark.asyncio
    @patch("bot.app.commands.chat.chat.flatten_discord_message")
    @patch("bot.app.commands.chat.chat.get_default_persona")
    @patch("bot.app.commands.chat.chat.chat_service")
    async def test_respects_message_count_limit(
        self,
        mock_chat_service: AsyncMock,
        mock_get_default_persona: Mock,
        mock_flatten: Mock
    ) -> None:
        """Test that message_count parameter limits history"""
        mock_bot = MagicMock()
        cog = ChatCog(mock_bot)
        
        mock_chat_service.return_value = "Response"
        mock_get_default_persona.return_value = "discord_user"
        mock_flatten.side_effect = lambda msg: msg.content
        
        # Create many mock messages
        messages = []
        for i in range(50):
            msg = MagicMock()
            msg.content = f"Message {i}"
            msg.author.display_name = f"User{i}"
            msg.author.bot = False
            messages.append(msg)
        
        mock_interaction = AsyncMock(spec=discord.Interaction)
        mock_interaction.user.id = 12345
        mock_interaction.user.display_name = "TestUser"
        mock_interaction.guild_id = 999
        mock_interaction.channel = AsyncMock(spec=discord.TextChannel)
        
        # Mock history generator - create a proper async iterator
        async def mock_history_gen(limit, oldest_first):
            """Generator that yields messages up to the limit"""
            for msg in messages[:limit]:
                yield msg
        
        # Mock history method to return our async generator
        def mock_history_method(*args, **kwargs):
            limit = kwargs.get('limit', 20)
            oldest_first = kwargs.get('oldest_first', False)
            return mock_history_gen(limit, oldest_first)
        
        mock_interaction.channel.history = mock_history_method
        mock_interaction.response = AsyncMock()
        mock_interaction.response.is_done = Mock(return_value=False)
        mock_interaction.response.defer = AsyncMock()
        mock_interaction.response.send_message = AsyncMock()
        mock_interaction.followup = AsyncMock()
        
        await cog._chat_handler(
            mock_interaction,
            msg="Current",
            message_count=10,  # Limit to 10 messages
            already_responded=False
        )
        
        # Verify chat_service was called with limited history
        mock_chat_service.assert_called_once()
        call_args = mock_chat_service.call_args
        history_arg = call_args[0][4]
        assert len(history_arg) == 10


class TestChatHandlerResponseHandling:
    """Tests for response handling and splitting"""

    @pytest.mark.asyncio
    @patch("bot.app.commands.chat.chat.chat_service")
    @patch("bot.app.commands.chat.chat.split_message")
    async def test_sends_single_chunk_response(
        self,
        mock_split: Mock,
        mock_chat_service: AsyncMock
    ) -> None:
        """Test sending a short response that fits in one message"""
        mock_bot = MagicMock()
        cog = ChatCog(mock_bot)
        
        response_text = "This is a short response."
        mock_chat_service.return_value = response_text
        mock_split.return_value = [response_text]
        
        mock_interaction = AsyncMock(spec=discord.Interaction)
        mock_interaction.user.id = 12345
        mock_interaction.user.display_name = "TestUser"
        mock_interaction.user.mention = "<@12345>"
        mock_interaction.guild_id = 999
        mock_interaction.channel = AsyncMock(spec=discord.TextChannel)
        mock_interaction.channel.history.return_value.__aiter__.return_value = []
        mock_interaction.response = AsyncMock()
        mock_interaction.response.is_done = Mock(return_value=False)
        mock_interaction.response.defer = AsyncMock()
        mock_interaction.response.send_message = AsyncMock()
        mock_interaction.followup = AsyncMock()
        
        await cog._chat_handler(
            mock_interaction,
            msg="Hello",
            already_responded=False
        )
        
        # Should send message via response (not followup for first chunk)
        mock_interaction.response.send_message.assert_called_once()
        
        # Should not send any followup messages
        mock_interaction.followup.send.assert_not_called()

    @pytest.mark.asyncio
    @patch("bot.app.commands.chat.chat.chat_service")
    @patch("bot.app.commands.chat.chat.split_message")
    async def test_sends_multiple_chunks_for_long_response(
        self,
        mock_split: Mock,
        mock_chat_service: AsyncMock
    ) -> None:
        """Test sending a long response split into multiple chunks"""
        mock_bot = MagicMock()
        cog = ChatCog(mock_bot)
        
        mock_chat_service.return_value = "A" * 5000  # Long response
        chunks = ["A" * 1900, "A" * 1900, "A" * 1200]
        mock_split.return_value = chunks
        
        mock_interaction = AsyncMock(spec=discord.Interaction)
        mock_interaction.user.id = 12345
        mock_interaction.user.display_name = "TestUser"
        mock_interaction.user.mention = "<@12345>"
        mock_interaction.guild_id = 999
        mock_interaction.channel = AsyncMock(spec=discord.TextChannel)
        mock_interaction.channel.history.return_value.__aiter__.return_value = []
        mock_interaction.response = AsyncMock()
        mock_interaction.response.is_done = Mock(return_value=False)
        mock_interaction.response.defer = AsyncMock()
        mock_interaction.response.send_message = AsyncMock()
        mock_interaction.followup = AsyncMock()
        
        await cog._chat_handler(
            mock_interaction,
            msg="Tell me a long story",
            already_responded=False
        )
        
        # First chunk via response
        mock_interaction.response.send_message.assert_called_once()
        
        # Remaining chunks via followup
        assert mock_interaction.followup.send.call_count == 2

    @pytest.mark.asyncio
    @patch("bot.app.commands.chat.chat.chat_service")
    async def test_private_flag_makes_response_ephemeral(
        self,
        mock_chat_service: AsyncMock
    ) -> None:
        """Test that private flag makes responses ephemeral"""
        mock_bot = MagicMock()
        cog = ChatCog(mock_bot)
        
        mock_chat_service.return_value = "Private response"
        
        mock_interaction = AsyncMock(spec=discord.Interaction)
        mock_interaction.user.id = 12345
        mock_interaction.user.display_name = "TestUser"
        mock_interaction.user.mention = "<@12345>"
        mock_interaction.guild_id = 999
        mock_interaction.channel = AsyncMock(spec=discord.TextChannel)
        mock_interaction.channel.history.return_value.__aiter__.return_value = []
        mock_interaction.response = AsyncMock()
        mock_interaction.response.is_done = Mock(return_value=False)
        mock_interaction.response.defer = AsyncMock()
        mock_interaction.response.send_message = AsyncMock()
        mock_interaction.followup = AsyncMock()
        
        await cog._chat_handler(
            mock_interaction,
            msg="Hello",
            private=1,  # Private mode
            already_responded=False
        )
        
        # Should send ephemeral message
        call_args = mock_interaction.response.send_message.call_args
        assert call_args[1]["ephemeral"] is True


class TestChatHandlerModelSelection:
    """Tests for model selection"""

    @pytest.mark.asyncio
    @patch("bot.app.commands.chat.chat.chat_service")
    async def test_uses_default_model_when_none_specified(
        self,
        mock_chat_service: AsyncMock
    ) -> None:
        """Test that default model is used when none specified"""
        mock_bot = MagicMock()
        cog = ChatCog(mock_bot)
        
        mock_chat_service.return_value = "Response"
        
        mock_interaction = AsyncMock(spec=discord.Interaction)
        mock_interaction.user.id = 12345
        mock_interaction.user.display_name = "TestUser"
        mock_interaction.guild_id = 999
        mock_interaction.channel = AsyncMock(spec=discord.TextChannel)
        mock_interaction.channel.history.return_value.__aiter__.return_value = []
        mock_interaction.response = AsyncMock()
        mock_interaction.response.is_done = Mock(return_value=False)
        mock_interaction.response.defer = AsyncMock()
        mock_interaction.response.send_message = AsyncMock()
        mock_interaction.followup = AsyncMock()
        
        await cog._chat_handler(
            mock_interaction,
            msg="Hello",
            model=None,  # No model specified
            already_responded=False
        )
        
        # Should call chat_service with default model
        mock_chat_service.assert_called_once()
        call_args = mock_chat_service.call_args
        model_arg = call_args[0][1]  # 2nd positional arg
        assert model_arg == "gpt-4o-mini"

    @pytest.mark.asyncio
    @patch("bot.app.commands.chat.chat.chat_service")
    async def test_uses_specified_model(
        self,
        mock_chat_service: AsyncMock
    ) -> None:
        """Test that specified model is used"""
        mock_bot = MagicMock()
        cog = ChatCog(mock_bot)
        
        mock_chat_service.return_value = "Response"
        
        mock_interaction = AsyncMock(spec=discord.Interaction)
        mock_interaction.user.id = 12345
        mock_interaction.user.display_name = "TestUser"
        mock_interaction.guild_id = 999
        mock_interaction.channel = AsyncMock(spec=discord.TextChannel)
        mock_interaction.channel.history.return_value.__aiter__.return_value = []
        mock_interaction.response = AsyncMock()
        mock_interaction.response.is_done = Mock(return_value=False)
        mock_interaction.response.defer = AsyncMock()
        mock_interaction.response.send_message = AsyncMock()
        mock_interaction.followup = AsyncMock()
        
        await cog._chat_handler(
            mock_interaction,
            msg="Hello",
            model="gpt-4o",  # Specific model
            already_responded=False
        )
        
        # Should call chat_service with specified model
        mock_chat_service.assert_called_once()
        call_args = mock_chat_service.call_args
        model_arg = call_args[0][1]
        assert model_arg == "gpt-4o"


class TestChatHandlerErrorHandling:
    """Tests for error handling"""

    @pytest.mark.asyncio
    @patch("bot.app.commands.chat.chat.chat_service")
    async def test_handles_chat_service_exception(
        self,
        mock_chat_service: AsyncMock
    ) -> None:
        """Test that exceptions from chat service are handled"""
        mock_bot = MagicMock()
        cog = ChatCog(mock_bot)
        
        # Simulate chat service error
        mock_chat_service.side_effect = Exception("API Error")
        
        mock_interaction = AsyncMock(spec=discord.Interaction)
        mock_interaction.user.id = 12345
        mock_interaction.user.display_name = "TestUser"
        mock_interaction.guild_id = 999
        mock_interaction.channel = AsyncMock(spec=discord.TextChannel)
        mock_interaction.channel.history.return_value.__aiter__.return_value = []
        mock_interaction.response = AsyncMock()
        mock_interaction.response.is_done = Mock(return_value=False)
        mock_interaction.response.defer = AsyncMock()
        mock_interaction.response.send_message = AsyncMock()
        mock_interaction.followup = AsyncMock()
        
        await cog._chat_handler(
            mock_interaction,
            msg="Hello",
            already_responded=False
        )
        
        # Should send error message
        mock_interaction.response.send_message.assert_called()
        call_args = mock_interaction.response.send_message.call_args
        message = call_args[0][0]
        assert "error" in message.lower()
        assert call_args[1]["ephemeral"] is True

    @pytest.mark.asyncio
    @patch("bot.app.commands.chat.chat.chat_service")
    async def test_handles_discord_api_error_gracefully(
        self,
        mock_chat_service: AsyncMock
    ) -> None:
        """Test that Discord API errors are handled gracefully"""
        mock_bot = MagicMock()
        cog = ChatCog(mock_bot)
        
        mock_chat_service.return_value = "Response"
        
        mock_interaction = AsyncMock(spec=discord.Interaction)
        mock_interaction.user.id = 12345
        mock_interaction.user.display_name = "TestUser"
        mock_interaction.user.mention = "<@12345>"
        mock_interaction.guild_id = 999
        mock_interaction.channel = AsyncMock(spec=discord.TextChannel)
        mock_interaction.channel.history.return_value.__aiter__.return_value = []
        mock_interaction.response = AsyncMock()
        mock_interaction.response.is_done.return_value = False
        
        # Simulate Discord API error when sending response
        mock_interaction.response.send_message.side_effect = discord.errors.NotFound(
            MagicMock(), "Interaction not found"
        )
        
        # Should not raise exception
        await cog._chat_handler(
            mock_interaction,
            msg="Hello",
            already_responded=False
        )
        
        # Should attempt to send to channel as fallback
        mock_interaction.channel.send.assert_called()


class TestChatCommandInitialization:
    """Tests for ChatCog initialization"""

    def test_initializes_llm_client(self) -> None:
        """Test that LLM client is initialized on cog creation"""
        mock_bot = MagicMock()
        
        cog = ChatCog(mock_bot)
        
        # Should have LLM client
        assert cog.llm is not None
        assert hasattr(cog, 'bot')
        assert cog.bot is mock_bot

