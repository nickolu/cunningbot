"""What a scan says in the channel: the status message, and the final report.

Everything here is a pure function over the job record and the result items, so
the wording is testable without Discord. `bot/app/scan_ux.py` owns the sending,
the editing and the publishing; it only ever asks this module what to write.

The scan posts three kinds of message:

* a status message when it starts (or when it resumes after a restart), edited
  in place from the throttled progress callback;
* a final report — the items inline when the list is short, otherwise a
  published page and a link;
* a plain sentence back to the model when a scan is already running, or when
  the person asking is not allowed to start one.
"""

from typing import Any, Dict, List, Optional

from bot.domain.pages.page_ids import slugify

# A list longer than this, or wordier than this, goes to a page instead of into
# the channel. Discord's own limit is 2000 characters; stopping well short of it
# keeps the report to one message with room for the header.
INLINE_ITEM_LIMIT = 15
INLINE_CHAR_LIMIT = 1500

# The instruction is user text echoed back into the channel; keep it short.
MAX_INSTRUCTION_CHARS = 120

STOP_HINT = 'Say "stop" or react 🛑 to stop it.'

STATUS_DONE = "done"
STATUS_CANCELLED = "cancelled"
STATUS_FAILED = "failed"


def short_instruction(instruction: str, limit: int = MAX_INSTRUCTION_CHARS) -> str:
    """The instruction as it appears in a message: one line, bounded."""
    text = " ".join(str(instruction or "").split())
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def scan_slug(instruction: str) -> str:
    """A stable page slug for this instruction.

    Derived from the instruction rather than the job id so that re-scanning a
    channel for the same thing updates the same page instead of leaving a trail
    of near-identical ones.
    """
    slug = slugify(short_instruction(instruction, 60))
    return "scan-{}".format(slug) if slug else "scan"


def _count(n: int, singular: str, plural: Optional[str] = None) -> str:
    return "{:,} {}".format(n, singular if n == 1 else (plural or singular + "s"))


def starting_text(channel_name: str, instruction: str) -> str:
    return "🔎 Scanning #{} for {} — I'll post when I'm done. {}".format(
        channel_name, short_instruction(instruction), STOP_HINT
    )


def resumed_text(channel_name: str, instruction: str, scanned: int, found: int) -> str:
    """The status message a scan posts when it picks up after a restart.

    The message from before the restart belongs to a task that no longer
    exists, so a resumed scan says where it got to rather than starting the
    count again.
    """
    return (
        "🔎 Picking up where I left off scanning #{} for {} — {} scanned so far, "
        "{} found. {}".format(
            channel_name,
            short_instruction(instruction),
            _count(scanned, "message"),
            _count(found, "item"),
            STOP_HINT,
        )
    )


def progress_text(
    channel_name: str, instruction: str, scanned: int, found: int
) -> str:
    return "🔎 Scanning #{} for {} — {} scanned, {} found so far. {}".format(
        channel_name,
        short_instruction(instruction),
        _count(scanned, "message"),
        _count(found, "item"),
        STOP_HINT,
    )


def already_running_text(job: Dict[str, Any]) -> str:
    """What the tool tells the model when the channel is already being scanned."""
    if not job:
        return "A scan is already running in that channel."
    return (
        "A scan is already running in that channel: looking for {}, {} scanned, "
        "{} found so far. Only one scan runs per channel — whoever started it can "
        'say "stop" or react 🛑 to it, or it can be left to finish.'.format(
            short_instruction(job.get("instruction") or "something"),
            _count(int(job.get("scanned") or 0), "message"),
            _count(int(job.get("matched") or 0), "item"),
        )
    )


def _item_line(item: Dict[str, Any], markdown_links: bool) -> str:
    key = str(item.get("key") or "").strip()
    text = str(item.get("text") or "").strip()
    label = "**{}**".format(key) if key else ""
    if text and text.lower() != key.lower():
        label = "{} — {}".format(label, text) if label else text
    source = str(item.get("source_url") or "").strip()
    if source:
        # Discord renders a bare link as an embed-suppressed link in angle
        # brackets; a page wants a real Markdown link.
        label += " ([source]({}))".format(source) if markdown_links else " <{}>".format(source)
    return "- " + label.strip()


def items_text(items: List[Dict[str, Any]], markdown_links: bool = False) -> str:
    return "\n".join(_item_line(i, markdown_links) for i in items)


def fits_inline(items: List[Dict[str, Any]]) -> bool:
    """True when the list is short enough to post in the channel as-is."""
    if len(items) > INLINE_ITEM_LIMIT:
        return False
    return len(items_text(items)) <= INLINE_CHAR_LIMIT


def headline(
    channel_name: str,
    instruction: str,
    status: str,
    scanned: int,
    found: int,
    error: Optional[str] = None,
) -> str:
    """The first line of the final report: how it ended and what it covered."""
    what = short_instruction(instruction)
    scanned_text = _count(scanned, "message")
    if status == STATUS_FAILED:
        return "⚠️ The scan of #{} for {} failed after {}: {}".format(
            channel_name, what, scanned_text, str(error or "unknown error")[:300]
        )
    if status == STATUS_CANCELLED:
        return "🛑 Stopped scanning #{} for {} early — {} scanned, {} found.".format(
            channel_name, what, scanned_text, _count(found, "item")
        )
    return "✅ Finished scanning #{} for {} — {} scanned, {} found.".format(
        channel_name, what, scanned_text, _count(found, "item")
    )


def inline_report(
    channel_name: str,
    instruction: str,
    status: str,
    scanned: int,
    items: List[Dict[str, Any]],
    error: Optional[str] = None,
) -> str:
    """The whole report as one (possibly long) Discord message."""
    lines = [headline(channel_name, instruction, status, scanned, len(items), error)]
    if items:
        lines.append(items_text(items))
    elif status != STATUS_FAILED:
        lines.append("Nothing matched.")
    return "\n".join(lines)


def page_report(
    channel_name: str,
    instruction: str,
    status: str,
    scanned: int,
    items: List[Dict[str, Any]],
    url: str,
    error: Optional[str] = None,
) -> str:
    """The report when the list went to a page: the headline plus the link."""
    return "{}\n{}".format(
        headline(channel_name, instruction, status, scanned, len(items), error), url
    )


def page_title(channel_name: str, instruction: str) -> str:
    return "#{}: {}".format(channel_name, short_instruction(instruction, 60))


def page_markdown(
    channel_name: str,
    instruction: str,
    status: str,
    scanned: int,
    items: List[Dict[str, Any]],
) -> str:
    """The published page: what was asked, what was covered, what was found."""
    lines = [
        "Scanned **{}** of #{} looking for _{}_.".format(
            _count(scanned, "message"), channel_name, short_instruction(instruction)
        ),
    ]
    if status == STATUS_CANCELLED:
        lines.append("")
        lines.append("_This scan was stopped early, so it did not reach the start of the channel._")
    if status == STATUS_FAILED:
        lines.append("")
        lines.append("_This scan failed partway through; what it had found is below._")
    lines.append("")
    lines.append("## {} found".format(_count(len(items), "item")))
    lines.append("")
    lines.append(items_text(items, markdown_links=True) if items else "Nothing matched.")
    return "\n".join(lines)
