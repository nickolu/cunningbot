"""Steam Source Query (A2S_INFO) client.

Talks UDP to a game server's *query* port and returns the public server
summary: name, map, player counts, version. Works for any Source-query game;
CunningBot uses it for DayZ. No API key, one round trip (two when the server
asks for a challenge, which every modern server does).

DayZ note: the query port is not the game port. Most hosts use game port +
24714 (2302 -> 27016), but hosts running several servers on one box number
the query ports consecutively instead, so `discover_query_port` tries the
conventional offset first and then a small range, keeping the responder whose
reported game port matches.
"""

from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

A2S_INFO_REQUEST = b"\xFF\xFF\xFF\xFFTSource Engine Query\x00"
_HEADER_INFO = 0x49  # 'I'
_HEADER_CHALLENGE = 0x41  # 'A'

DEFAULT_TIMEOUT = 3.0
DAYZ_QUERY_PORT_OFFSET = 24714
_QUERY_PORT_SCAN_RANGE = range(27014, 27031)


class A2SError(Exception):
    """The server did not answer, or answered with something unparseable."""


@dataclass
class ServerInfo:
    name: str
    map: str
    game: str
    players: int
    max_players: int
    bots: int
    version: str
    password: bool
    vac: bool
    game_port: Optional[int] = None
    keywords: str = ""
    query_port: Optional[int] = None
    raw_flags: dict = field(default_factory=dict)


def _read_cstring(buf: bytes, i: int) -> Tuple[str, int]:
    end = buf.index(b"\x00", i)
    return buf[i:end].decode("utf-8", "replace"), end + 1


def parse_info_response(data: bytes) -> ServerInfo:
    """Parse an A2S_INFO reply (Source format) into a `ServerInfo`."""
    if len(data) < 6 or data[:4] != b"\xFF\xFF\xFF\xFF" or data[4] != _HEADER_INFO:
        raise A2SError(f"unexpected A2S response header: {data[:5]!r}")
    i = 6  # skip header + protocol byte
    try:
        name, i = _read_cstring(data, i)
        map_name, i = _read_cstring(data, i)
        _folder, i = _read_cstring(data, i)
        game, i = _read_cstring(data, i)
        i += 2  # app id (16-bit, truncated for DayZ; the real one is in EDF)
        players, max_players, bots = data[i], data[i + 1], data[i + 2]
        i += 3
        _server_type, _env = data[i], data[i + 1]
        password, vac = bool(data[i + 2]), bool(data[i + 3])
        i += 4
        version, i = _read_cstring(data, i)
    except (IndexError, ValueError) as exc:
        raise A2SError(f"truncated A2S_INFO response: {exc}") from exc

    info = ServerInfo(
        name=name, map=map_name, game=game, players=players,
        max_players=max_players, bots=bots, version=version,
        password=password, vac=vac,
    )
    if i < len(data):
        edf = data[i]
        i += 1
        try:
            if edf & 0x80:
                info.game_port = struct.unpack_from("<H", data, i)[0]
                i += 2
            if edf & 0x10:
                i += 8  # server steam id
            if edf & 0x40:
                i += 2  # spectator port
                _spec_name, i = _read_cstring(data, i)
            if edf & 0x20:
                info.keywords, i = _read_cstring(data, i)
        except (IndexError, ValueError, struct.error):
            pass  # optional trailer; keep what we have
    return info


class _ReplyProtocol(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.queue: "asyncio.Queue[bytes]" = asyncio.Queue()
        self.transport: Optional[asyncio.DatagramTransport] = None

    def connection_made(self, transport) -> None:  # type: ignore[override]
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        self.queue.put_nowait(data)

    def error_received(self, exc: Exception) -> None:
        self.queue.put_nowait(b"")


async def query_server_info(
    host: str, query_port: int, timeout: float = DEFAULT_TIMEOUT
) -> ServerInfo:
    """Send A2S_INFO to `host:query_port` and return the parsed reply.

    Raises `A2SError` on timeout, connection failure, or a bad reply.
    """
    loop = asyncio.get_running_loop()
    try:
        transport, protocol = await loop.create_datagram_endpoint(
            _ReplyProtocol, remote_addr=(host, query_port)
        )
    except OSError as exc:
        raise A2SError(f"could not open UDP socket to {host}:{query_port}: {exc}") from exc

    try:
        transport.sendto(A2S_INFO_REQUEST)
        try:
            data = await asyncio.wait_for(protocol.queue.get(), timeout)
        except asyncio.TimeoutError:
            raise A2SError(f"no reply from {host}:{query_port} within {timeout:g}s")
        if len(data) >= 9 and data[4] == _HEADER_CHALLENGE:
            transport.sendto(A2S_INFO_REQUEST + data[5:9])
            try:
                data = await asyncio.wait_for(protocol.queue.get(), timeout)
            except asyncio.TimeoutError:
                raise A2SError(f"no reply to challenge from {host}:{query_port}")
        if not data:
            raise A2SError(f"UDP error talking to {host}:{query_port}")
        info = parse_info_response(data)
        info.query_port = query_port
        return info
    finally:
        transport.close()


def candidate_query_ports(game_port: int) -> List[int]:
    """Query ports worth trying for a DayZ server on `game_port`, best first."""
    ordered = [game_port + DAYZ_QUERY_PORT_OFFSET, 27016]
    ordered.extend(_QUERY_PORT_SCAN_RANGE)
    ordered.append(game_port + 1)
    seen: set = set()
    out: List[int] = []
    for p in ordered:
        if 0 < p < 65536 and p not in seen:
            seen.add(p)
            out.append(p)
    return out


async def discover_query_port(
    host: str, game_port: int, timeout: float = DEFAULT_TIMEOUT
) -> Optional[ServerInfo]:
    """Find the query port for the server playing on `host:game_port`.

    Probes every candidate concurrently. Returns the `ServerInfo` whose
    reported game port matches, or -- if no responder reports a game port --
    the reply from the conventional +24714 port. None when nothing fits.
    """
    candidates = candidate_query_ports(game_port)
    results = await asyncio.gather(
        *(query_server_info(host, p, timeout) for p in candidates),
        return_exceptions=True,
    )
    conventional = game_port + DAYZ_QUERY_PORT_OFFSET
    fallback: Optional[ServerInfo] = None
    for port, result in zip(candidates, results):
        if not isinstance(result, ServerInfo):
            continue
        if result.game_port == game_port:
            return result
        if result.game_port is None and port == conventional:
            fallback = result
    return fallback
