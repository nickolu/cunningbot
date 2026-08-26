"""Guild-scoped page identifiers.

CunningBot serves multiple Discord servers and published pages live on one
shared public host, so a page's URL is the only thing standing between server A
and server B's data. Discord guild ids are *not* secret — they appear in every
channel URL — so an id like ``restaurants-<guild_id>`` would let anyone who
knows another server's id read its pages.

Instead an id is ``<slug>-<hmac>``, where the hmac is derived from the guild id
and ``PAGES_ID_SECRET``, a secret that never leaves the bot host. That keeps
ids:

- **stable** — the same guild and slug always produce the same URL, so a list
  page can be republished in place as its contents change;
- **unguessable across guilds** — deriving another server's URL requires the
  secret;
- **readable** — the slug survives, so a pasted link still says what it is.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from typing import Optional

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_HMAC_CHARS = 16
_MAX_SLUG_LEN = 48


class PageIdError(RuntimeError):
    """Raised when page ids cannot be derived (missing secret)."""


def slugify(text: str) -> str:
    """Reduce arbitrary text to a short, URL-safe slug."""
    slug = _SLUG_RE.sub("-", text.strip().lower()).strip("-")
    return slug[:_MAX_SLUG_LEN].strip("-")


def derive_page_id(guild_id: str, slug: Optional[str] = None) -> str:
    """Return the public page id for ``slug`` within ``guild_id``.

    A falsy ``slug`` produces a one-off page: a random slug is generated, so
    each publish gets its own URL (used for reasoning traces and other
    snapshots that should not overwrite each other).
    """
    secret = os.getenv("PAGES_ID_SECRET")
    if not secret:
        raise PageIdError("PAGES_ID_SECRET is not configured")

    clean = slugify(slug) if slug else ""
    if not clean:
        clean = secrets.token_hex(4)

    digest = hmac.new(
        secret.encode("utf-8"),
        f"{guild_id}:{clean}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:_HMAC_CHARS]

    return f"{clean}-{digest}"
