"""The free pre-filter in front of the calendar extractor.

Every message in an enabled server passes through here, so this has to be
cheap and generous: it exists to throw away the chat that cannot possibly
name a day, not to decide what is an event. Anything that mentions a month,
a weekday, a relative day ("tomorrow", "next week"), a numeric date or a
clock time goes on to the model, which makes the real call.

False negatives here are silent misses, so the patterns err towards letting
things through. Expect something like one message in ten to pass in an
ordinary channel.
"""

import re

# Short messages are reactions, not plans. "7pm?" is 4 characters, so keep
# this low.
MIN_CHARS = 4

_MONTHS = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
_WEEKDAYS = (
    r"mon(?:day)?|tue(?:s(?:day)?)?|wed(?:nesday)?|thu(?:rs?(?:day)?)?|"
    r"fri(?:day)?|sat(?:urday)?|sun(?:day)?"
)
_RELATIVE = (
    r"tomorrow|tonight|this (?:weekend|week|evening|afternoon|morning)|"
    r"next (?:week|weekend|month|" + _WEEKDAYS + r")|"
    r"(?:in|after) \d+ (?:days?|weeks?)|"
    # "on the 14th", "the 28th works" -- but not "the 3rd season" or "the 1st boss".
    r"\b(?:on|by|until|till|the)\s+\d{1,2}(?:st|nd|rd|th)\b"
    r"(?!\s+(?:time|season|one|boss|place|floor|round|level|try|attempt|year|grade|"
    r"episode|ep|chapter|book|game|match|quarter|half|period|day of|and|or))"
)
_NUMERIC_DATE = r"\b\d{1,2}[/.-]\d{1,2}(?:[/.-]\d{2,4})?\b|\b\d{4}-\d{2}-\d{2}\b"
# "7pm", "7:30", and a bare "at 8" (raid's at 8), which carries no am/pm.
_CLOCK = (
    r"\b\d{1,2}(?::\d{2})?\s?(?:am|pm|a\.m\.|p\.m\.)\b|\b\d{1,2}:\d{2}\b"
    r"|\bat\s+\d{1,2}\b(?![:/.-]?\d)"
)

DATE_HINT = re.compile(
    r"(?<![a-z])(?:" + _MONTHS + r")(?![a-z])"
    r"|(?<![a-z])(?:" + _WEEKDAYS + r")(?![a-z])"
    r"|" + _RELATIVE +
    r"|" + _NUMERIC_DATE +
    r"|" + _CLOCK,
    re.IGNORECASE,
)

# A message that is only a link (or several) is a share, not a plan.
_URL_ONLY = re.compile(r"^(?:\s*https?://\S+\s*)+$", re.IGNORECASE)

# Month and weekday abbreviations that are ordinary words far more often than
# dates. On their own they do not count; they do with a date-shaped neighbour
# ("this sat", "sat night", "may 3") or alongside any other hit.
_AMBIGUOUS = {"may", "sat", "sun", "mar", "march"}
_AMBIGUOUS_IN_CONTEXT = re.compile(
    r"\b(?:on|this|next|every|by|until|till|til|for)\s+(?:sat|sun|may|mar|march)\b"
    r"|\b(?:sat|sun|may|mar|march)\b\s+(?:\d|the\s|night|morning|evening|afternoon|at\s)",
    re.IGNORECASE,
)


def looks_like_date(text: str) -> bool:
    """True if the text might name a day or time. Cheap; no model call."""
    content = (text or "").strip()
    if len(content) < MIN_CHARS:
        return False
    if _URL_ONLY.match(content):
        return False
    hits = [m.group(0) for m in DATE_HINT.finditer(content)]
    if not hits:
        return False
    if any(h.lower() not in _AMBIGUOUS for h in hits):
        return True
    return bool(_AMBIGUOUS_IN_CONTEXT.search(content))
