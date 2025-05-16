"""
llm_client.py
Core LLM client logic for the bot.
"""

from typing import List, Literal
from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI

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
class LLMClient:
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
        if self.provider == "openai":
            self.llm = ChatOpenAI(model=model)
        else:
            raise ValueError(f"Unsupported provider: {self.provider}")


    async def chat(self, history: List[BaseMessage]) -> str:
        """
        Chat with the LLM model, maintaining message history per session.
        Args:
            history: List of BaseMessage objects.
        Returns:
            The assistant's reply as a string.
        """
        if self.provider == "openai":
            response = await self.llm.ainvoke(history)
            # print("Response: ", response)
            if hasattr(response, "content"):
                return str(response.content)
            if isinstance(response, dict) and "content" in response:
                return str(response["content"])
            return str(response)
        raise NotImplementedError(f"chat not implemented for provider: {self.provider}")

    async def summarize(self, text: str) -> str:
        if self.provider == "openai":
            from langchain_core.messages import HumanMessage
            prompt = (
                "Summarize the following text in a concise manner:\n\n"
                f"{text}"
                
            )
            response = await self.llm.ainvoke([HumanMessage(content=prompt)])
            return str(response.content) if hasattr(response, "content") else ""
        raise NotImplementedError(f"summarize not implemented for provider: {self.provider}")

    @staticmethod
    def factory(model: PermittedModelType = "gpt-4o-mini") -> "LLMClient":
        return LLMClient(model=model)

