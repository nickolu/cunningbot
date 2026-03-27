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
the bot should respond.

Messages from the bot are prefixed with [BOT] in the conversation history.

Output EXACTLY one of: RESPOND, IGNORE, ASK_CLARIFY

RESPOND when:
- The bot is addressed by name or alias: "{bot_name}", "bot", "agent", "manbot", "cunningbot" (e.g. "bot, what's the weather?", "hey manbot")
- A request or command is made: "do X", "give me...", "make...", "generate...", "show...", "explain...", "search for..."
- A question is asked that an AI could answer: "what is...", "how do...", "why does...", "what do you think..."
- The user is following up on something the bot said: "what about X?", "can you also...", "now do Y", "why?", "try again", "no I meant..."
- The bot was the last to speak and the user sends a question or request of any kind
- The message is a direct reply to the bot
- Someone asks for help, opinions, or information in a channel where the bot is active

IGNORE when:
- Humans are clearly talking to each other (multiple humans going back and forth)
- Short reactions with no ask: "lol", "same", "lmao", "nice", "rip", "gg"
- Third-person meta discussion about the bot: "the bot should...", "it would be cool if the bot..."
- Messages directed at a specific other user by name

ASK_CLARIFY (use rarely):
- Genuinely ambiguous: could be talking to the bot or could be idle chatter, and you really can't tell

Guidelines:
- When the bot was the last to speak, lean toward RESPOND — the user is likely continuing the conversation.
- When in doubt between RESPOND and IGNORE, prefer RESPOND. The bot would rather be helpful than silent.
- Only IGNORE when you're fairly confident the message is not meant for the bot.\
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
    history_slice = recent_history[-8:]  # Last 8 messages for context
    context_lines = []
    bot_spoke_last = False
    for i, msg in enumerate(history_slice):
        name = msg.get("name", "Unknown")
        role = msg.get("role", "user")
        prefix = f"[BOT] {name}" if role == "assistant" else name
        context_lines.append(f"{prefix}: {msg.get('content', '')[:200]}")
        # Track if the very last message before the new one was from the bot
        if i == len(history_slice) - 1 and role == "assistant":
            bot_spoke_last = True

    context_block = "\n".join(context_lines) if context_lines else "(no prior context)"

    user_prompt = (
        f"Recent conversation:\n{context_block}\n\n"
        f"Latest message (from user): {latest_message}\n"
        f"Is reply to bot: {is_reply_to_bot}\n"
        f"Bot was the last to speak before this message: {bot_spoke_last}\n\n"
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
