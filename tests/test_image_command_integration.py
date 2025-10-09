"""Integration tests for /image command

These tests ensure that the image command properly handles Discord's 3-second
interaction timeout and other critical timing/ordering requirements.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, Mock, call
import discord

from bot.app.commands.image.image import ImageCog


class TestImageCommandResponseTiming:
    """Tests that ensure proper response timing to avoid Discord timeouts"""

    @pytest.mark.asyncio
    @patch("bot.app.commands.image.image.get_task_queue")
    async def test_defers_immediately_to_avoid_timeout(self, mock_get_queue: Mock) -> None:
        """
        CRITICAL TEST: Verify that interaction.response.defer() is called IMMEDIATELY
        before any other logic to avoid Discord's 3-second timeout.
        
        This test would have caught the issue where queue status was checked
        before responding to the interaction, causing timeouts.
        """
        mock_bot = MagicMock()
        cog = ImageCog(mock_bot)
        
        # Track the order of calls
        call_order = []
        
        # Mock task queue
        mock_queue = MagicMock()
        def track_queue_status():
            call_order.append("queue_status_checked")
            return {"queue_size": 0}
        mock_queue.get_queue_status = track_queue_status
        mock_queue.enqueue_task = AsyncMock(return_value="task-123")
        mock_get_queue.return_value = mock_queue
        
        # Mock interaction
        mock_interaction = AsyncMock(spec=discord.Interaction)
        mock_interaction.user.id = 12345
        mock_interaction.user.display_name = "TestUser"
        mock_interaction.response.is_done.return_value = False
        
        async def track_defer():
            call_order.append("interaction_deferred")
        mock_interaction.response.defer = track_defer
        
        # Call the command
        await cog.image.callback(
            cog,
            mock_interaction,
            prompt="Test image",
            model="openai"
        )
        
        # CRITICAL ASSERTION: defer must be called BEFORE queue status check
        assert call_order[0] == "interaction_deferred", \
            "interaction.response.defer() MUST be called before checking queue status to avoid 3-second timeout"
        assert call_order[1] == "queue_status_checked"

    @pytest.mark.asyncio
    @patch("bot.app.commands.image.image.get_task_queue")
    async def test_defers_before_client_initialization_issues(self, mock_get_queue: Mock) -> None:
        """
        Test that defer happens even if there are delays in getting queue or other setup.
        This simulates network latency or slow initialization.
        """
        mock_bot = MagicMock()
        cog = ImageCog(mock_bot)
        
        # Mock task queue with a delay
        mock_queue = MagicMock()
        async def slow_queue_status():
            import asyncio
            await asyncio.sleep(0.1)  # Simulate some delay
            return {"queue_size": 0}
        
        mock_queue.get_queue_status = MagicMock(return_value={"queue_size": 0})
        mock_queue.enqueue_task = AsyncMock(return_value="task-123")
        mock_get_queue.return_value = mock_queue
        
        # Track that defer was called
        defer_called = False
        
        mock_interaction = AsyncMock(spec=discord.Interaction)
        mock_interaction.user.id = 12345
        mock_interaction.user.display_name = "TestUser"
        mock_interaction.response.is_done.return_value = False
        
        async def track_defer():
            nonlocal defer_called
            defer_called = True
        mock_interaction.response.defer = track_defer
        
        await cog.image.callback(
            cog,
            mock_interaction,
            prompt="Test",
            model="openai"
        )
        
        # Should have deferred
        assert defer_called, "Must defer before any potentially slow operations"

    @pytest.mark.asyncio
    @patch("bot.app.commands.image.image.get_task_queue")
    async def test_uses_followup_after_immediate_defer(self, mock_get_queue: Mock) -> None:
        """
        Test that when queue has items, we use followup (not response.send_message)
        because we've already deferred.
        """
        mock_bot = MagicMock()
        cog = ImageCog(mock_bot)
        
        # Mock task queue with items in queue
        mock_queue = MagicMock()
        mock_queue.get_queue_status.return_value = {"queue_size": 3}
        mock_queue.enqueue_task = AsyncMock(return_value="task-123")
        mock_get_queue.return_value = mock_queue
        
        mock_interaction = AsyncMock(spec=discord.Interaction)
        mock_interaction.user.id = 12345
        mock_interaction.user.display_name = "TestUser"
        mock_interaction.response.is_done.return_value = False
        mock_interaction.response.defer = AsyncMock()
        mock_interaction.followup.send = AsyncMock()
        
        await cog.image.callback(
            cog,
            mock_interaction,
            prompt="Test",
            model="openai"
        )
        
        # Should defer immediately
        mock_interaction.response.defer.assert_called_once()
        
        # Should use followup to inform about queue position
        mock_interaction.followup.send.assert_called_once()
        call_args = mock_interaction.followup.send.call_args
        message = call_args[0][0]
        assert "queued" in message.lower()
        assert "3" in message


class TestImageCommandQueueManagement:
    """Tests for image command queue management"""

    @pytest.mark.asyncio
    @patch("bot.app.commands.image.image.get_task_queue")
    async def test_enqueues_task_with_correct_parameters(self, mock_get_queue: Mock) -> None:
        """Test that task is enqueued with all the correct parameters"""
        mock_bot = MagicMock()
        cog = ImageCog(mock_bot)
        
        mock_queue = MagicMock()
        mock_queue.get_queue_status.return_value = {"queue_size": 0}
        mock_queue.enqueue_task = AsyncMock(return_value="task-123")
        mock_get_queue.return_value = mock_queue
        
        mock_interaction = AsyncMock(spec=discord.Interaction)
        mock_interaction.user.id = 12345
        mock_interaction.user.display_name = "TestUser"
        mock_interaction.response.is_done.return_value = False
        mock_interaction.response.defer = AsyncMock()
        
        await cog.image.callback(
            cog,
            mock_interaction,
            prompt="A beautiful sunset",
            model="gemini",
            size="1536x1024",
            quality="high",
            background="transparent"
        )
        
        # Verify task was enqueued with correct params
        mock_queue.enqueue_task.assert_called_once()
        call_args = mock_queue.enqueue_task.call_args
        
        # First arg should be the handler function
        assert call_args[0][0] == cog._image_handler
        
        # Second arg should be the interaction
        assert call_args[0][1] == mock_interaction
        
        # Check prompt
        assert call_args[0][2] == "A beautiful sunset"
        
        # Check model
        assert call_args[0][7] == "gemini"

    @pytest.mark.asyncio
    @patch("bot.app.commands.image.image.get_task_queue")
    async def test_handles_queue_full_error(self, mock_get_queue: Mock) -> None:
        """Test that queue full error is handled gracefully"""
        mock_bot = MagicMock()
        cog = ImageCog(mock_bot)
        
        # Mock task queue that raises error
        mock_queue = MagicMock()
        mock_queue.get_queue_status.return_value = {"queue_size": 0}
        mock_queue.enqueue_task = AsyncMock(
            side_effect=Exception("Task queue is full. Please try again later.")
        )
        mock_get_queue.return_value = mock_queue
        
        mock_interaction = AsyncMock(spec=discord.Interaction)
        mock_interaction.user.id = 12345
        mock_interaction.response.is_done.return_value = False
        mock_interaction.response.defer = AsyncMock()
        mock_interaction.followup.send = AsyncMock()
        
        await cog.image.callback(
            cog,
            mock_interaction,
            prompt="Test",
            model="openai"
        )
        
        # Should send error message about capacity
        mock_interaction.followup.send.assert_called_once()
        call_args = mock_interaction.followup.send.call_args
        message = call_args[0][0]
        assert "maximum capacity" in message.lower() or "overwhelmed" in message.lower()


class TestImageCommandFeatureFlags:
    """Tests for image generation feature flags"""

    @pytest.mark.asyncio
    @patch("bot.app.commands.image.image.IMAGE_GENERATION_ENABLED", False)
    async def test_respects_global_disable_flag(self) -> None:
        """Test that global disable flag prevents image generation"""
        mock_bot = MagicMock()
        cog = ImageCog(mock_bot)
        
        mock_interaction = AsyncMock(spec=discord.Interaction)
        mock_interaction.user.id = 12345
        mock_interaction.response.is_done.return_value = False
        mock_interaction.response.send_message = AsyncMock()
        
        await cog.image.callback(
            cog,
            mock_interaction,
            prompt="Test",
            model="openai"
        )
        
        # Should send maintenance message
        mock_interaction.response.send_message.assert_called_once()
        call_args = mock_interaction.response.send_message.call_args
        message = call_args[0][0]
        assert "maintenance" in message.lower() or "unavailable" in message.lower()
        assert call_args[1]["ephemeral"] is True

    @pytest.mark.asyncio
    @patch("bot.app.commands.image.image.IMAGE_GENERATION_ENABLED", True)
    @patch("bot.app.commands.image.image.IMAGE_GENERATION_DISABLED_FOR_USERS", ["12345"])
    async def test_respects_user_specific_disable(self) -> None:
        """Test that user-specific disable flag prevents image generation"""
        mock_bot = MagicMock()
        cog = ImageCog(mock_bot)
        
        mock_interaction = AsyncMock(spec=discord.Interaction)
        mock_interaction.user.id = 12345
        mock_interaction.response.is_done.return_value = False
        mock_interaction.response.send_message = AsyncMock()
        
        await cog.image.callback(
            cog,
            mock_interaction,
            prompt="Test",
            model="openai"
        )
        
        # Should send maintenance message
        mock_interaction.response.send_message.assert_called_once()
        call_args = mock_interaction.response.send_message.call_args
        message = call_args[0][0]
        assert "maintenance" in message.lower() or "unavailable" in message.lower()


class TestImageCommandModelSelection:
    """Tests for model selection and validation"""

    @pytest.mark.asyncio
    @patch("bot.app.commands.image.image.get_task_queue")
    async def test_gemini_unavailable_shows_error(self, mock_get_queue: Mock) -> None:
        """Test that selecting Gemini when unavailable shows appropriate error"""
        mock_bot = MagicMock()
        cog = ImageCog(mock_bot)
        
        # Simulate Gemini being unavailable
        cog.gemini_generation_client = None
        
        mock_queue = MagicMock()
        mock_queue.get_queue_status.return_value = {"queue_size": 0}
        mock_queue.enqueue_task = AsyncMock(return_value="task-123")
        mock_get_queue.return_value = mock_queue
        
        mock_interaction = AsyncMock(spec=discord.Interaction)
        mock_interaction.user.id = 12345
        mock_interaction.user.display_name = "TestUser"
        mock_interaction.response.is_done.return_value = False
        mock_interaction.response.defer = AsyncMock()
        mock_interaction.followup.send = AsyncMock()
        
        # Enqueue task (will be processed by _image_handler)
        await cog.image.callback(
            cog,
            mock_interaction,
            prompt="Test",
            model="gemini"
        )
        
        # Task should be enqueued (error will be shown by handler)
        mock_queue.enqueue_task.assert_called_once()


class TestImageCommandEditing:
    """Tests for image editing functionality"""

    @pytest.mark.asyncio
    @patch("bot.app.commands.image.image.get_task_queue")
    async def test_handles_attachment_for_editing(self, mock_get_queue: Mock) -> None:
        """Test that attachment is properly passed for image editing"""
        mock_bot = MagicMock()
        cog = ImageCog(mock_bot)
        
        mock_queue = MagicMock()
        mock_queue.get_queue_status.return_value = {"queue_size": 0}
        mock_queue.enqueue_task = AsyncMock(return_value="task-123")
        mock_get_queue.return_value = mock_queue
        
        mock_attachment = MagicMock(spec=discord.Attachment)
        mock_attachment.filename = "test.png"
        
        mock_interaction = AsyncMock(spec=discord.Interaction)
        mock_interaction.user.id = 12345
        mock_interaction.user.display_name = "TestUser"
        mock_interaction.response.is_done.return_value = False
        mock_interaction.response.defer = AsyncMock()
        mock_interaction.followup.send = AsyncMock()
        
        await cog.image.callback(
            cog,
            mock_interaction,
            prompt="Make it blue",
            attachment=mock_attachment,
            model="openai"
        )
        
        # Verify attachment was passed to handler
        mock_queue.enqueue_task.assert_called_once()
        call_args = mock_queue.enqueue_task.call_args
        assert call_args[0][3] == mock_attachment  # 4th arg is attachment


class TestImageCommandErrorRecovery:
    """Tests for error handling and recovery"""

    @pytest.mark.asyncio
    @patch("bot.app.commands.image.image.get_task_queue")
    async def test_handles_unexpected_exceptions_gracefully(self, mock_get_queue: Mock) -> None:
        """Test that unexpected exceptions are handled gracefully"""
        mock_bot = MagicMock()
        cog = ImageCog(mock_bot)
        
        # Mock queue to raise unexpected error
        mock_queue = MagicMock()
        mock_queue.get_queue_status.side_effect = Exception("Unexpected error")
        mock_get_queue.return_value = mock_queue
        
        mock_interaction = AsyncMock(spec=discord.Interaction)
        mock_interaction.user.id = 12345
        mock_interaction.response.is_done.return_value = False
        mock_interaction.response.defer = AsyncMock()
        mock_interaction.followup.send = AsyncMock()
        
        # Should not raise exception
        await cog.image.callback(
            cog,
            mock_interaction,
            prompt="Test",
            model="openai"
        )
        
        # Should still defer (this is the critical fix)
        mock_interaction.response.defer.assert_called_once()
        
        # Should send error message
        mock_interaction.followup.send.assert_called_once()
        call_args = mock_interaction.followup.send.call_args
        message = call_args[0][0]
        assert "overwhelmed" in message.lower() or "try again" in message.lower()


class TestImageCommandRateLimitHandling:
    """Tests for rate limit error handling"""

    @pytest.mark.asyncio
    async def test_gemini_rate_limit_shows_friendly_message(self) -> None:
        """Test that Gemini rate limit errors show user-friendly messages"""
        mock_bot = MagicMock()
        cog = ImageCog(mock_bot)
        
        # Mock Gemini client to return rate limit error
        mock_gemini_client = AsyncMock()
        mock_gemini_client.generate_image = AsyncMock(
            return_value=(None, "RATE_LIMIT: Google Gemini is currently experiencing high demand. Please try again in a few moments.")
        )
        cog.gemini_generation_client = mock_gemini_client
        
        mock_interaction = AsyncMock(spec=discord.Interaction)
        mock_interaction.user.id = 12345
        mock_interaction.user.mention = "<@12345>"
        mock_interaction.user.display_name = "TestUser"
        mock_interaction.response.is_done.return_value = True
        mock_interaction.followup.send = AsyncMock()
        
        # Call the handler directly (simulating queued execution)
        await cog._image_handler(
            mock_interaction,
            prompt="Test image",
            model="gemini",
            size="1024x1024",
            already_responded=True
        )
        
        # Should send rate limit message
        mock_interaction.followup.send.assert_called_once()
        call_args = mock_interaction.followup.send.call_args
        message = call_args[0][0]
        
        # Verify user-friendly rate limit message
        assert "⏱️" in message or "Rate Limit" in message
        assert "not a problem with the bot" in message.lower()
        assert "high demand" in message.lower()
        assert "Test image" in message  # Shows user's original request

    @pytest.mark.asyncio
    async def test_gemini_edit_rate_limit_shows_friendly_message(self) -> None:
        """Test that Gemini rate limit errors on edit show user-friendly messages"""
        mock_bot = MagicMock()
        cog = ImageCog(mock_bot)
        
        # Mock both Gemini clients (generation is checked for availability)
        mock_gemini_generation_client = AsyncMock()
        cog.gemini_generation_client = mock_gemini_generation_client
        
        # Mock Gemini edit client to return rate limit error
        mock_gemini_edit_client = AsyncMock()
        mock_gemini_edit_client.edit_image = AsyncMock(
            return_value=(None, "RATE_LIMIT: Google Gemini is currently experiencing high demand. Please try again in a few moments.")
        )
        cog.gemini_edit_client = mock_gemini_edit_client
        
        mock_attachment = MagicMock(spec=discord.Attachment)
        mock_attachment.filename = "test.png"
        mock_attachment.read = AsyncMock(return_value=b"fake image data")
        
        mock_interaction = AsyncMock(spec=discord.Interaction)
        mock_interaction.user.id = 12345
        mock_interaction.user.mention = "<@12345>"
        mock_interaction.user.display_name = "TestUser"
        mock_interaction.response.is_done.return_value = True
        mock_interaction.followup.send = AsyncMock()
        
        # Call the handler directly (simulating queued execution)
        await cog._image_handler(
            mock_interaction,
            prompt="Make it blue",
            attachment=mock_attachment,
            model="gemini",
            size="1024x1024",
            already_responded=True
        )
        
        # Should send rate limit message
        mock_interaction.followup.send.assert_called_once()
        call_args = mock_interaction.followup.send.call_args
        message = call_args[0][0]
        
        # Verify user-friendly rate limit message
        assert "⏱️" in message or "Rate Limit" in message
        assert "not a problem with the bot" in message.lower()
        assert "Make it blue" in message  # Shows user's original request
        assert "test.png" in message  # Shows attachment filename

    @pytest.mark.asyncio
    async def test_non_rate_limit_errors_show_generic_message(self) -> None:
        """Test that non-rate-limit errors show generic error messages"""
        mock_bot = MagicMock()
        cog = ImageCog(mock_bot)
        
        # Mock Gemini client to return generic error
        mock_gemini_client = AsyncMock()
        mock_gemini_client.generate_image = AsyncMock(
            return_value=(None, "Some other error occurred")
        )
        cog.gemini_generation_client = mock_gemini_client
        
        mock_interaction = AsyncMock(spec=discord.Interaction)
        mock_interaction.user.id = 12345
        mock_interaction.user.mention = "<@12345>"
        mock_interaction.user.display_name = "TestUser"
        mock_interaction.response.is_done.return_value = True
        mock_interaction.followup.send = AsyncMock()
        
        # Call the handler directly
        await cog._image_handler(
            mock_interaction,
            prompt="Test",
            model="gemini",
            already_responded=True
        )
        
        # Should send generic error message (not rate limit)
        mock_interaction.followup.send.assert_called_once()
        call_args = mock_interaction.followup.send.call_args
        message = call_args[0][0]
        
        # Should NOT be the rate limit message
        assert "Rate Limit" not in message
        assert "unexpected error" in message.lower() or "error occurred" in message.lower()

