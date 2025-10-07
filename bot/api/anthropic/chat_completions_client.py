"""
chat_completions_client.py
Anthropic client logic for chat completions.
"""

from typing import List, Dict, Any
from anthropic import AsyncAnthropic
import os

from bot.app.utils.logger import get_logger

logger = get_logger()


class AnthropicChatCompletionsClient:
    """Client for Anthropic Claude models."""

    def __init__(self, model: str = "claude-3-5-sonnet-20241022"):
        self.model = model
        self.api_key = os.getenv("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise EnvironmentError("ANTHROPIC_API_KEY environment variable is not set.")
        self.client = AsyncAnthropic(api_key=self.api_key)

    def transform_history_to_anthropic(self, history: List[Dict[str, Any]]) -> tuple[str, List[Dict[str, Any]]]:
        """
        Transform message history to Anthropic format.
        Extracts system messages and converts remaining messages.

        Returns:
            tuple of (system_prompt, messages)
        """
        system_messages = []
        anthropic_messages = []

        for message in history:
            role = message.get("role")
            content = message.get("content", "")

            if role == "system":
                system_messages.append(content)
            elif role == "user":
                anthropic_messages.append({
                    "role": "user",
                    "content": content
                })
            elif role == "assistant":
                anthropic_messages.append({
                    "role": "assistant",
                    "content": content
                })
            # Skip other roles like developer, function, tool as Anthropic doesn't support them

        system_prompt = "\n\n".join(system_messages) if system_messages else None
        return system_prompt, anthropic_messages

    async def chat(self, history: List[Dict[str, Any]]) -> str:
        """
        Chat with the Anthropic model, maintaining message history.

        Args:
            history: List of message dicts with 'role' and 'content'.

        Returns:
            The assistant's reply as a string.
        """
        try:
            system_prompt, messages = self.transform_history_to_anthropic(history)

            kwargs = {
                "model": self.model,
                "max_tokens": 8192,
                "messages": messages,
            }

            if system_prompt:
                kwargs["system"] = system_prompt

            response = await self.client.messages.create(**kwargs)

            # Extract text from response
            if response.content and len(response.content) > 0:
                return response.content[0].text
            return ""

        except Exception as e:
            logger.error(f"Failed to generate Anthropic response: {e}")
            return "There was an error: " + str(e)

    async def summarize(self, text: str) -> str:
        """Summarize the given text."""
        prompt = (
            "Summarize the following text in a concise manner:\n\n"
            f"{text}"
        )
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=8192,
            messages=[{"role": "user", "content": prompt}]
        )

        if response.content and len(response.content) > 0:
            return response.content[0].text
        return ""

    @staticmethod
    def factory(model: str = "claude-3-5-sonnet-20241022") -> "AnthropicChatCompletionsClient":
        return AnthropicChatCompletionsClient(model=model)
