"""Suggested replies the agent offers at the end of a reply.

The `suggest_replies` tool runs inside `run_agent`, but the buttons belong on
the reply message, which the caller posts after `run_agent` returns. This module
carries the options between the two without changing `run_agent`'s return type.

The caller opens a box with `collect_suggestions()` around the run; the tool
puts its options in whichever box is open. A `ContextVar` rather than a dict
keyed by channel: the box belongs to the task running the agent, so a failed
run can't leave options behind for the next one, and a caller that never opens a
box (one that can't show buttons) makes the tool say so instead of pretending.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator, List, Optional

MIN_OPTIONS = 2
MAX_OPTIONS = 5
# Discord's limit on a button label.
MAX_OPTION_CHARS = 80

_current: ContextVar[Optional[List[str]]] = ContextVar("agent_suggestions", default=None)


@contextmanager
def collect_suggestions() -> Iterator[List[str]]:
    """Open a box for the run's suggestions; read it after the run."""
    box: List[str] = []
    token = _current.set(box)
    try:
        yield box
    finally:
        _current.reset(token)


def offer_suggestions(options: List[str]) -> bool:
    """Replace the open box's options. False if no caller is collecting them."""
    box = _current.get()
    if box is None:
        return False
    box[:] = options
    return True


def clean_options(raw: object) -> List[str]:
    """Trimmed, non-empty, de-duplicated options, in the order given."""
    if not isinstance(raw, list):
        return []
    seen = set()
    options: List[str] = []
    for item in raw:
        text = " ".join(str(item or "").split())
        if text and text.lower() not in seen:
            seen.add(text.lower())
            options.append(text)
    return options
