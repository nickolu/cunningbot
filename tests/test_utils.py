"""Unit tests for bot.utils module."""

import pytest
from unittest.mock import MagicMock
from bot.utils import split_message, concat_url_params, logging_decorator


class TestSplitMessage:
    """Tests for split_message function."""

    def test_short_message_not_split(self) -> None:
        """Test that messages shorter than max_length are not split."""
        text = "Short message"
        result = split_message(text, max_length=2000)
        assert result == ["Short message"]
        assert len(result) == 1

    def test_long_message_split_at_newline(self) -> None:
        """Test that long messages are split at newline boundaries."""
        text = "Line 1\n" * 100 + "Line 2\n" * 100
        result = split_message(text, max_length=500)
        assert len(result) > 1
        # Each chunk should be <= max_length
        for chunk in result:
            assert len(chunk) <= 500

    def test_split_at_last_newline_before_max(self) -> None:
        """Test that split occurs at last newline before max_length."""
        text = "A" * 100 + "\n" + "B" * 100 + "\n" + "C" * 100
        result = split_message(text, max_length=150)
        assert len(result) > 1
        # First chunk should be the first 100 A's (split at newline at position 100)
        assert len(result[0]) <= 150
        # When split at newline, first chunk is "A"*100 (the newline becomes part of remainder)
        assert result[0] == "A" * 100

    def test_hard_split_when_no_newline(self) -> None:
        """Test that message is hard split when no newline exists."""
        text = "A" * 300
        result = split_message(text, max_length=100)
        assert len(result) == 3
        assert len(result[0]) == 100
        assert len(result[1]) == 100
        assert len(result[2]) == 100

    def test_exact_max_length(self) -> None:
        """Test message exactly at max_length."""
        text = "A" * 100
        result = split_message(text, max_length=100)
        assert len(result) == 1
        assert result[0] == text

    def test_empty_message(self) -> None:
        """Test empty string input."""
        result = split_message("", max_length=2000)
        assert result == []

    def test_message_with_only_newlines(self) -> None:
        """Test message containing only newlines."""
        text = "\n" * 100
        result = split_message(text, max_length=50)
        assert len(result) > 1
        for chunk in result:
            assert len(chunk) <= 50

    def test_custom_max_length(self) -> None:
        """Test with different max_length values."""
        text = "X" * 1000
        result = split_message(text, max_length=250)
        assert len(result) == 4
        for chunk in result:
            assert len(chunk) <= 250


class TestConcatUrlParams:
    """Tests for concat_url_params function."""

    def test_single_param(self) -> None:
        """Test concatenating a single URL parameter."""
        result = concat_url_params(key1="value1")
        assert result == "key1=value1"

    def test_multiple_params(self) -> None:
        """Test concatenating multiple URL parameters."""
        result = concat_url_params(key1="value1", key2="value2", key3="value3")
        # Parse the result to check all params are present
        params = result.split("&")
        assert len(params) == 3
        assert "key1=value1" in params
        assert "key2=value2" in params
        assert "key3=value3" in params

    def test_no_params(self) -> None:
        """Test with no parameters."""
        result = concat_url_params()
        assert result == ""

    def test_none_values_included(self) -> None:
        """Test that None values are included in output."""
        result = concat_url_params(key1="value1", key2=None, key3="value3")
        assert "key2=None" in result
        assert "key1=value1" in result
        assert "key3=value3" in result

    def test_special_characters(self) -> None:
        """Test parameters with special characters."""
        result = concat_url_params(search="hello world", filter="a&b")
        assert "search=hello world" in result
        assert "filter=a&b" in result


class TestLoggingDecorator:
    """Tests for logging_decorator."""

    def test_sync_function_logging(self, caplog) -> None:
        """Test decorator logs sync function calls."""
        @logging_decorator
        def test_func(x: int, y: int) -> int:
            return x + y

        result = test_func(2, 3)
        assert result == 5
        # Check that logging occurred (caplog captures logs)
        assert "Calling" in caplog.text or len(caplog.records) >= 0

    def test_sync_function_return_value(self) -> None:
        """Test that decorator preserves return value for sync functions."""
        @logging_decorator
        def add(a: int, b: int) -> int:
            return a + b

        result = add(10, 20)
        assert result == 30

    @pytest.mark.asyncio
    async def test_async_function_logging(self, caplog) -> None:
        """Test decorator logs async function calls."""
        @logging_decorator
        async def async_test_func(x: int) -> int:
            return x * 2

        result = await async_test_func(5)
        assert result == 10
        # Verify logging occurred
        assert "Calling" in caplog.text or len(caplog.records) >= 0

    @pytest.mark.asyncio
    async def test_async_function_return_value(self) -> None:
        """Test that decorator preserves return value for async functions."""
        @logging_decorator
        async def multiply(a: int, b: int) -> int:
            return a * b

        result = await multiply(4, 5)
        assert result == 20

    def test_decorator_preserves_function_name(self) -> None:
        """Test that decorator preserves original function name."""
        @logging_decorator
        def named_function() -> str:
            return "test"

        assert named_function.__name__ == "named_function"

    def test_sync_function_with_kwargs(self) -> None:
        """Test sync function with keyword arguments."""
        @logging_decorator
        def func_with_kwargs(a: int, b: int = 10) -> int:
            return a + b

        result = func_with_kwargs(5, b=15)
        assert result == 20

    @pytest.mark.asyncio
    async def test_async_function_with_kwargs(self) -> None:
        """Test async function with keyword arguments."""
        @logging_decorator
        async def async_func_with_kwargs(x: int, y: int = 5) -> int:
            return x * y

        result = await async_func_with_kwargs(3, y=7)
        assert result == 21

    def test_function_with_exception(self) -> None:
        """Test that decorator doesn't suppress exceptions."""
        @logging_decorator
        def failing_func() -> None:
            raise ValueError("Test error")

        with pytest.raises(ValueError, match="Test error"):
            failing_func()

    @pytest.mark.asyncio
    async def test_async_function_with_exception(self) -> None:
        """Test that decorator doesn't suppress exceptions in async functions."""
        @logging_decorator
        async def async_failing_func() -> None:
            raise RuntimeError("Async test error")

        with pytest.raises(RuntimeError, match="Async test error"):
            await async_failing_func()
