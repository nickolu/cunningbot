"""
chat_completions_client.py
Core LLM client logic for the bot.
"""

import os
from typing import Any, Dict, Iterable, List, Literal

from openai import AsyncOpenAI
from openai.types.chat import (ChatCompletionAssistantMessageParam,
                               ChatCompletionDeveloperMessageParam,
                               ChatCompletionFunctionMessageParam,
                               ChatCompletionMessageParam,
                               ChatCompletionSystemMessageParam,
                               ChatCompletionToolMessageParam,
                               ChatCompletionUserMessageParam)

from bot.domain.logger import get_logger

logger = get_logger()

openai = AsyncOpenAI()

PermittedModelType = Literal[
    "gpt-3.5-turbo",
    "gpt-4",
    "gpt-4-turbo",
    "gpt-4.1-mini",
    "gpt-4.1-nano",
    "gpt-4.1",
    "gpt-4.5-preview",
    "gpt-4o-mini",
    "gpt-4o",
    "o3",
    "o4-mini",
    "o4",
]

def transform_arguments_for_model(model: PermittedModelType) -> Dict[str, Any]:
    if model == "gpt-3.5-turbo":
        return {"model": "gpt-3.5-turbo", "max_tokens": 10000}
    elif model == "gpt-4":
        return {"model": "gpt-4", "max_tokens": 10000}
    elif model == "gpt-4-turbo":
        return {"model": "gpt-4-turbo", "max_tokens": 10000}
    elif model == "gpt-4.1-mini":
        return {"model": "gpt-4.1-mini", "max_tokens": 10000}
    elif model == "gpt-4.1-nano":
        return {"model": "gpt-4.1-nano", "max_tokens": 10000}
    elif model == "gpt-4.1":
        return {"model": "gpt-4.1", "max_tokens": 10000}
    elif model == "gpt-4.5-preview":
        return {"model": "gpt-4.5-preview", "max_tokens": 10000}
    elif model == "gpt-4o-mini":
        return {"model": "gpt-4o-mini", "max_completion_tokens": 10000}
    elif model == "gpt-4o":
        return {"model": "gpt-4o", "max_completion_tokens": 10000}
    elif model == "o3":
        return {"model": "o3", "max_completion_tokens": 10000}
    elif model == "o4-mini":
        return {"model": "o4-mini", "max_completion_tokens": 10000}
    elif model == "o4":
        return {"model": "o4", "max_completion_tokens": 10000}

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
    PERMITTED_MODELS = {
        "gpt-3.5-turbo": "openai",
        "gpt-4": "openai",
        "gpt-4-turbo": "openai",
        "gpt-4.1-mini": "openai",
        "gpt-4.1-nano": "openai",
        "gpt-4.1": "openai",
        "gpt-4.5-preview": "openai",
        "gpt-4o-mini": "openai",
        "gpt-4o": "openai",
        "o3": "openai",
        "o4-mini": "openai",
        "o4": "openai",
    }

    def __init__(self, model: PermittedModelType = "gpt-4o-mini"):
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
            response = await openai.chat.completions.create(
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
        response = await openai.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            **transform_arguments_for_model(self.model),
        )
        return response.choices[0].message.content or ""

    @staticmethod
    def factory(model: PermittedModelType = "gpt-4o-mini") -> "ChatCompletionsClient":
        return ChatCompletionsClient(model=model)

