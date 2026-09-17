"""Reads a Framed result out of a chat message without an LLM.

Three shapes are recognized here:

- The share text framed.wtf copies to the clipboard:
      Framed #1650
      🎥 🟥 🟩 ⬛ ⬛ ⬛ ⬛
  The score is the position of the 🟩; no 🟩 is a miss. Only the daily game
  counts: the side games share as "Framed - One Frame Challenge #N" and so on.
- A bare score: "3", "3/6", "X", "X/6".
- A word everyone uses the same way: "three", "nada", "fail".

Anything else returns None, and the sync service decides whether to ask the
LLM about it.
"""

import re
from dataclasses import dataclass
from typing import Optional

from bot.domain.framed.puzzle import FAIL, MAX_GUESSES

# Discord keeps these as the literal emoji, sometimes with a variation selector.
_RED = "\U0001F7E5"
_GREEN = "\U0001F7E9"
_BLACK = "⬛"
_WHITE = "⬜"
_CAMERA = "\U0001F3A5"

_SHARE_HEADER = re.compile(r"(?:^|\n)\s*Framed\s*#\s*(\d{1,5})\b", re.IGNORECASE)
_SQUARES = re.compile("[%s%s%s%s]" % (_RED, _GREEN, _BLACK, _WHITE))

_BARE_SCORE = re.compile(r"^([0-9]|x)(?:\s*/\s*6)?$")

_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "nada": FAIL, "zero": FAIL, "none": FAIL, "fail": FAIL, "failed": FAIL,
    "dnf": FAIL, "missed": FAIL, "x": FAIL,
}


@dataclass(frozen=True)
class ParsedResult:
    score: int                     # 1-6, or FAIL (0)
    puzzle: Optional[int] = None   # set only when the message names one
    source: str = "number"         # "share", "number", "word", "llm", "manual"


def parse_share(text: str) -> Optional[ParsedResult]:
    header = _SHARE_HEADER.search(text or "")
    if not header:
        return None
    rest = text[header.end():]
    camera = rest.find(_CAMERA)
    if camera < 0:
        return None
    # The squares sit on the camera's line.
    line = rest[camera:].split("\n", 1)[0]
    squares = _SQUARES.findall(line)
    if not squares:
        return None
    score = FAIL
    for i, square in enumerate(squares[:MAX_GUESSES]):
        if square == _GREEN:
            score = i + 1
            break
    return ParsedResult(score=score, puzzle=int(header.group(1)), source="share")


def parse_simple(text: str) -> Optional[ParsedResult]:
    cleaned = (text or "").strip().lower()
    cleaned = re.sub(r"[\s!.?]+$", "", cleaned)
    if not cleaned:
        return None

    match = _BARE_SCORE.match(cleaned)
    if match:
        value = match.group(1)
        if value == "x" or value == "0":
            return ParsedResult(score=FAIL)
        number = int(value)
        if 1 <= number <= MAX_GUESSES:
            return ParsedResult(score=number)
        if number == MAX_GUESSES + 1:
            # "7" is what people say when six guesses weren't enough.
            return ParsedResult(score=FAIL)
        return None

    if cleaned in _WORDS:
        return ParsedResult(score=_WORDS[cleaned], source="word")
    return None


def parse_message(text: str) -> Optional[ParsedResult]:
    return parse_share(text) or parse_simple(text)
