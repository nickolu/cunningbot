from types import SimpleNamespace
from typing import Dict, List
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from bot.domain.chat_completions_client import ChatCompletionsClient


@pytest.fixture
def chat_history() -> List[BaseMessage]:
    return [
        HumanMessage(content="Hello, who are you?"),
        AIMessage(content="I'm a bot."),
        HumanMessage(content="What can you do?")
    ]

@pytest.mark.asyncio
@patch("bot.domain.chat_completions_client.RunnableWithMessageHistory")
async def test_chat_openai(mock_runnable: AsyncMock, chat_history: List[BaseMessage]) -> None:
    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = SimpleNamespace(content="I can chat with you.")
    mock_runnable.return_value = mock_llm

    client = ChatCompletionsClient(model="gpt-4o-mini")
    result = await client.chat(chat_history)
    # print("Result: ", result)
    assert result == "I can chat with you."
    mock_llm.ainvoke.assert_awaited()

@pytest.mark.asyncio
@patch("bot.domain.chat_completions_client.ChatOpenAI")
async def test_summarize_openai(mock_chatopenai: AsyncMock) -> None:
    mock_llm = AsyncMock()
    mock_llm.ainvoke.return_value = SimpleNamespace(content="This is a summary.")
    mock_chatopenai.return_value = mock_llm

    client = ChatCompletionsClient(model="gpt-4o-mini")
    result = await client.summarize("Long text here.")
    assert result == "This is a summary."
    mock_llm.ainvoke.assert_awaited()

def test_factory_returns_llmclient() -> None:
    client = ChatCompletionsClient.factory(model="gpt-4o-mini")
    assert isinstance(client, ChatCompletionsClient)
    assert client.provider == "openai"
    assert client.model == "gpt-4o-mini"

def test_invalid_model() -> None:
    with pytest.raises(ValueError):
        ChatCompletionsClient(model="invalid-model") # type: ignore

@pytest.mark.skip(reason="Live test - requires valid OpenAI API key and network access")
@pytest.mark.asyncio
async def test_live_chat_openai() -> None:
    """Live integration test: requires OPENAI_API_KEY in env and network access."""
    from bot.domain.chat_completions_client import ChatCompletionsClient
    client = ChatCompletionsClient(model="gpt-3.5-turbo")
    history = [
        BaseMessage(content="What is the capital of France?"),
    ]
    result = await client.chat(history)
    # print("Ai response: ", result)
    assert isinstance(result, str)
    assert "Paris" in result
