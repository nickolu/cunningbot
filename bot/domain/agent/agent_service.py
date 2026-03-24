"""Agent service — orchestrates the OpenAI tool-calling loop.

Given a channel's message history and agent configuration, this service:
1. Builds the message payload (system prompt + history + tools)
2. Calls OpenAI with tool definitions
3. Executes any tool calls the model requests
4. Loops until the model produces a final text response
"""

import json
from typing import Any, Dict, List, Optional

import discord
from openai import AsyncOpenAI

from bot.domain.agent.agent_tools import (
    TOOL_EXECUTORS,
    CHANNEL_AWARE_TOOLS,
    get_tool_schemas_for_config,
)
from bot.domain.chat.chat_personas import CHAT_PERSONAS
from bot.app.app_state import get_default_persona
from bot.app.utils.logger import get_logger

logger = get_logger()

openai_client = AsyncOpenAI()

MAX_TOOL_ROUNDS = 5  # Safety limit on tool-calling iterations

AGENT_SYSTEM_PROMPT = """\
You are an AI agent in a Discord channel. You can see the recent conversation \
history and should respond naturally as a participant.

You have access to tools — use them when the conversation calls for it. For example:
- If someone mentions the weather, look it up with get_weather.
- If someone asks for an image, generate one with generate_image.
- If someone asks to edit an image from the chat, use edit_image with the image URL from the [Image: filename | URL] annotations in the conversation. Available models: gemini-2.5-flash (default, fast/cheap), gemini-3-pro (higher quality), gpt-image-1 (OpenAI). Use what the user asks for, or default to gemini-2.5-flash.
- If someone wants to roll dice, use roll_dice.
- If someone wants a GIF, use search_gifs.

Guidelines:
- Be conversational and concise — this is Discord, not an essay.
- Use tools proactively when they'd add value to the conversation.
- You can chain tools (e.g. get weather, then generate an image based on it).
- When referencing tool results, summarize naturally instead of dumping raw data.
- Don't mention "tools" or "function calls" — just do the thing.
- If you can't help, say so briefly.
{persona_block}\
"""


def _build_system_prompt(
    persona_key: Optional[str], guild_id: Optional[int]
) -> str:
    """Build the system prompt, optionally injecting persona instructions."""
    persona_block = ""

    # Resolve persona
    resolved_key = persona_key
    if not resolved_key and guild_id is not None:
        resolved_key = get_default_persona(guild_id)

    if resolved_key and resolved_key in CHAT_PERSONAS:
        persona_data = CHAT_PERSONAS[resolved_key]
        instructions = persona_data.get("instructions") or persona_data.get("personality", "")
        if instructions:
            persona_block = f"\nYour personality: {instructions}\n"

    return AGENT_SYSTEM_PROMPT.format(persona_block=persona_block)


def _build_history_messages(
    history: List[Dict[str, str]],
) -> List[Dict[str, Any]]:
    """Convert flattened Discord history into OpenAI message format."""
    messages: List[Dict[str, Any]] = []
    for msg in history:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        name = msg.get("name")
        entry: Dict[str, Any] = {"role": role, "content": content}
        if name:
            entry["name"] = name
        messages.append(entry)
    return messages


async def run_agent(
    channel: discord.TextChannel,
    history: List[Dict[str, str]],
    agent_config: Dict[str, Any],
    guild_id: Optional[int] = None,
) -> Optional[str]:
    """Run the agent loop and return the final text response (or None if empty).

    Images and other rich content are sent directly to the channel by tool
    executors, so the returned string is just the conversational text part.
    """
    model: str = agent_config.get("model", "gpt-4o")
    enabled_tools: List[str] = agent_config.get("tools", [])
    persona: Optional[str] = agent_config.get("persona")

    system_prompt = _build_system_prompt(persona, guild_id)
    tool_schemas = get_tool_schemas_for_config(enabled_tools)

    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        *_build_history_messages(history),
    ]

    for round_num in range(MAX_TOOL_ROUNDS):
        try:
            kwargs: Dict[str, Any] = {
                "model": model,
                "messages": messages,
                "max_completion_tokens": 4096,
            }
            if tool_schemas:
                kwargs["tools"] = tool_schemas
                kwargs["tool_choice"] = "auto"

            response = await openai_client.chat.completions.create(**kwargs)
        except Exception as e:
            logger.error(f"Agent LLM call failed (round {round_num}): {e}")
            return f"Sorry, I ran into an error: {e}"

        choice = response.choices[0]
        assistant_message = choice.message

        # Append assistant message to running context
        msg_dict: Dict[str, Any] = {"role": "assistant"}
        if assistant_message.content:
            msg_dict["content"] = assistant_message.content
        if assistant_message.tool_calls:
            msg_dict["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in assistant_message.tool_calls
            ]
        messages.append(msg_dict)

        # If no tool calls, we're done
        if not assistant_message.tool_calls:
            return assistant_message.content

        # Execute each tool call
        for tool_call in assistant_message.tool_calls:
            fn_name = tool_call.function.name
            try:
                fn_args = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                fn_args = {}

            logger.info({
                "event": "agent_tool_call",
                "tool": fn_name,
                "args": fn_args,
                "round": round_num,
            })

            executor = TOOL_EXECUTORS.get(fn_name)
            if executor is None:
                result = f"Unknown tool: {fn_name}"
            else:
                try:
                    if fn_name in CHANNEL_AWARE_TOOLS:
                        result = await executor(fn_args, channel)
                    else:
                        result = await executor(fn_args)
                except Exception as e:
                    logger.error(f"Tool '{fn_name}' failed: {e}")
                    result = f"Tool error: {e}"

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": str(result),
            })

    # If we exhausted rounds, return whatever content we have
    logger.warning("Agent hit MAX_TOOL_ROUNDS limit")
    return assistant_message.content if assistant_message.content else "I got a bit carried away with tools there. Let me know if you need anything else."
