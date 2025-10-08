"""Unit tests for OpenAI image generation and editing clients"""

import pytest
import base64
from unittest.mock import AsyncMock, MagicMock, patch, Mock, mock_open
from io import BytesIO
from types import SimpleNamespace

from bot.api.openai.image_generation_client import ImageGenerationClient
from bot.api.openai.image_edit_client import ImageEditClient


class TestImageGenerationClient:
    """Tests for OpenAI ImageGenerationClient"""

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key-123"})
    def test_initialization_with_api_key(self) -> None:
        """Test that client initializes successfully with API key"""
        client = ImageGenerationClient()
        
        assert client.api_key == "test-key-123"
        assert client.model == "gpt-image-1"

    @patch.dict("os.environ", {}, clear=True)
    def test_initialization_without_api_key_raises_error(self) -> None:
        """Test that missing API key raises EnvironmentError"""
        with pytest.raises(EnvironmentError, match="OPENAI_API_KEY"):
            ImageGenerationClient()

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"})
    def test_factory_method_creates_instance(self) -> None:
        """Test factory method returns correct instance"""
        client = ImageGenerationClient.factory()
        
        assert isinstance(client, ImageGenerationClient)
        assert client.model == "gpt-image-1"

    @pytest.mark.asyncio
    async def test_generate_image_success(self) -> None:
        """Test successful image generation"""
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}), \
             patch("bot.api.openai.image_generation_client.openai") as mock_openai, \
             patch("bot.api.openai.image_generation_client.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            
            # Create mock response
            fake_image_bytes = b"fake_png_data"
            fake_b64 = base64.b64encode(fake_image_bytes).decode()
            
            mock_response = SimpleNamespace(
                data=[
                    SimpleNamespace(
                        b64_json=fake_b64,
                        revised_prompt=None
                    )
                ]
            )
            
            mock_to_thread.return_value = mock_response
            
            client = ImageGenerationClient()
            result_bytes, error_msg = await client.generate_image("a sunset")
            
            # Verify result
            assert result_bytes == fake_image_bytes
            assert error_msg == ""
            
            # Verify API was called correctly via asyncio.to_thread
            mock_to_thread.assert_awaited_once()
            call_args = mock_to_thread.call_args
            assert call_args[0][0] == mock_openai.images.generate
            assert call_args[1]["prompt"] == "a sunset"
            assert call_args[1]["n"] == 1
            assert call_args[1]["size"] == "1024x1024"

    @pytest.mark.asyncio
    async def test_generate_image_with_custom_size(self) -> None:
        """Test image generation with custom size parameter"""
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}), \
             patch("bot.api.openai.image_generation_client.openai"), \
             patch("bot.api.openai.image_generation_client.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            
            fake_b64 = base64.b64encode(b"image").decode()
            mock_response = SimpleNamespace(
                data=[SimpleNamespace(b64_json=fake_b64, revised_prompt=None)]
            )
            mock_to_thread.return_value = mock_response
            
            client = ImageGenerationClient()
            await client.generate_image("test", size="1536x1024", n=2)
            
            # Verify size and n parameters were passed
            call_args = mock_to_thread.call_args
            assert call_args[1]["size"] == "1536x1024"
            assert call_args[1]["n"] == 2

    @pytest.mark.asyncio
    async def test_generate_image_no_data_returned(self) -> None:
        """Test handling when API returns no data"""
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}), \
             patch("bot.api.openai.image_generation_client.openai"), \
             patch("bot.api.openai.image_generation_client.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            
            mock_response = SimpleNamespace(data=[])
            mock_to_thread.return_value = mock_response
            
            client = ImageGenerationClient()
            result_bytes, error_msg = await client.generate_image("test")
            
            assert result_bytes is None
            assert "No image data" in error_msg

    @pytest.mark.asyncio
    async def test_generate_image_no_b64_json(self) -> None:
        """Test handling when API returns data without b64_json"""
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}), \
             patch("bot.api.openai.image_generation_client.openai"), \
             patch("bot.api.openai.image_generation_client.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            
            mock_response = SimpleNamespace(
                data=[SimpleNamespace(b64_json=None, revised_prompt="test")]
            )
            mock_to_thread.return_value = mock_response
            
            client = ImageGenerationClient()
            result_bytes, error_msg = await client.generate_image("test")
            
            assert result_bytes is None
            assert "No image data" in error_msg

    @pytest.mark.asyncio
    async def test_generate_image_api_exception(self) -> None:
        """Test handling of API exceptions"""
        with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}), \
             patch("bot.api.openai.image_generation_client.openai"), \
             patch("bot.api.openai.image_generation_client.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            
            mock_to_thread.side_effect = Exception("API rate limit exceeded")
            
            client = ImageGenerationClient()
            result_bytes, error_msg = await client.generate_image("test")
            
            assert result_bytes is None
            assert "API rate limit exceeded" in error_msg


class TestImageEditClient:
    """Tests for OpenAI ImageEditClient"""

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key-456"})
    def test_initialization_with_api_key(self) -> None:
        """Test that client initializes successfully with API key"""
        client = ImageEditClient()
        
        assert client.api_key == "test-key-456"
        assert client.client is not None

    @patch.dict("os.environ", {}, clear=True)
    def test_initialization_without_api_key_raises_error(self) -> None:
        """Test that missing API key raises EnvironmentError"""
        with pytest.raises(EnvironmentError, match="OPENAI_API_KEY"):
            ImageEditClient()

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"})
    def test_factory_method_creates_instance(self) -> None:
        """Test factory method returns correct instance"""
        client = ImageEditClient.factory()
        
        assert isinstance(client, ImageEditClient)

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"})
    @patch("builtins.open", new_callable=mock_open, read_data=b"fake_image")
    def test_edit_image_with_file_path(self, mock_file: Mock) -> None:
        """Test editing image from file path"""
        client = ImageEditClient()
        
        # Mock the API response
        fake_b64 = base64.b64encode(b"edited_image").decode()
        mock_response = SimpleNamespace(
            data=[SimpleNamespace(b64_json=fake_b64)]
        )
        client.client.images.edit = Mock(return_value=mock_response)
        
        result, error_msg = client.edit_image(
            image="/path/to/image.png",
            prompt="make it blue"
        )
        
        # Verify success
        assert result is not None
        assert len(result) == 1
        assert result[0] == b"edited_image"
        assert error_msg == ""
        
        # Verify file was opened
        mock_file.assert_called_with("/path/to/image.png", "rb")

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"})
    def test_edit_image_with_bytes(self) -> None:
        """Test editing image from bytes"""
        client = ImageEditClient()
        
        # Mock the API response
        fake_b64 = base64.b64encode(b"edited").decode()
        mock_response = SimpleNamespace(
            data=[SimpleNamespace(b64_json=fake_b64)]
        )
        client.client.images.edit = Mock(return_value=mock_response)
        
        image_bytes = b"original_image_data"
        result, error_msg = client.edit_image(
            image=image_bytes,
            prompt="make it red"
        )
        
        assert result is not None
        assert len(result) == 1
        assert error_msg == ""

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"})
    def test_edit_image_with_file_like_object(self) -> None:
        """Test editing image from file-like object"""
        client = ImageEditClient()
        
        # Mock the API response
        fake_b64 = base64.b64encode(b"edited").decode()
        mock_response = SimpleNamespace(
            data=[SimpleNamespace(b64_json=fake_b64)]
        )
        client.client.images.edit = Mock(return_value=mock_response)
        
        image_io = BytesIO(b"image_data")
        result, error_msg = client.edit_image(
            image=image_io,
            prompt="edit this"
        )
        
        assert result is not None
        assert error_msg == ""

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"})
    def test_edit_image_invalid_n_parameter(self) -> None:
        """Test that invalid n parameter is rejected"""
        client = ImageEditClient()
        
        # Test n < 1
        result, error_msg = client.edit_image(
            image=b"fake",
            prompt="test",
            n=0
        )
        assert result is None
        assert "between 1 and 10" in error_msg
        
        # Test n > 10
        result, error_msg = client.edit_image(
            image=b"fake",
            prompt="test",
            n=11
        )
        assert result is None
        assert "between 1 and 10" in error_msg

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"})
    def test_edit_image_with_quality_and_background_params(self) -> None:
        """Test that quality and background parameters are passed correctly"""
        client = ImageEditClient()
        
        fake_b64 = base64.b64encode(b"edited").decode()
        mock_response = SimpleNamespace(
            data=[SimpleNamespace(b64_json=fake_b64)]
        )
        client.client.images.edit = Mock(return_value=mock_response)
        
        result, error_msg = client.edit_image(
            image=b"image",
            prompt="test",
            quality="high",
            background="transparent"
        )
        
        # Verify extra_body was used
        call_args = client.client.images.edit.call_args
        assert "extra_body" in call_args[1]
        assert call_args[1]["extra_body"]["quality"] == "high"
        assert call_args[1]["extra_body"]["background"] == "transparent"

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"})
    def test_edit_image_auto_params_not_in_extra_body(self) -> None:
        """Test that 'auto' values are not included in extra_body"""
        client = ImageEditClient()
        
        fake_b64 = base64.b64encode(b"edited").decode()
        mock_response = SimpleNamespace(
            data=[SimpleNamespace(b64_json=fake_b64)]
        )
        client.client.images.edit = Mock(return_value=mock_response)
        
        result, error_msg = client.edit_image(
            image=b"image",
            prompt="test",
            quality="auto",
            background="auto"
        )
        
        # Verify extra_body is not present or empty
        call_args = client.client.images.edit.call_args
        if "extra_body" in call_args[1]:
            assert len(call_args[1]["extra_body"]) == 0

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"})
    @patch("builtins.open", new_callable=mock_open, read_data=b"mask_data")
    def test_edit_image_with_mask(self, mock_file: Mock) -> None:
        """Test editing image with mask file"""
        client = ImageEditClient()
        
        fake_b64 = base64.b64encode(b"edited").decode()
        mock_response = SimpleNamespace(
            data=[SimpleNamespace(b64_json=fake_b64)]
        )
        client.client.images.edit = Mock(return_value=mock_response)
        
        result, error_msg = client.edit_image(
            image=b"image",
            prompt="test",
            mask_path="/path/to/mask.png"
        )
        
        # Verify mask file was opened
        assert any("/path/to/mask.png" in str(call) for call in mock_file.call_args_list)

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"})
    def test_edit_image_no_data_returned(self) -> None:
        """Test handling when API returns no data"""
        client = ImageEditClient()
        
        mock_response = SimpleNamespace(data=[])
        client.client.images.edit = Mock(return_value=mock_response)
        
        result, error_msg = client.edit_image(
            image=b"image",
            prompt="test"
        )
        
        assert result is None
        assert "No image data" in error_msg

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"})
    def test_edit_image_no_b64_json_in_response(self) -> None:
        """Test handling when response has no b64_json"""
        client = ImageEditClient()
        
        mock_response = SimpleNamespace(
            data=[SimpleNamespace(b64_json=None)]
        )
        client.client.images.edit = Mock(return_value=mock_response)
        
        result, error_msg = client.edit_image(
            image=b"image",
            prompt="test"
        )
        
        assert result is None
        assert "No images with b64_json" in error_msg

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"})
    @patch("builtins.open", side_effect=FileNotFoundError("image.png"))
    def test_edit_image_file_not_found(self, mock_file: Mock) -> None:
        """Test handling of file not found error"""
        client = ImageEditClient()
        
        result, error_msg = client.edit_image(
            image="/nonexistent/image.png",
            prompt="test"
        )
        
        assert result is None
        assert "File not found" in error_msg

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"})
    def test_edit_image_api_status_error(self) -> None:
        """Test handling of OpenAI API status errors"""
        from openai import APIStatusError
        
        client = ImageEditClient()
        
        # Create mock error response
        mock_response = Mock()
        mock_response.status_code = 429
        mock_response.content = b'{"error": {"message": "Rate limit exceeded"}}'
        mock_response.json.return_value = {"error": {"message": "Rate limit exceeded"}}
        mock_response.text = "Rate limit exceeded"
        
        mock_request = Mock()
        mock_error = APIStatusError(
            message="Rate limit", response=mock_response, body=None
        )
        
        client.client.images.edit = Mock(side_effect=mock_error)
        
        result, error_msg = client.edit_image(
            image=b"image",
            prompt="test"
        )
        
        assert result is None
        assert "429" in error_msg or "Rate limit" in error_msg

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"})
    def test_edit_image_multiple_images(self) -> None:
        """Test editing with n > 1 returns multiple images"""
        client = ImageEditClient()
        
        # Mock multiple images in response
        fake_b64_1 = base64.b64encode(b"image1").decode()
        fake_b64_2 = base64.b64encode(b"image2").decode()
        mock_response = SimpleNamespace(
            data=[
                SimpleNamespace(b64_json=fake_b64_1),
                SimpleNamespace(b64_json=fake_b64_2)
            ]
        )
        client.client.images.edit = Mock(return_value=mock_response)
        
        result, error_msg = client.edit_image(
            image=b"image",
            prompt="test",
            n=2
        )
        
        assert result is not None
        assert len(result) == 2
        assert result[0] == b"image1"
        assert result[1] == b"image2"
        assert error_msg == ""

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"})
    def test_edit_image_invalid_input_type(self) -> None:
        """Test that invalid input type returns error"""
        client = ImageEditClient()
        
        result, error_msg = client.edit_image(
            image=12345,  # Invalid type
            prompt="test"
        )
        
        assert result is None
        assert "Invalid image input type" in error_msg

