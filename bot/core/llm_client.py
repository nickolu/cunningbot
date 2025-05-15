"""
llm_client.py
Core LLM client logic for the bot.
"""

from typing import List, Dict, Any, Literal
from langchain_openai import ChatOpenAI
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_core.chat_history import InMemoryChatMessageHistory

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
        self.llm: ChatOpenAI  # type annotation for mypy
        self.runnable: RunnableWithMessageHistory  # type annotation for mypy
        self._history_store: Dict[str, InMemoryChatMessageHistory] = {}

        if not self.provider:
            raise ValueError(f"Unsupported or unknown model: {model}")
        if self.provider == "openai":
            self.llm = ChatOpenAI(model=model)
            def get_session_history(session_id: str) -> InMemoryChatMessageHistory:
                if session_id not in self._history_store:
                    self._history_store[session_id] = InMemoryChatMessageHistory()
                return self._history_store[session_id]
            self.runnable = RunnableWithMessageHistory(
                self.llm,
                get_session_history,
                input_messages_key="messages",
                history_messages_key="history"
            )
        else:
            raise ValueError(f"Unsupported provider: {self.provider}")

    async def chat(self, history: List[Dict[str, Any]], session_id: str = "default") -> str:
        """
        Chat with the LLM model, maintaining message history per session.
        Args:
            history: List of message dicts with 'role' and 'content'.
            session_id: Unique identifier for the chat session.
        Returns:
            The assistant's reply as a string.
        """
        if self.provider == "openai":
            response = await self.runnable.ainvoke(
                {"messages": history},
                config={"configurable": {"session_id": session_id}}
            )
            print("Response: ", response)
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
    def factory(model: _PermittedModelType = "gpt-4o-mini") -> "LLMClient":
        return LLMClient(model=model)

