"""DayZ server monitoring: addresses, channel-name templates, online/offline state.

Pure functions shared by the `/dayz` cog, the `dayz_status` agent tool, and
the `dayz_channel_updater` worker. No discord.py, no Redis.

A *server* is a registered host:game_port with a display name. A *watch*
binds one server to one Discord channel whose name the worker rewrites on
every tick, plus an optional text channel for online/offline notices.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from bot.api.steam.a2s_client import ServerInfo

DEFAULT_TEMPLATE = "{emoji} {name} {players}/{max} · {time}"
OFFLINE_TEMPLATE = "{emoji} {name} offline"
DEFAULT_TIMEZONE = "America/Los_Angeles"
TEMPLATE_VARS = ("emoji", "name", "players", "max", "map", "time")

ONLINE_EMOJI = "🟢"
OFFLINE_EMOJI = "🔴"

STATE_UNKNOWN = "unknown"
STATE_ONLINE = "online"
STATE_OFFLINE = "offline"

# Consecutive failed ticks before a watched server is called offline.
# At a 5-minute tick this is 15 minutes of silence.
OFFLINE_AFTER_FAILURES = 3

DISCORD_CHANNEL_NAME_MAX = 100


def parse_address(address: str) -> Tuple[str, int]:
    """Split "host:port" into (host, port). Raises ValueError with a user-facing message."""
    text = (address or "").strip()
    if ":" not in text:
        raise ValueError("Address must look like `IP:PORT`, e.g. `51.222.245.31:2502`.")
    host, _, port_text = text.rpartition(":")
    host = host.strip()
    if not host or " " in host:
        raise ValueError(f"`{text}` does not have a valid host.")
    try:
        port = int(port_text)
    except ValueError:
        raise ValueError(f"`{port_text}` is not a port number.")
    if not 0 < port < 65536:
        raise ValueError(f"Port {port} is out of range (1–65535).")
    return host, port


def server_key(name: str) -> str:
    """The lookup key for a server name: case- and whitespace-insensitive."""
    return " ".join((name or "").strip().lower().split())


def resolve_timezone(name: Optional[str]) -> ZoneInfo:
    try:
        return ZoneInfo(name or DEFAULT_TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        return ZoneInfo("UTC")


def format_clock(now: dt.datetime) -> str:
    """"3:45pm" -- short enough to live in a channel name."""
    return now.strftime("%I:%M%p").lstrip("0").lower()


def validate_template(template: str) -> Optional[str]:
    """Return an error message if `template` uses unknown placeholders, else None."""
    sample = {v: "x" for v in TEMPLATE_VARS}
    try:
        template.format(**sample)
    except (KeyError, IndexError, ValueError) as exc:
        allowed = ", ".join(f"{{{v}}}" for v in TEMPLATE_VARS)
        return f"Template placeholder problem ({exc}). Allowed placeholders: {allowed}."
    return None


def _truncate(name: str) -> str:
    name = " ".join(name.split())
    return name[:DISCORD_CHANNEL_NAME_MAX]


def render_online_name(
    template: str,
    display_name: str,
    info: ServerInfo,
    now: dt.datetime,
) -> str:
    """Channel name for a server that answered."""
    text = (template or DEFAULT_TEMPLATE).format(
        emoji=ONLINE_EMOJI,
        name=display_name,
        players=info.players,
        max=info.max_players,
        map=info.map,
        time=format_clock(now),
    )
    return _truncate(text)


def render_offline_name(display_name: str) -> str:
    """Channel name for a server that stopped answering.

    Deliberately carries no clock: the name must stay stable while the server
    is down so the worker does not spend a rename every tick on it.
    """
    return _truncate(OFFLINE_TEMPLATE.format(emoji=OFFLINE_EMOJI, name=display_name))


def advance_state(
    previous: str, failures: int, success: bool
) -> Tuple[str, int]:
    """Fold one tick's result into (state, consecutive_failures).

    A success is online immediately. A failure only flips to offline after
    `OFFLINE_AFTER_FAILURES` in a row, so one dropped UDP packet is not an outage.
    """
    if success:
        return STATE_ONLINE, 0
    failures += 1
    if failures >= OFFLINE_AFTER_FAILURES:
        return STATE_OFFLINE, failures
    return previous if previous != STATE_UNKNOWN else STATE_UNKNOWN, failures


def should_notify(previous: str, new: str) -> bool:
    """Post a notice on real transitions, not on the first successful tick."""
    if previous == new:
        return False
    if previous == STATE_UNKNOWN and new == STATE_ONLINE:
        return False
    return new in (STATE_ONLINE, STATE_OFFLINE)


def describe_server(display_name: str, info: ServerInfo) -> str:
    """One readable line for embeds and the agent tool."""
    bits = [f"{info.players}/{info.max_players} players", info.map or "unknown map"]
    if info.version:
        bits.append(f"v{info.version}")
    if info.password:
        bits.append("password")
    return f"**{display_name}** — " + ", ".join(bits)


def new_server_record(
    display_name: str, host: str, game_port: int, query_port: int, added_by: Optional[int]
) -> Dict[str, Any]:
    return {
        "name": display_name,
        "host": host,
        "game_port": game_port,
        "query_port": query_port,
        "added_by": added_by,
        "added_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def new_watch_record(
    server: str,
    template: Optional[str],
    timezone: Optional[str],
    notify_channel_id: Optional[str],
) -> Dict[str, Any]:
    return {
        "server": server_key(server),
        "template": template or DEFAULT_TEMPLATE,
        "timezone": timezone or DEFAULT_TIMEZONE,
        "notify_channel_id": notify_channel_id,
        "state": STATE_UNKNOWN,
        "failures": 0,
        "last_name": None,
        "last_players": None,
        "last_checked_at": None,
        "last_changed_at": None,
    }
