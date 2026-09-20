"""The plain data a scan works with.

Nothing here knows about discord.py: the runner flattens a discord.Message
into a ScanMessage and the loop never sees anything else.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class ScanMessage:
    """One message from the channel, as the extractor sees it."""

    id: int
    author_name: str
    author_id: str
    timestamp: str                       # "YYYY-MM-DD HH:MM", UTC
    content: str
    attachment_urls: Tuple[str, ...] = ()
    jump_url: str = ""


@dataclass(frozen=True)
class ScanPage:
    """One history request's worth of messages.

    `messages` are the ones worth extracting from (the runner drops the bot's
    own posts). `oldest_id` and `read` cover *every* message the request
    returned, including the dropped ones — otherwise a page that happens to be
    all bot posts would look like the end of the channel and stop the scan.
    """

    messages: List[ScanMessage] = field(default_factory=list)
    oldest_id: Optional[int] = None
    read: int = 0

    @property
    def exhausted(self) -> bool:
        """True when the history request came back empty: nothing older left."""
        return self.read == 0


@dataclass(frozen=True)
class ScanItem:
    """One thing the scan found. `key` is what dedup happens on."""

    key: str
    text: str
    source_url: str = ""
    image_urls: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "text": self.text,
            "source_url": self.source_url,
            "image_urls": list(self.image_urls),
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "ScanItem":
        return ScanItem(
            key=str(data.get("key") or ""),
            text=str(data.get("text") or ""),
            source_url=str(data.get("source_url") or ""),
            image_urls=tuple(data.get("image_urls") or ()),
        )
