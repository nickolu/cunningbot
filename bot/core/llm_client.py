"""
llm_client.py
Core LLM client logic for the bot.
"""

from typing import List, Dict, Any, Literal
from langchain_openai import ChatOpenAI
from langchain.memory import ConversationBufferMemory

class LLMClient:
    PERMITTED_MODELS = {
        "gpt-4o-mini": "openai",
        "gpt-4o": "openai",
        "gpt-4-turbo": "openai",
        "gpt-3.5-turbo": "openai",
    }
    _PermittedModelType = Literal[
        "gpt-4o-mini",
        "gpt-4o",
        "gpt-4-turbo",
        "gpt-3.5-turbo",
    ]

    def __init__(self, model: _PermittedModelType = "gpt-4o-mini"):
        self.model = model
        self.provider = self.PERMITTED_MODELS.get(model)
        self.memory: ConversationBufferMemory  # type annotation for mypy
        self.llm: ChatOpenAI  # type annotation for mypy    

        if not self.provider:
            raise ValueError(f"Unsupported or unknown model: {model}")
        if self.provider == "openai":
            self.llm = ChatOpenAI(model=model)
            self.memory = ConversationBufferMemory(memory_key="chat_history", return_messages=True)
        else:
            raise ValueError(f"Unsupported provider: {self.provider}")

    async def chat(self, history: List[Dict[str, Any]]) -> str:
        if self.provider == "openai":
            if not self.memory:
                raise ValueError("Memory not initialized")
            self.memory.clear()
            for msg in history:
                role = msg.get("role")
                if "content" not in msg or msg["content"] is None:
                    raise ValueError("Message dict missing non-null 'content'")
                content: str = msg["content"]
                if role == "user":
                    self.memory.chat_memory.add_user_message(content)
                elif role == "assistant":
                    self.memory.chat_memory.add_ai_message(content)
            response = await self.llm.ainvoke(self.memory.chat_memory.messages)
            return str(response.content) if hasattr(response, "content") else ""
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
    def factory(model: _PermittedModelType = "gpt-4o-mini") -> "LLMClient":
        return LLMClient(model=model)

