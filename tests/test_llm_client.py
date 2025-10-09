from typing import Dict, List
import os
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from types import SimpleNamespace
from bot.api.openai.chat_completions_client import ChatCompletionsClient

@pytest.fixture
def chat_history() -> List[Dict]:
    return [
        {"role": "user", "content": "Hello, who are you?"},
        {"role": "assistant", "content": "I'm a bot."},
        {"role": "user", "content": "What can you do?"}
    ]

@pytest.mark.asyncio
@patch("bot.api.openai.chat_completions_client.openai")
async def test_chat_openai(mock_openai: MagicMock, chat_history: List[Dict]) -> None:
    # Mock the OpenAI response
    mock_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="I can chat with you."))]
    )
    mock_openai.chat.completions.create = AsyncMock(return_value=mock_response)

    client = ChatCompletionsClient(model="gpt-4o-mini")
    result = await client.chat(chat_history)
    
    assert result == "I can chat with you."
    mock_openai.chat.completions.create.assert_awaited_once()

@pytest.mark.asyncio
@patch("bot.api.openai.chat_completions_client.openai")
async def test_summarize_openai(mock_openai: MagicMock) -> None:
    # Mock the OpenAI response
    mock_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="This is a summary."))]
    )
    mock_openai.chat.completions.create = AsyncMock(return_value=mock_response)

    client = ChatCompletionsClient(model="gpt-4o-mini")
    result = await client.summarize("Long text here.")
    
    assert result == "This is a summary."
    mock_openai.chat.completions.create.assert_awaited_once()

def test_factory_returns_llmclient() -> None:
    client = ChatCompletionsClient.factory(model="gpt-4o-mini")
    assert isinstance(client, ChatCompletionsClient)
    assert client.provider == "openai"
    assert client.model == "gpt-4o-mini"

def test_invalid_model() -> None:
    with pytest.raises(ValueError):
        ChatCompletionsClient(model="invalid-model") # type: ignore

@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"),
    reason="Live test - requires OPENAI_API_KEY environment variable"
)
@pytest.mark.asyncio
async def test_live_chat_openai() -> None:
    """Live integration test: requires OPENAI_API_KEY in env and network access."""
    from bot.api.openai.chat_completions_client import ChatCompletionsClient
    client = ChatCompletionsClient(model="gpt-3.5-turbo")
    history = [
        {"role": "user", "content": "What is the capital of France?"},
    ]
    result = await client.chat(history)
    # print("Ai response: ", result)
    assert isinstance(result, str)
    assert "Paris" in result
