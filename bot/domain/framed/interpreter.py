"""Asks the utility model which leftover messages are Framed results.

The rule parser handles share text and bare scores. What reaches this module is
everything else a person posted in the results channel that day: jokes,
chatter, and results written like "got it on the last frame". The whole day
goes in one call, with the messages the rules already understood shown for
context, so "wait no, 4" can be read against the "3" before it.
"""

import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Awaitable, Callable, Dict, List, Optional

from bot.domain.framed.puzzle import FAIL, MAX_GUESSES, format_score, puzzle_for_date


class InterpretError(Exception):
    """The model could not be reached or returned something unreadable."""


@dataclass(frozen=True)
class DayMessage:
    index: int
    author: str
    time: str                     # "HH:MM", local
    text: str
    parsed_score: Optional[int]   # set when the rule parser already read it


# (system prompt, user prompt) -> raw model reply
LLMCall = Callable[[str, str], Awaitable[str]]

SYSTEM_PROMPT = (
    "You read a Discord channel where friends post their results for Framed, "
    "a daily movie-guessing game with 6 guesses. A result is the guess number "
    "they got it on (1-6, lower is better) or a miss (they didn't get it). "
    "Return only JSON."
)


def build_prompt(day: date, messages: List[DayMessage]) -> str:
    lines = []
    for m in messages:
        text = " ".join(m.text.split())
        if m.parsed_score is not None:
            note = "  [already read as %s]" % format_score(m.parsed_score)
        else:
            note = ""
        lines.append("%d. [%s] %s: %s%s" % (m.index, m.time, m.author, text, note))
    return (
        "These are the messages posted on %s (Framed #%d).\n\n%s\n\n"
        "For each message NOT marked [already read], decide whether it reports "
        "the author's own result for today's puzzle. Examples: \"three\" is 3, "
        "\"nada\" or \"didn't get it\" is a miss, \"got it on the last frame\" is 6, "
        "\"actually 4\" corrects an earlier post to 4. Ignore chatter, questions, "
        "reactions to other people, and talk about other games or other days.\n\n"
        "Reply as JSON: {\"results\": [{\"index\": <n>, \"score\": <1-6 or \"X\">}]} "
        "listing only the messages that are results. Use an empty list if none are."
        % (day.isoformat(), puzzle_for_date(day), "\n".join(lines))
    )


def parse_reply(reply: str, candidates: List[int]) -> Dict[int, int]:
    """Map message index -> score from the model's JSON. Raises on garbage."""
    match = re.search(r"\{.*\}", reply or "", re.DOTALL)
    if not match:
        raise InterpretError("No JSON in model reply: %r" % (reply or "")[:200])
    try:
        data = json.loads(match.group())
    except json.JSONDecodeError as e:
        raise InterpretError("Bad JSON in model reply: %s" % e)
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        raise InterpretError("Model reply has no results list")

    allowed = set(candidates)
    scores: Dict[int, int] = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        if index not in allowed:
            continue
        raw = str(item.get("score", "")).strip().upper()
        if raw in ("X", "0", "MISS", "FAIL"):
            scores[index] = FAIL
        elif raw.isdigit() and 1 <= int(raw) <= MAX_GUESSES:
            scores[index] = int(raw)
    return scores


async def interpret_day(
    day: date, messages: List[DayMessage], llm: LLMCall
) -> Dict[int, int]:
    """Scores for the messages the rules didn't read. Raises InterpretError."""
    candidates = [m.index for m in messages if m.parsed_score is None]
    if not candidates:
        return {}
    try:
        reply = await llm(SYSTEM_PROMPT, build_prompt(day, messages))
    except InterpretError:
        raise
    except Exception as e:
        raise InterpretError("Model call failed: %s" % e)
    return parse_reply(reply, candidates)


async def openai_llm(system: str, prompt: str) -> str:
    """The production LLMCall: the utility model through the shared client."""
    from bot.api.openai.chat_completions_client import ChatCompletionsClient
    from bot.domain.llm.models import UTILITY_MODEL

    client = ChatCompletionsClient.factory(UTILITY_MODEL)
    reply = await client.chat([
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ])
    # chat() reports failures as text instead of raising.
    if reply.startswith("There was an error:"):
        raise InterpretError(reply)
    return reply
