"""Reads a Framed result out of a chat message without an LLM.

Three shapes are recognized here:

- The share text framed.wtf copies to the clipboard:
      Framed #1650
      🎥 🟥 🟩 ⬛ ⬛ ⬛ ⬛
  The score is the position of the 🟩; no 🟩 is a miss. Only the daily game
  counts: the side games share as "Framed - One Frame Challenge #N" and so on.
- A bare score: "3", "3/6", "X", "X/6".
- A word everyone uses the same way: "three", "nada", "fail".
- The shorthand this chat uses for a miss: "F", "👎", "nope", "never heard of
  it". These are read separately because they are also what someone posts in
  sympathy for someone else's miss, so `sync_service` ignores one from a player
  who already posted a real score that day.

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

# "6. pathetic", "3 lol", "X — brutal": a score, then a short remark with no
# other number in it. The remark has to be punctuated off or very short, so
# "4 people played today" and "3 of us missed" still go to the LLM.
_LEADING_SCORE = re.compile(r"^([1-6]|x)(?:\s*/\s*6)?([\s.,!:;—–-]+)(\D{1,40})$")

# The same for a miss: "F, never heard of it", "Nope. Couldn't get the name".
_LEADING_MISS = re.compile(
    r"^(f|ff|nope|nada|nah|fail|failed|zilch|negative)([\s.,!:;—–-]+)(\D{1,60})$"
)

_PUNCTUATED = re.compile(r"[.,!:;—–-]")
# "F for Kyle" is sympathy for someone else, not the author's own result.
_ABOUT_SOMEONE_ELSE = re.compile(r"^(for|to|at|@)\b")


def _remark_is_an_aside(separator: str, remark: str) -> bool:
    """A remark after a score only counts when it reads like an aside."""
    if _ABOUT_SOMEONE_ELSE.match(remark):
        return False
    return bool(_PUNCTUATED.search(separator)) or len(remark) <= 8

_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "nada": FAIL, "zero": FAIL, "none": FAIL, "fail": FAIL, "failed": FAIL,
    "dnf": FAIL, "missed": FAIL, "x": FAIL,
}


# How people here say "I didn't get it" without saying a number. Matched on the
# whole message, so "nope, that was brutal" still goes to the LLM.
_MISS_SHORTHAND = {
    "f", "also f", "big f", "mega f", "ff", "rip",
    "nope", "nah", "negative", "no clue", "no idea", "zilch", "nothing",
    "never heard of it", "never heard of", "never seen it", "never seen this",
    "didnt get it", "didn't get it", "no clue at all",
    "\U0001F44E",                      # thumbs down
    "\U0001F937", "\U0001F937\u200d\u2642",   # shrug
    "\U0001F645", "\U0001F645\u200d\u2642",   # no-good gesture
    "\U0001F480",                      # skull
    "\xaf\\_(\u30c4)_/\xaf",
}


@dataclass(frozen=True)
class ParsedResult:
    score: int                     # 1-6, or FAIL (0)
    puzzle: Optional[int] = None   # set only when the message names one
    source: str = "number"         # "share", "number", "word", "shorthand", "llm"
    # Set on an LLM reading that explicitly corrects the author's earlier post
    # ("actually 4"), which is the only way a remark may replace a real score.
    corrects_earlier: bool = False


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

    leading = _LEADING_SCORE.match(cleaned)
    if leading and _remark_is_an_aside(leading.group(2), leading.group(3)):
        value = leading.group(1)
        return ParsedResult(score=FAIL if value == "x" else int(value), source="word")
    return None


def parse_miss_shorthand(text: str) -> Optional[ParsedResult]:
    """A whole message that means "I missed it" in this chat's shorthand."""
    cleaned = (text or "").replace("\ufe0f", "").strip().lower()
    cleaned = re.sub(r"^[|*_~\s]+|[|*_~\s]+$", "", cleaned)
    cleaned = re.sub(r"[\s!.?,]+$", "", cleaned)
    if cleaned in _MISS_SHORTHAND:
        return ParsedResult(score=FAIL, source="shorthand")
    leading = _LEADING_MISS.match(cleaned)
    if leading and _remark_is_an_aside(leading.group(2), leading.group(3)):
        return ParsedResult(score=FAIL, source="shorthand")
    return None


def parse_message(text: str) -> Optional[ParsedResult]:
    return parse_share(text) or parse_simple(text) or parse_miss_shorthand(text)
