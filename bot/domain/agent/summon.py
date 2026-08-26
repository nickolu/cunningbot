"""Name-based summoning for the channel agent.

The agent has always woken up on an @mention or a reply.  People also
address the bot the way they address each other — by name ("cunningbot,
what's the weather?") — so this module recognises the bot's name in plain
message text.

Kept free of discord.py: callers pass the names the bot answers to
(username, per-guild nickname) and get back a plain bool.
"""

import re
from typing import Iterable, List, Optional, Pattern

# Names the bot answers to on top of its Discord username / nickname.
# Deliberately narrow — the intent classifier treats loose aliases like
# "bot" and "agent" as a signal, but they are far too common to hard-gate on.
EXTRA_ALIASES = ("cunningbot", "cunning bot", "manbot")

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _variants(name: str) -> List[str]:
    """Return the spellings of ``name`` worth matching.

    "CunningBot" is also written "cunning bot", so CamelCase names get a
    spaced variant alongside the original.
    """
    name = name.strip()
    if not name:
        return []
    variants = [name]
    spaced = _CAMEL_BOUNDARY.sub(" ", name)
    if spaced.lower() != name.lower():
        variants.append(spaced)
    return variants


def build_summon_pattern(names: Iterable[str]) -> Optional[Pattern]:
    """Compile a word-boundary pattern matching any of ``names`` or an alias.

    Returns None if there is nothing to match on.
    """
    alternatives: List[str] = []
    seen = set()
    for name in list(names) + list(EXTRA_ALIASES):
        for variant in _variants(name or ""):
            key = variant.lower()
            if key in seen:
                continue
            seen.add(key)
            alternatives.append(re.escape(variant))

    if not alternatives:
        return None

    # Longest first so "cunning bot" wins over a bare "bot"-style alias.
    alternatives.sort(key=len, reverse=True)
    return re.compile(
        r"(?<![\w-])(?:" + "|".join(alternatives) + r")(?![\w-])",
        re.IGNORECASE,
    )


def is_summoned_by_name(content: str, pattern: Optional[Pattern]) -> bool:
    """True if ``content`` addresses the bot by one of its names."""
    if not content or pattern is None:
        return False
    return pattern.search(content) is not None
