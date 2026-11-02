"""Who is allowed to start a channel history scan.

A scan reads an entire channel and pays for a model call per page, so it is not
something any member of a server can set running from a chat message. Two ways
in, checked in this order:

1. ``SCAN_ALLOWED_USER_IDS`` — a comma-separated allowlist of Discord user ids.
   Set it and only those accounts can start a scan, in any server the bot is in.
2. Nothing set — the Discord application owner, via ``bot.is_owner(user)``.

The allowlist deliberately lives in the environment: user ids are personal data
and this repository is public, so none is ever committed. ``.env.example``
documents the key with no value.

``is_owner`` needs the client, which an agent tool executor never sees, so the
scan listener cog hands it over when it loads (`set_scan_client`). If it has not
been handed over — a test, or a process with no cogs — the fallback refuses
rather than letting everyone through.
"""

import os
from typing import Any, Optional, Set

from bot.app.utils.logger import get_logger

logger = get_logger()

ALLOWED_IDS_ENV = "SCAN_ALLOWED_USER_IDS"

REFUSAL = (
    "Only the bot's owner can start a channel history scan. Tell the user this "
    "is owner-only and that they should ask the bot owner to run it."
)

_client: Optional[Any] = None


def set_scan_client(client: Any) -> None:
    """Remember the running bot so the owner fallback can ask Discord."""
    global _client
    _client = client


def get_scan_client() -> Optional[Any]:
    return _client


def allowed_user_ids() -> Set[int]:
    """The ids in SCAN_ALLOWED_USER_IDS. Empty when unset, empty, or unparsable."""
    raw = os.getenv(ALLOWED_IDS_ENV) or ""
    ids: Set[int] = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            logger.warning(f"{ALLOWED_IDS_ENV} contains a non-numeric entry: {part!r}")
    return ids


async def can_start_scan(user: Any) -> bool:
    """True if `user` may start a scan. Never raises — a failure is a refusal."""
    user_id = getattr(user, "id", None)
    if user_id is None:
        return False

    allowed = allowed_user_ids()
    if allowed:
        return int(user_id) in allowed

    client = _client
    if client is None:
        logger.warning(
            f"{ALLOWED_IDS_ENV} is unset and no client is registered; "
            "refusing to start a scan"
        )
        return False
    try:
        return bool(await client.is_owner(user))
    except Exception as e:
        logger.error(f"Could not check scan ownership: {e}")
        return False
