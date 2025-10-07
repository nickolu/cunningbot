
"""
chat_service.py
Service for chat functionality.
"""

from typing import Dict, List, Optional

from bot.api.openai.utils import sanitize_name
from bot.api.openai.chat_completions_client import ChatCompletionsClient, PermittedModelType
from bot.api.anthropic.chat_completions_client import AnthropicChatCompletionsClient
from bot.app.utils.logger import get_logger

logger = get_logger()


async def chat_service(
    msg: str,
    model: Optional[PermittedModelType] = None,
    name: Optional[str] = None,
    personality: Optional[str] = None,
    history: Optional[List[Dict[str, str]]] = None,
) -> str:   
    
    if model is None:
        model = "gpt-4o-mini"

    if name is None:
        name = "User"

    system_prompt_parts = ["You are a helpful AI assistant."]
    if personality:
        system_prompt_parts.append(f"Your current personality is: '{personality}'. Please act accordingly.")
    
    system_prompt = " ".join(system_prompt_parts)

    messages = [
        {"role": "system", "content": system_prompt},
        *(history or []),
    ]
    if msg:
        messages.append({"role": "user", "content": msg, "name": sanitize_name(name)})

    try:
        # Determine which client to use based on the model
        provider = ChatCompletionsClient.PERMITTED_MODELS.get(model)

        if provider == "anthropic":
            current_llm = AnthropicChatCompletionsClient.factory(model=model)
        else:
            current_llm = ChatCompletionsClient.factory(model=model)

        response = await current_llm.chat(messages)

        return response

    except Exception as e:
        logger.error({
            "event": "llm_error",
            "error": str(e),
            "model": model,
        })
        raise