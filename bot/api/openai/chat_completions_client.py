"""
chat_completions_client.py
Core LLM client logic for the bot.
"""

from typing import List, Dict, Any, Iterable, Optional
from openai import AsyncOpenAI
import os

from openai.types.chat import ChatCompletionUserMessageParam, ChatCompletionAssistantMessageParam, ChatCompletionSystemMessageParam, ChatCompletionDeveloperMessageParam, ChatCompletionFunctionMessageParam, ChatCompletionToolMessageParam, ChatCompletionMessageParam, ChatCompletionFunctionMessageParam
from bot.domain.llm.models import (
    DEFAULT_CHAT_MODEL,
    PERMITTED_MODELS as _PERMITTED_MODELS,
    PermittedModelType as _PermittedModelType,
    request_arguments as _request_arguments,
)
from bot.app.utils.logger import get_logger
logger = get_logger()

_client: Optional[AsyncOpenAI] = None


def get_client() -> AsyncOpenAI:
    """Return the shared AsyncOpenAI client, constructing it on first use.

    Constructed lazily because AsyncOpenAI() raises when OPENAI_API_KEY is
    unset, and this module is imported transitively by callers that never make
    an API call (e.g. the RSS collector importing breaking_news_service only
    for its keyword matcher). A missing key should fail at the call site, not
    at import time.
    """
    global _client
    if _client is None:
        _client = AsyncOpenAI()
    return _client


async def close_client() -> None:
    """Close the shared client if one was ever constructed."""
    global _client
    if _client is not None:
        await _client.close()
        _client = None


def __getattr__(name: str) -> Any:
    # Back-compat for `from ... import openai`; constructs on access.
    if name == "openai":
        return get_client()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# The catalog lives in bot/domain/llm/models.py. Re-exported here because
# callers have imported these names from this module since before it existed.
PermittedModelType = _PermittedModelType
transform_arguments_for_model = _request_arguments


def transform_history_to_openai(history: List[Dict[str, Any]]) -> Iterable[ChatCompletionMessageParam]:
    for message in history:
        if message["role"] == "user":
            yield ChatCompletionUserMessageParam(content=message["content"], role="user")
        elif message["role"] == "assistant":
            yield ChatCompletionAssistantMessageParam(content=message["content"], role="assistant")
        elif message["role"] == "system":
            yield ChatCompletionSystemMessageParam(content=message["content"], role="system")
        elif message["role"] == "developer":
            yield ChatCompletionDeveloperMessageParam(content=message["content"], role="developer")
        elif message["role"] == "function":
            yield ChatCompletionFunctionMessageParam(content=message["content"], role="function", name=message["name"])
        elif message["role"] == "tool":
            yield ChatCompletionToolMessageParam(content=message["content"], role="tool", tool_call_id=message["tool_call_id"])
        else:
            raise ValueError(f"Unsupported role: {message['role']}")
    

class ChatCompletionsClient:
    #: Model id -> vendor. Derived; see bot/domain/llm/models.py.
    PERMITTED_MODELS = _PERMITTED_MODELS

    def __init__(self, model: PermittedModelType = DEFAULT_CHAT_MODEL):
        self.model = model
        self.provider = self.PERMITTED_MODELS.get(model)
        if not self.provider:
            raise ValueError(f"Unsupported or unknown model: {model}")
        if self.provider != "openai":
            raise ValueError(f"Unsupported provider: {self.provider}")
        self.api_key = os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise EnvironmentError("OPENAI_API_KEY environment variable is not set.")

    async def chat(self, history: List[Dict[str, Any]]) -> str:
        """
        Chat with the LLM model, maintaining message history per session.
        Args:
            history: List of message dicts with 'role' and 'content'.
        Returns:
            The assistant's reply as a string.
        """

        try:
            openai_history = transform_history_to_openai(history)
            response = await get_client().chat.completions.create(
                messages=openai_history,
                **transform_arguments_for_model(self.model),
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            logger.error(f"Failed to generate response: {e}")
            return "There was an error: " + str(e)

    async def summarize(self, text: str) -> str:
        prompt = (
            "Summarize the following text in a concise manner:\n\n"
            f"{text}"
        )
        response = await get_client().chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            **transform_arguments_for_model(self.model),
        )
        return response.choices[0].message.content or ""

    @staticmethod
    def factory(model: PermittedModelType = DEFAULT_CHAT_MODEL) -> "ChatCompletionsClient":
        return ChatCompletionsClient(model=model)

