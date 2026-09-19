"""Asks the utility model what one page of messages contains.

The whole point of scanning a page at a time is that the accumulated results
are *never* sent back to the model: every call is the same size whether the
scan has found three restaurants or three hundred. So the model is asked only
"what is on this page", and merging and dedup happen in code
(bot/domain/scan/scan_service.py).

The model proposes a short `key` per item, which is what dedup compares.
`normalize_key` does the actual normalizing in code, because a model asked for
"joes pizza" twice will happily write "Joe's Pizza" the second time.

The model also returns the *index* of the message an item came from rather than
a link: the jump URL and attachment URLs are filled in from the page in code,
so a hallucinated URL can never reach the results.
"""

import json
import re
from typing import Awaitable, Callable, Dict, List, Optional

from bot.domain.scan.models import ScanItem, ScanMessage


class ExtractError(Exception):
    """The model could not be reached or returned something unreadable."""


# (system prompt, user prompt) -> raw model reply
LLMCall = Callable[[str, str], Awaitable[str]]

# Longer messages are almost always conversation; the tail rarely carries the
# thing being collected and a page of 100 full-length posts is a big prompt.
MAX_MESSAGE_CHARS = 600
MAX_KEY_CHARS = 80
MAX_TEXT_CHARS = 300

SYSTEM_PROMPT = (
    "You are scanning a Discord channel's history one page at a time, "
    "collecting whatever the instruction asks for. You see only this page: "
    "never mention earlier pages, and never invent messages. Return only JSON."
)

_WHITESPACE = re.compile(r"\s+")


def normalize_key(raw: str) -> str:
    """Lowercase, collapse whitespace, trim punctuation. '' if nothing is left."""
    key = _WHITESPACE.sub(" ", str(raw or "")).strip().lower()
    key = key.strip(" \t\r\n.,;:!?'\"`*_-()[]{}")
    return key[:MAX_KEY_CHARS].strip()


def extract_json(reply: str) -> str:
    """The first balanced {...} in the reply, unwrapping a ``` fence."""
    text = (reply or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    start = text.find("{")
    if start < 0:
        raise ExtractError("No JSON in model reply: %r" % (reply or "")[:200])
    depth = 0
    in_string = escaped = False
    for i in range(start, len(text)):
        char = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    raise ExtractError("Unterminated JSON in model reply: %r" % text[start:start + 200])


def _render(index: int, message: ScanMessage) -> str:
    content = _WHITESPACE.sub(" ", message.content or "").strip()
    if len(content) > MAX_MESSAGE_CHARS:
        content = content[:MAX_MESSAGE_CHARS] + "…"
    line = "%d. [%s] %s: %s" % (index, message.timestamp, message.author_name, content)
    for url in message.attachment_urls:
        line += "\n   [attachment: %s]" % url
    return line


def build_prompt(instruction: str, messages: List[ScanMessage]) -> str:
    lines = [_render(i, m) for i, m in enumerate(messages, start=1)]
    return (
        "Instruction: %s\n\n"
        "Here is one page of messages from the channel, newest first:\n\n%s\n\n"
        "List every item on THIS page that the instruction asks for. Judge each "
        "message on its own; there is no earlier context and you will never see "
        "this page again.\n"
        "- \"index\" is the number of the message the item came from. Use only "
        "the numbers above; skip anything you cannot point at.\n"
        "- \"key\" is a short lowercase name for the item, three or four words "
        "at most, that you would write the same way if the same item came up on "
        "another page (a restaurant's name, a person's name, a file name). It is "
        "used to recognise duplicates, so leave out dates, counts and wording "
        "from the message.\n"
        "- \"text\" is one short line describing the item, including anything the "
        "message said about it that the instruction cares about.\n"
        "Leave out anything the instruction did not ask for, and do not repeat an "
        "item that appears twice on this page.\n\n"
        "Reply as JSON: {\"items\": [{\"index\": <n>, \"key\": \"<short name>\", "
        "\"text\": \"<one line>\"}]}. Use an empty list if this page has nothing."
        % (instruction.strip(), "\n".join(lines) or "(no messages)")
    )


def parse_reply(reply: str, messages: List[ScanMessage]) -> List[ScanItem]:
    """Items from the model's reply, linked back to their messages.

    Raises ExtractError only when the reply is unusable as a whole. Individual
    bad entries are dropped: a model that gets one of fifty items wrong should
    not cost the page.
    """
    try:
        data = json.loads(extract_json(reply))
    except json.JSONDecodeError as e:
        raise ExtractError("Bad JSON in model reply: %s" % e)
    if not isinstance(data, dict):
        raise ExtractError("Model reply was not a JSON object")
    raw_items = data.get("items")
    if raw_items is None:
        raise ExtractError("Model reply has no items list")
    if not isinstance(raw_items, list):
        raise ExtractError("Model reply's items was not a list")

    by_index: Dict[int, ScanMessage] = dict(enumerate(messages, start=1))
    items: List[ScanItem] = []
    seen = set()
    for entry in raw_items:
        if not isinstance(entry, dict):
            continue
        try:
            index = int(entry.get("index"))
        except (TypeError, ValueError):
            continue
        message = by_index.get(index)
        if message is None:
            continue
        key = normalize_key(entry.get("key") or entry.get("text") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        text = _WHITESPACE.sub(" ", str(entry.get("text") or "")).strip()
        items.append(ScanItem(
            key=key,
            text=text[:MAX_TEXT_CHARS] or key,
            source_url=message.jump_url,
            image_urls=tuple(message.attachment_urls),
        ))
    return items


async def extract_items(
    instruction: str, messages: List[ScanMessage], llm: LLMCall
) -> List[ScanItem]:
    """The new items on one page. Raises ExtractError; the caller decides."""
    if not messages:
        return []
    prompt = build_prompt(instruction, messages)
    try:
        reply = await llm(SYSTEM_PROMPT, prompt)
    except ExtractError:
        raise
    except Exception as e:
        raise ExtractError("Model call failed: %s" % e)
    return parse_reply(reply, messages)


def make_extractor(
    instruction: str, llm: Optional[LLMCall] = None
) -> Callable[[List[ScanMessage]], Awaitable[List[ScanItem]]]:
    """A one-argument page extractor, which is what run_scan wants."""
    call = llm or openai_llm

    async def extract(messages: List[ScanMessage]) -> List[ScanItem]:
        return await extract_items(instruction, messages, call)

    return extract


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
        raise ExtractError(reply)
    return reply
