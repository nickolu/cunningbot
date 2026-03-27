"""Lightweight intent classifier for the channel agent.

Determines whether the bot should respond to a message based on
conversational context. Uses a cheap/fast LLM call to classify intent
as RESPOND, IGNORE, or ASK_CLARIFY.
"""

from enum import Enum
from typing import Dict, List, Optional

from openai import AsyncOpenAI

from bot.app.utils.logger import get_logger

logger = get_logger()

openai_client = AsyncOpenAI()

CLASSIFIER_MODEL = "gpt-4o-mini"


class Intent(str, Enum):
    RESPOND = "RESPOND"
    IGNORE = "IGNORE"
    ASK_CLARIFY = "ASK_CLARIFY"


CLASSIFIER_SYSTEM_PROMPT = """\
You are an intent classifier for a Discord bot named "{bot_name}".

Given the recent conversation history and the latest message, decide whether \
the bot is being addressed and should respond.

Output EXACTLY one of: RESPOND, IGNORE, ASK_CLARIFY

Strong signals to RESPOND:
- Direct address: "{bot_name}...", "bot...", "can you...", "hey bot"
- Imperatives aimed at an assistant: "do X", "give me...", "summarize...", "write...", "make...", "generate...", "show...", "explain..."
- Follow-ups referencing bot output: "that one you made", "redo it but...", "make it more..."
- Questions clearly aimed at an AI: "what do you think...", "do you know..."
- The message is a direct reply to the bot

Strong signals to IGNORE:
- Third-person/meta chat about the bot: "the bot should...", "it would be cool if..."
- Conversations between humans: names/handles directed at other users
- Short reactions with no ask: "lol", "same", "lmao", "nice", "rip"
- Messages with no question or request
- Discussions about unrelated topics between users

ASK_CLARIFY (use sparingly):
- Ambiguous follow-ups right after bot spoke: "that's wild", "okay", "hmm", "interesting"
- "Can it do X?" — could be discussion or a request

Default to IGNORE unless you are confident the bot is being addressed. \
It is much worse to respond when not addressed than to miss a message.\
"""


async def classify_intent(
    latest_message: str,
    recent_history: List[Dict[str, str]],
    bot_name: str,
    is_reply_to_bot: bool = False,
) -> Intent:
    """Classify whether the bot should respond to the latest message.

    Args:
        latest_message: The message text to classify.
        recent_history: Last few messages for context (role + content + name).
        bot_name: The bot's display name in Discord.
        is_reply_to_bot: Whether the message is a direct reply to the bot.

    Returns:
        Intent enum value.
    """
    # Build a compact context string from recent history
    context_lines = []
    for msg in recent_history[-6:]:  # Last 6 messages for context
        name = msg.get("name", "Unknown")
        role = msg.get("role", "user")
        prefix = f"[BOT] {name}" if role == "assistant" else name
        context_lines.append(f"{prefix}: {msg.get('content', '')[:200]}")

    context_block = "\n".join(context_lines) if context_lines else "(no prior context)"

    user_prompt = (
        f"Recent conversation:\n{context_block}\n\n"
        f"Latest message: {latest_message}\n"
        f"Is reply to bot: {is_reply_to_bot}\n\n"
        f"Classification:"
    )

    try:
        response = await openai_client.chat.completions.create(
            model=CLASSIFIER_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": CLASSIFIER_SYSTEM_PROMPT.format(bot_name=bot_name),
                },
                {"role": "user", "content": user_prompt},
            ],
            max_completion_tokens=10,
            temperature=0,
        )

        raw = response.choices[0].message.content.strip().upper()

        if "RESPOND" in raw:
            return Intent.RESPOND
        if "ASK_CLARIFY" in raw or "CLARIFY" in raw:
            return Intent.ASK_CLARIFY
        return Intent.IGNORE

    except Exception as e:
        logger.error(f"Intent classifier failed: {e}")
        # On failure, default to IGNORE to avoid being annoying
        return Intent.IGNORE
