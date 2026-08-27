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
#: A page id is "<slug>-<hmac>"; the hmac is _HMAC_CHARS hex characters.
_PAGE_ID_RE = re.compile(r"^(?P<slug>.+)-(?P<digest>[0-9a-f]{16})$")
_HMAC_CHARS = 16
_MAX_SLUG_LEN = 48


class PageIdError(RuntimeError):
    """Raised when page ids cannot be derived (missing secret)."""


def slugify(text: str) -> str:
    """Reduce arbitrary text to a short, URL-safe slug."""
    slug = _SLUG_RE.sub("-", text.strip().lower()).strip("-")
    return slug[:_MAX_SLUG_LEN].strip("-")


def derive_guild_prefix(guild_id: str) -> str:
    """Return the unguessable storage prefix for a guild's uploaded images.

    Hosted images live at ``img/<prefix>/...``. Deriving the prefix the same way
    page ids are derived means one guild cannot enumerate another's uploads even
    if it learns the blob store's hostname, and keeps ``PAGES_ID_SECRET`` on the
    bot host -- the upload service only validates the prefix's shape.
    """
    secret = os.getenv("PAGES_ID_SECRET")
    if not secret:
        raise PageIdError("PAGES_ID_SECRET is not configured")

    return hmac.new(
        secret.encode("utf-8"),
        f"images:{guild_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:_HMAC_CHARS]


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


def looks_like_page_id(value: str) -> bool:
    """True if ``value`` is already a page id rather than a bare slug.

    The agent is as likely to have a URL it published earlier as it is to have
    a slug, and the two must not be confused: deriving an id from a slug that
    is *already* an id produces a different, empty page.
    """
    return bool(_PAGE_ID_RE.match(value.strip().lower()))


def slug_from_page_id(page_id: str) -> str:
    """Recover the readable slug from a page id, for display."""
    match = _PAGE_ID_RE.match(page_id.strip().lower())
    return match.group("slug") if match else page_id
