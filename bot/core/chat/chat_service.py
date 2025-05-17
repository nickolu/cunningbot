
"""
chat_service.py
Service for chat functionality.
"""

from typing import Dict, List, Optional

from bot.services.openai.utils import sanitize_name
from bot.core.llm_client import LLMClient, PermittedModelType, transform_history_to_openai
from bot.core.settings.personality_service import get_personality, set_personality
from bot.core.logger import get_logger
import os
import re

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
        {"role": "user", "content": msg, "name": sanitize_name(name)},
    ]

    try:
        # Use the specified model if provided, otherwise use the default
        current_llm = LLMClient.factory(model=model)
        response = await current_llm.chat(messages)
        
        return response
        
    except Exception as e:
        logger.error({
            "event": "llm_error",
            "error": str(e),
            "model": model,
        })
        raise