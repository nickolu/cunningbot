"""Unit tests for Google Gemini image generation and editing clients"""

import pytest
from unittest.mock import AsyncMock, MagicMock, Mock, patch, mock_open
from io import BytesIO
from types import SimpleNamespace

from bot.api.google.image_generation_client import GeminiImageGenerationClient, SIZE_TO_ASPECT_RATIO
from bot.api.google.image_edit_client import GeminiImageEditClient


class TestGeminiImageGenerationClient:
    """Tests for Google Gemini ImageGenerationClient"""

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-google-key-123"})
    @patch("bot.api.google.image_generation_client.genai")
    def test_initialization_with_api_key(self, mock_genai: Mock) -> None:
        """Test that client initializes successfully with API key"""
        client = GeminiImageGenerationClient()
        
        assert client.api_key == "test-google-key-123"
        assert client.model == "gemini-2.5-flash-image"
        mock_genai.Client.assert_called_once_with(api_key="test-google-key-123")

    @patch.dict("os.environ", {}, clear=True)
    def test_initialization_without_api_key_raises_error(self) -> None:
        """Test that missing API key raises EnvironmentError"""
        with pytest.raises(EnvironmentError, match="GOOGLE_API_KEY"):
            GeminiImageGenerationClient()

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    @patch("bot.api.google.image_generation_client.genai")
    def test_factory_method_creates_instance(self, mock_genai: Mock) -> None:
        """Test factory method returns correct instance"""
        client = GeminiImageGenerationClient.factory()
        
        assert isinstance(client, GeminiImageGenerationClient)
        assert client.model == "gemini-2.5-flash-image"

    @pytest.mark.asyncio
    async def test_generate_image_success(self) -> None:
        """Test successful image generation"""
        with patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"}), \
             patch("bot.api.google.image_generation_client.genai") as mock_genai:
            
            # Create mock response
            fake_image_bytes = b"fake_png_data_from_gemini"
            mock_part = SimpleNamespace(
                inline_data=SimpleNamespace(data=fake_image_bytes)
            )
            mock_response = SimpleNamespace(
                candidates=[
                    SimpleNamespace(
                        content=SimpleNamespace(parts=[mock_part])
                    )
                ]
            )
            
            mock_client_instance = Mock()
            mock_client_instance.models.generate_content.return_value = mock_response
            mock_genai.Client.return_value = mock_client_instance
            
            client = GeminiImageGenerationClient()
            result_bytes, error_msg = await client.generate_image("a beautiful sunset")
            
            # Verify result
            assert result_bytes == fake_image_bytes
            assert error_msg == ""
            
            # Verify API was called correctly
            mock_client_instance.models.generate_content.assert_called_once()
            call_args = mock_client_instance.models.generate_content.call_args
            assert call_args[1]["model"] == "gemini-2.5-flash-image"
            assert call_args[1]["contents"] == ["a beautiful sunset"]

    @pytest.mark.asyncio
    async def test_generate_image_with_size_conversion(self) -> None:
        """Test that OpenAI-style size is converted to aspect ratio"""
        with patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"}), \
             patch("bot.api.google.image_generation_client.genai") as mock_genai:
            
            fake_image_bytes = b"image"
            mock_part = SimpleNamespace(inline_data=SimpleNamespace(data=fake_image_bytes))
            mock_response = SimpleNamespace(
                candidates=[SimpleNamespace(content=SimpleNamespace(parts=[mock_part]))]
            )
            
            mock_client_instance = Mock()
            mock_client_instance.models.generate_content.return_value = mock_response
            mock_genai.Client.return_value = mock_client_instance
            mock_genai.types = MagicMock()
            
            client = GeminiImageGenerationClient()
            await client.generate_image("test", size="1536x1024")
            
            # Verify aspect ratio was set correctly (1536x1024 -> 3:2)
            call_args = mock_client_instance.models.generate_content.call_args
            config = call_args[1]["config"]
            # Config should have aspect_ratio set to 3:2
            assert config.image_config.aspect_ratio == "3:2"

    @pytest.mark.asyncio
    async def test_generate_image_with_direct_aspect_ratio(self) -> None:
        """Test that direct aspect ratio parameter is used"""
        with patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"}), \
             patch("bot.api.google.image_generation_client.genai") as mock_genai:
            
            fake_image_bytes = b"image"
            mock_part = SimpleNamespace(inline_data=SimpleNamespace(data=fake_image_bytes))
            mock_response = SimpleNamespace(
                candidates=[SimpleNamespace(content=SimpleNamespace(parts=[mock_part]))]
            )
            
            mock_client_instance = Mock()
            mock_client_instance.models.generate_content.return_value = mock_response
            mock_genai.Client.return_value = mock_client_instance
            mock_genai.types = MagicMock()
            
            client = GeminiImageGenerationClient()
            await client.generate_image("test", aspect_ratio="16:9")
            
            # Verify aspect ratio was set correctly
            call_args = mock_client_instance.models.generate_content.call_args
            config = call_args[1]["config"]
            assert config.image_config.aspect_ratio == "16:9"

    @pytest.mark.asyncio
    async def test_generate_image_no_candidates(self) -> None:
        """Test handling when API returns no candidates"""
        with patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"}), \
             patch("bot.api.google.image_generation_client.genai") as mock_genai:
            
            mock_response = SimpleNamespace(candidates=[])
            
            mock_client_instance = Mock()
            mock_client_instance.models.generate_content.return_value = mock_response
            mock_genai.Client.return_value = mock_client_instance
            
            client = GeminiImageGenerationClient()
            result_bytes, error_msg = await client.generate_image("test")
            
            assert result_bytes is None
            assert "No parts returned" in error_msg

    @pytest.mark.asyncio
    async def test_generate_image_no_image_parts(self) -> None:
        """Test handling when response has no image parts"""
        with patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"}), \
             patch("bot.api.google.image_generation_client.genai") as mock_genai:
            
            # Mock response with text part but no image part
            mock_text_part = SimpleNamespace(inline_data=None)
            mock_response = SimpleNamespace(
                candidates=[SimpleNamespace(content=SimpleNamespace(parts=[mock_text_part]))]
            )
            
            mock_client_instance = Mock()
            mock_client_instance.models.generate_content.return_value = mock_response
            mock_genai.Client.return_value = mock_client_instance
            
            client = GeminiImageGenerationClient()
            result_bytes, error_msg = await client.generate_image("test")
            
            assert result_bytes is None
            assert "No image data" in error_msg

    @pytest.mark.asyncio
    async def test_generate_image_api_exception(self) -> None:
        """Test handling of API exceptions"""
        with patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"}), \
             patch("bot.api.google.image_generation_client.genai") as mock_genai:
            
            mock_client_instance = Mock()
            mock_client_instance.models.generate_content.side_effect = Exception("Gemini API error")
            mock_genai.Client.return_value = mock_client_instance
            
            client = GeminiImageGenerationClient()
            result_bytes, error_msg = await client.generate_image("test")
            
            assert result_bytes is None
            assert "Gemini API error" in error_msg

    def test_size_to_aspect_ratio_mappings(self) -> None:
        """Test that size mappings are correct"""
        assert SIZE_TO_ASPECT_RATIO["1024x1024"] == "1:1"
        assert SIZE_TO_ASPECT_RATIO["1536x1024"] == "3:2"
        assert SIZE_TO_ASPECT_RATIO["1024x1536"] == "2:3"
        assert SIZE_TO_ASPECT_RATIO["auto"] == "1:1"


class TestGeminiImageEditClient:
    """Tests for Google Gemini ImageEditClient"""

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-google-key-456"})
    @patch("bot.api.google.image_edit_client.genai")
    def test_initialization_with_api_key(self, mock_genai: Mock) -> None:
        """Test that client initializes successfully with API key"""
        client = GeminiImageEditClient()
        
        assert client.api_key == "test-google-key-456"
        mock_genai.Client.assert_called_once_with(api_key="test-google-key-456")

    @patch.dict("os.environ", {}, clear=True)
    def test_initialization_without_api_key_raises_error(self) -> None:
        """Test that missing API key raises EnvironmentError"""
        with pytest.raises(EnvironmentError, match="GOOGLE_API_KEY"):
            GeminiImageEditClient()

    @patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"})
    @patch("bot.api.google.image_edit_client.genai")
    def test_factory_method_creates_instance(self, mock_genai: Mock) -> None:
        """Test factory method returns correct instance"""
        client = GeminiImageEditClient.factory()
        
        assert isinstance(client, GeminiImageEditClient)

    @pytest.mark.asyncio
    async def test_edit_image_with_file_path(self) -> None:
        """Test editing image from file path"""
        with patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"}), \
             patch("bot.api.google.image_edit_client.genai") as mock_genai, \
             patch("bot.api.google.image_edit_client.types") as mock_types, \
             patch("bot.api.google.image_edit_client.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread, \
             patch("builtins.open", new_callable=mock_open, read_data=b"fake_image") as mock_file:
            
            # Mock response
            fake_edited_bytes = b"edited_image_data"
            mock_part = SimpleNamespace(inline_data=SimpleNamespace(data=fake_edited_bytes))
            mock_response = SimpleNamespace(
                candidates=[SimpleNamespace(content=SimpleNamespace(parts=[mock_part]))]
            )
            
            mock_client_instance = Mock()
            mock_to_thread.return_value = mock_response
            mock_genai.Client.return_value = mock_client_instance
            
            # Mock types objects
            mock_types.Part.from_image.return_value = MagicMock()
            mock_types.Image.return_value = MagicMock()
            mock_types.GenerateContentConfig.return_value = MagicMock()
            mock_types.ImageConfig.return_value = MagicMock()
            
            client = GeminiImageEditClient()
            result, error_msg = await client.edit_image(
                image="/path/to/image.png",
                prompt="make it blue"
            )
            
            # Verify success
            assert result is not None
            assert len(result) == 1
            assert result[0] == fake_edited_bytes
            assert error_msg == ""
            
            # Verify file was opened
            mock_file.assert_called_with("/path/to/image.png", "rb")

    @pytest.mark.asyncio
    async def test_edit_image_with_bytes(self) -> None:
        """Test editing image from bytes"""
        with patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"}), \
             patch("bot.api.google.image_edit_client.genai") as mock_genai, \
             patch("bot.api.google.image_edit_client.types") as mock_types, \
             patch("bot.api.google.image_edit_client.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            
            fake_edited_bytes = b"edited"
            mock_part = SimpleNamespace(inline_data=SimpleNamespace(data=fake_edited_bytes))
            mock_response = SimpleNamespace(
                candidates=[SimpleNamespace(content=SimpleNamespace(parts=[mock_part]))]
            )
            
            mock_client_instance = Mock()
            mock_to_thread.return_value = mock_response
            mock_genai.Client.return_value = mock_client_instance
            
            # Mock types objects
            mock_types.Part.from_image.return_value = MagicMock()
            mock_types.Image.return_value = MagicMock()
            mock_types.GenerateContentConfig.return_value = MagicMock()
            mock_types.ImageConfig.return_value = MagicMock()
            
            client = GeminiImageEditClient()
            image_bytes = b"original_image_data"
            result, error_msg = await client.edit_image(
                image=image_bytes,
                prompt="make it red"
            )
            
            assert result is not None
            assert len(result) == 1
            assert error_msg == ""

    @pytest.mark.asyncio
    async def test_edit_image_with_file_like_object(self) -> None:
        """Test editing image from file-like object"""
        with patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"}), \
             patch("bot.api.google.image_edit_client.genai") as mock_genai, \
             patch("bot.api.google.image_edit_client.types") as mock_types, \
             patch("bot.api.google.image_edit_client.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            
            fake_edited_bytes = b"edited"
            mock_part = SimpleNamespace(inline_data=SimpleNamespace(data=fake_edited_bytes))
            mock_response = SimpleNamespace(
                candidates=[SimpleNamespace(content=SimpleNamespace(parts=[mock_part]))]
            )
            
            mock_client_instance = Mock()
            mock_to_thread.return_value = mock_response
            mock_genai.Client.return_value = mock_client_instance
            
            # Mock types objects
            mock_types.Part.from_image.return_value = MagicMock()
            mock_types.Image.return_value = MagicMock()
            mock_types.GenerateContentConfig.return_value = MagicMock()
            mock_types.ImageConfig.return_value = MagicMock()
            
            client = GeminiImageEditClient()
            image_io = BytesIO(b"image_data")
            result, error_msg = await client.edit_image(
                image=image_io,
                prompt="edit this"
            )
            
            assert result is not None
            assert error_msg == ""

    @pytest.mark.asyncio
    async def test_edit_image_multiple_images(self) -> None:
        """Test editing with n > 1 generates multiple images"""
        with patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"}), \
             patch("bot.api.google.image_edit_client.genai") as mock_genai, \
             patch("bot.api.google.image_edit_client.types") as mock_types, \
             patch("bot.api.google.image_edit_client.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            
            # Mock responses for multiple calls
            fake_bytes_1 = b"edited1"
            fake_bytes_2 = b"edited2"
            
            mock_part_1 = SimpleNamespace(inline_data=SimpleNamespace(data=fake_bytes_1))
            mock_response_1 = SimpleNamespace(
                candidates=[SimpleNamespace(content=SimpleNamespace(parts=[mock_part_1]))]
            )
            
            mock_part_2 = SimpleNamespace(inline_data=SimpleNamespace(data=fake_bytes_2))
            mock_response_2 = SimpleNamespace(
                candidates=[SimpleNamespace(content=SimpleNamespace(parts=[mock_part_2]))]
            )
            
            mock_client_instance = Mock()
            mock_to_thread.side_effect = [mock_response_1, mock_response_2]
            mock_genai.Client.return_value = mock_client_instance
            
            # Mock types objects
            mock_types.Part.from_image.return_value = MagicMock()
            mock_types.Image.return_value = MagicMock()
            mock_types.GenerateContentConfig.return_value = MagicMock()
            mock_types.ImageConfig.return_value = MagicMock()
            
            client = GeminiImageEditClient()
            result, error_msg = await client.edit_image(
                image=b"image",
                prompt="test",
                n=2
            )
            
            # Should call API twice and return both images
            assert result is not None
            assert len(result) == 2
            assert result[0] == fake_bytes_1
            assert result[1] == fake_bytes_2
            assert error_msg == ""
            assert mock_to_thread.call_count == 2

    @pytest.mark.asyncio
    async def test_edit_image_accepts_openai_params(self) -> None:
        """Test that OpenAI-specific parameters are accepted but ignored"""
        with patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"}), \
             patch("bot.api.google.image_edit_client.genai") as mock_genai, \
             patch("bot.api.google.image_edit_client.types") as mock_types, \
             patch("bot.api.google.image_edit_client.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            
            fake_edited_bytes = b"edited"
            mock_part = SimpleNamespace(inline_data=SimpleNamespace(data=fake_edited_bytes))
            mock_response = SimpleNamespace(
                candidates=[SimpleNamespace(content=SimpleNamespace(parts=[mock_part]))]
            )
            
            mock_client_instance = Mock()
            mock_to_thread.return_value = mock_response
            mock_genai.Client.return_value = mock_client_instance
            
            # Mock types objects
            mock_types.Part.from_image.return_value = MagicMock()
            mock_types.Image.return_value = MagicMock()
            mock_types.GenerateContentConfig.return_value = MagicMock()
            mock_types.ImageConfig.return_value = MagicMock()
            
            client = GeminiImageEditClient()
            
            # Should not raise error even with OpenAI-specific params
            result, error_msg = await client.edit_image(
                image=b"image",
                prompt="test",
                quality="high",  # OpenAI-specific
                background="transparent"  # OpenAI-specific
            )
            
            assert result is not None
            assert error_msg == ""

    @pytest.mark.asyncio
    async def test_edit_image_no_candidates(self) -> None:
        """Test handling when API returns no candidates"""
        with patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"}), \
             patch("bot.api.google.image_edit_client.genai") as mock_genai, \
             patch("bot.api.google.image_edit_client.types") as mock_types, \
             patch("bot.api.google.image_edit_client.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            
            mock_response = SimpleNamespace(candidates=[])
            
            mock_client_instance = Mock()
            mock_to_thread.return_value = mock_response
            mock_genai.Client.return_value = mock_client_instance
            
            # Mock types objects
            mock_types.Part.from_image.return_value = MagicMock()
            mock_types.Image.return_value = MagicMock()
            mock_types.GenerateContentConfig.return_value = MagicMock()
            mock_types.ImageConfig.return_value = MagicMock()
            
            client = GeminiImageEditClient()
            result, error_msg = await client.edit_image(
                image=b"image",
                prompt="test"
            )
            
            assert result is None
            assert "no image data" in error_msg.lower()

    @pytest.mark.asyncio
    async def test_edit_image_no_image_parts_in_response(self) -> None:
        """Test handling when response has no image parts"""
        with patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"}), \
             patch("bot.api.google.image_edit_client.genai") as mock_genai, \
             patch("bot.api.google.image_edit_client.types") as mock_types, \
             patch("bot.api.google.image_edit_client.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            
            mock_text_part = SimpleNamespace(inline_data=None)
            mock_response = SimpleNamespace(
                candidates=[SimpleNamespace(content=SimpleNamespace(parts=[mock_text_part]))]
            )
            
            mock_client_instance = Mock()
            mock_to_thread.return_value = mock_response
            mock_genai.Client.return_value = mock_client_instance
            
            # Mock types objects
            mock_types.Part.from_image.return_value = MagicMock()
            mock_types.Image.return_value = MagicMock()
            mock_types.GenerateContentConfig.return_value = MagicMock()
            mock_types.ImageConfig.return_value = MagicMock()
            
            client = GeminiImageEditClient()
            result, error_msg = await client.edit_image(
                image=b"image",
                prompt="test"
            )
            
            assert result is None
            assert "no image data" in error_msg.lower()

    @pytest.mark.asyncio
    async def test_edit_image_file_not_found(self) -> None:
        """Test handling of file not found error"""
        with patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"}), \
             patch("bot.api.google.image_edit_client.genai") as mock_genai, \
             patch("builtins.open", side_effect=FileNotFoundError("image.png")):
            
            mock_genai.Client.return_value = Mock()
            mock_genai.types = MagicMock()
            
            client = GeminiImageEditClient()
            result, error_msg = await client.edit_image(
                image="/nonexistent/image.png",
                prompt="test"
            )
            
            assert result is None
            assert "File not found" in error_msg

    @pytest.mark.asyncio
    async def test_edit_image_api_exception(self) -> None:
        """Test handling of API exceptions"""
        with patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"}), \
             patch("bot.api.google.image_edit_client.genai") as mock_genai, \
             patch("bot.api.google.image_edit_client.types") as mock_types, \
             patch("bot.api.google.image_edit_client.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            
            mock_client_instance = Mock()
            mock_to_thread.side_effect = Exception("Gemini API error")
            mock_genai.Client.return_value = mock_client_instance
            
            # Mock types objects
            mock_types.Part.from_image.return_value = MagicMock()
            mock_types.Image.return_value = MagicMock()
            mock_types.GenerateContentConfig.return_value = MagicMock()
            mock_types.ImageConfig.return_value = MagicMock()
            
            client = GeminiImageEditClient()
            result, error_msg = await client.edit_image(
                image=b"image",
                prompt="test"
            )
            
            assert result is None
            assert "Gemini API error" in error_msg

    @pytest.mark.asyncio
    async def test_edit_image_invalid_input_type(self) -> None:
        """Test that invalid input type returns error"""
        with patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"}), \
             patch("bot.api.google.image_edit_client.genai") as mock_genai:
            
            mock_genai.Client.return_value = Mock()
            mock_genai.types = MagicMock()
            
            client = GeminiImageEditClient()
            result, error_msg = await client.edit_image(
                image=12345,  # Invalid type
                prompt="test"
            )
            
            assert result is None
            assert "Invalid image input type" in error_msg

    @pytest.mark.asyncio
    async def test_edit_image_partial_failure_with_multiple_n(self) -> None:
        """Test that partial failures are handled when n > 1"""
        with patch.dict("os.environ", {"GOOGLE_API_KEY": "test-key"}), \
             patch("bot.api.google.image_edit_client.genai") as mock_genai, \
             patch("bot.api.google.image_edit_client.types") as mock_types, \
             patch("bot.api.google.image_edit_client.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            
            # First call succeeds, second fails
            fake_bytes = b"edited"
            mock_part = SimpleNamespace(inline_data=SimpleNamespace(data=fake_bytes))
            mock_response = SimpleNamespace(
                candidates=[SimpleNamespace(content=SimpleNamespace(parts=[mock_part]))]
            )
            
            mock_client_instance = Mock()
            mock_to_thread.side_effect = [
                mock_response,
                Exception("API error on second call")
            ]
            mock_genai.Client.return_value = mock_client_instance
            
            # Mock types objects
            mock_types.Part.from_image.return_value = MagicMock()
            mock_types.Image.return_value = MagicMock()
            mock_types.GenerateContentConfig.return_value = MagicMock()
            mock_types.ImageConfig.return_value = MagicMock()
            
            client = GeminiImageEditClient()
            result, error_msg = await client.edit_image(
                image=b"image",
                prompt="test",
                n=2
            )
            
            # Should still return the successful result
            assert result is not None
            assert len(result) == 1
            assert result[0] == fake_bytes
            assert error_msg == ""

