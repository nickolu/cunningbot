from typing import Dict, List
import pytest
from unittest.mock import AsyncMock, patch

from bot.core.llm_client import LLMClient

@pytest.fixture
def chat_history() -> List[Dict[str, str]]:
    return [
        {"role": "user", "content": "Hello, who are you?"},
        {"role": "assistant", "content": "I'm a bot."},
        {"role": "user", "content": "What can you do?"}
    ]

@pytest.mark.asyncio
@patch("bot.core.llm_client.ChatOpenAI")
async def test_chat_openai(mock_chatopenai: AsyncMock, chat_history: List[Dict[str, str]]) -> None:
    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = AsyncMock(content="I can chat with you.")
    mock_chatopenai.return_value = mock_llm

    client = LLMClient(model="gpt-4o-mini")
    with patch.object(client, "memory"):
        result = await client.chat(chat_history)
    assert result == "I can chat with you."
    mock_llm.ainvoke.assert_awaited()

@pytest.mark.asyncio
@patch("bot.core.llm_client.ChatOpenAI")
async def test_summarize_openai(mock_chatopenai: AsyncMock) -> None:
    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = AsyncMock(content="This is a summary.")
    mock_chatopenai.return_value = mock_llm

    client = LLMClient(model="gpt-4o-mini")
    with patch.object(client, "memory"):
        result = await client.summarize("Long text here.")
    assert result == "This is a summary."
    mock_llm.ainvoke.assert_awaited()

def test_factory_returns_llmclient() -> None:
    client = LLMClient.factory(model="gpt-4o-mini")
    assert isinstance(client, LLMClient)
    assert client.provider == "openai"
    assert client.model == "gpt-4o-mini"

def test_invalid_model() -> None:
    with pytest.raises(ValueError):
        LLMClient(model="invalid-model") # type: ignore

# @pytest.mark.skip(reason="Live test - requires valid OpenAI API key and network access")
@pytest.mark.asyncio
async def test_live_chat_openai() -> None:
    """Live integration test: requires OPENAI_API_KEY in env and network access."""
    from bot.core.llm_client import LLMClient
    client = LLMClient(model="gpt-3.5-turbo")
    history = [
        {"role": "user", "content": "What is the capital of France?"}
    ]
    result = await client.chat(history)
    print("Ai response: ", result)
    assert isinstance(result, str)
    assert "Paris" in result
