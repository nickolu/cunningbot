"""User uploads: opening upload links and finding what came through them.

The bot cannot take a file *from* Discord the way it posts one -- attachments
expire, and nothing in Discord lets a user hand the bot a file for keeps. So
instead the bot opens an **upload session** on the pages service and hands
back a link. The page behind the link takes files for fifteen minutes with no
login: the link is the capability, and whoever it was posted to is the
audience. Files land in a private storage bucket, get a stable download URL,
and are recorded in a per-guild catalog the bot can search.

Everything guild-scoped happens here. The service (``web/``) enforces the
limits; this module only chooses them and formats what comes back.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from bot.api.pages.client import PagesClient
from bot.app.utils.logger import get_logger

logger = get_logger()

#: How long a file is kept, in days. Each value is a lifecycle tier on the
#: bucket (see ``web/README.md``); the service refuses anything else.
TTL_CHOICES: Tuple[int, ...] = (1, 7, 30)
DEFAULT_TTL_DAYS = 7

KIND_IMAGES = "images"
KIND_ANY = "any"
KINDS: Tuple[str, ...] = (KIND_IMAGES, KIND_ANY)

DEFAULT_MAX_FILES = 10
MAX_FILES_CAP = 25
DEFAULT_MAX_BYTES = 500 * 1024 * 1024
MAX_BYTES_CAP = 500 * 1024 * 1024

#: An upload link stays open this long. Mirrors SESSION_OPEN_SECONDS in
#: ``web/api/_files_lib.js``; the service's ``expires_at`` is authoritative.
LINK_OPEN_MINUTES = 15


@dataclass(frozen=True)
class UploadLink:
    session_id: str
    url: str
    expires_at: str
    max_files: int
    max_bytes: int
    ttl_days: int
    kind: str


def clamp_ttl(ttl_days: Optional[int]) -> int:
    """The nearest tier at or above ``ttl_days`` (defaults to a week)."""
    if ttl_days is None:
        return DEFAULT_TTL_DAYS
    try:
        wanted = int(ttl_days)
    except (TypeError, ValueError):
        return DEFAULT_TTL_DAYS
    for tier in TTL_CHOICES:
        if wanted <= tier:
            return tier
    return TTL_CHOICES[-1]


def clamp_max_files(max_files: Optional[int]) -> int:
    try:
        n = int(max_files) if max_files is not None else DEFAULT_MAX_FILES
    except (TypeError, ValueError):
        n = DEFAULT_MAX_FILES
    return max(1, min(MAX_FILES_CAP, n))


def normalize_kind(kind: Optional[str]) -> str:
    return KIND_IMAGES if (kind or "").strip().lower() == KIND_IMAGES else KIND_ANY


async def open_upload_link(
    guild_id: str,
    channel_id: Optional[str] = None,
    channel_name: Optional[str] = None,
    requester_id: Optional[str] = None,
    requester_name: Optional[str] = None,
    label: Optional[str] = None,
    kind: Optional[str] = None,
    max_files: Optional[int] = None,
    ttl_days: Optional[int] = None,
) -> UploadLink:
    """Open an upload session and return the link to share.

    Raises ``EnvironmentError`` if the pages service isn't configured,
    ``UploadsNotConfigured`` if it is but has no bucket yet,
    ``PagesNotDeployed`` if it predates uploads, and ``RuntimeError`` for
    anything else it refuses.
    """
    client = PagesClient()
    result = await client.create_upload_session(
        guild_id=guild_id,
        channel_id=channel_id,
        channel_name=channel_name,
        requester_id=requester_id,
        requester_name=requester_name,
        label=(label or "").strip()[:120] or None,
        kind=normalize_kind(kind),
        max_files=clamp_max_files(max_files),
        max_bytes=DEFAULT_MAX_BYTES,
        ttl_days=clamp_ttl(ttl_days),
    )

    link = UploadLink(
        session_id=str(result["session_id"]),
        url=str(result["url"]),
        expires_at=str(result.get("expires_at") or ""),
        max_files=int(result.get("max_files") or DEFAULT_MAX_FILES),
        max_bytes=int(result.get("max_bytes") or DEFAULT_MAX_BYTES),
        ttl_days=int(result.get("ttl_days") or DEFAULT_TTL_DAYS),
        kind=str(result.get("kind") or KIND_ANY),
    )

    logger.info({
        "event": "upload_link_opened",
        "guild_id": guild_id,
        "channel_id": channel_id,
        "requester_id": requester_id,
        "session_id": link.session_id,
        "kind": link.kind,
        "max_files": link.max_files,
        "ttl_days": link.ttl_days,
    })
    return link


async def read_upload_session(
    guild_id: str, session_id: str
) -> Optional[Dict[str, Any]]:
    """The session and the files it has received: ``{"session": ..., "files": [...]}``.

    None once the service has forgotten it (an hour after it was opened).
    """
    client = PagesClient()
    return await client.get_upload_session(session_id, guild_id)


async def search_files(
    guild_id: str, query: str = "", limit: int = 25
) -> Tuple[List[Dict[str, Any]], int]:
    """Files uploaded for this guild, newest first, filtered by ``query``.

    Returns ``(files, total_matching)``; ``files`` is at most ``limit`` long.
    """
    client = PagesClient()
    data = await client.list_files(guild_id, query=query, limit=limit)
    files = data.get("files")
    total = data.get("total")
    files = files if isinstance(files, list) else []
    return files, int(total) if isinstance(total, int) else len(files)


async def delete_file(guild_id: str, file_id: str) -> bool:
    """Remove one file. False if there was no such file in this guild."""
    client = PagesClient()
    deleted = await client.delete_file(guild_id, file_id)
    if deleted:
        logger.info({"event": "file_deleted", "guild_id": guild_id, "file_id": file_id})
    return deleted


def format_bytes(n: Any) -> str:
    try:
        size = float(n)
    except (TypeError, ValueError):
        return ""
    if size < 1024:
        return f"{int(size)} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} KB"
    if size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{size / (1024 * 1024 * 1024):.1f} GB"


def describe_file(record: Dict[str, Any]) -> str:
    """One line for a catalog entry, for the agent and ``/files list`` alike."""
    name = record.get("name") or "file"
    parts = [f"**{name}**"]
    size = format_bytes(record.get("bytes"))
    if size:
        parts.append(size)
    who = record.get("uploader_name")
    if who:
        parts.append(f"from {who}")
    when = (record.get("created_at") or "")[:10]
    if when:
        parts.append(when)
    line = " · ".join(parts)
    note = (record.get("note") or "").strip()
    if note:
        line += f" — {note}"
    label = (record.get("label") or "").strip()
    if label and label.lower() not in note.lower():
        line += f" [{label}]"
    url = record.get("url")
    if url:
        line += f"\n  {url}"
    return line


def expires_in_words(expires_at: str, now: Optional[datetime] = None) -> str:
    """'in 14 minutes', 'in about a minute', or 'now' for a session's close."""
    try:
        exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return f"in {LINK_OPEN_MINUTES} minutes"
    now = now or datetime.now(timezone.utc)
    seconds = (exp - now).total_seconds()
    if seconds <= 30:
        return "now"
    minutes = int(round(seconds / 60))
    if minutes <= 1:
        return "in about a minute"
    return f"in {minutes} minutes"
