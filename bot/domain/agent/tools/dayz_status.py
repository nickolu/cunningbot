"""The `dayz_status` agent tool.

Live player counts for the DayZ servers this guild registered with
`/dayz server register`. Queries the servers directly over Steam A2S; nothing
is cached because the numbers change by the minute.
"""

import asyncio
from typing import Any, Dict

import discord

from bot.api.steam.a2s_client import ServerInfo, query_server_info
from bot.app.redis.dayz_store import DayzRedisStore
from bot.app.redis.serialization import guild_id_to_str
from bot.app.utils.logger import get_logger
from bot.domain.agent.tools.base import AgentTool
from bot.domain.dayz import dayz_service as svc

logger = get_logger()


SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "dayz_status",
        "description": (
            "Live status of this server's registered DayZ game servers: who is "
            "online right now, player count and capacity, map, version. Use when "
            "someone asks how many people are on the DayZ server or whether it is up."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Only servers whose name contains this. Omit for all.",
                },
            },
            "required": [],
        },
    },
}


async def execute_dayz_status(arguments: Dict[str, Any], channel: discord.TextChannel) -> str:
    store = DayzRedisStore()
    guild_id = guild_id_to_str(channel.guild.id)
    try:
        servers = await store.get_all_servers(guild_id)
    except Exception as exc:
        logger.error(f"dayz_status: store error: {exc}")
        return "Couldn't read the registered DayZ servers right now."

    needle = svc.server_key(arguments.get("name") or "")
    if needle:
        servers = {k: v for k, v in servers.items() if needle in k}
    if not servers:
        if needle:
            return f"No registered DayZ server matches '{arguments.get('name')}'."
        return "No DayZ servers are registered here. An admin can add one with /dayz server register."

    ordered = sorted(servers.values(), key=lambda r: r["name"].lower())
    results = await asyncio.gather(
        *(query_server_info(r["host"], int(r["query_port"])) for r in ordered),
        return_exceptions=True,
    )
    lines = []
    for record, result in zip(ordered, results):
        if isinstance(result, ServerInfo):
            lines.append(svc.describe_server(record["name"], result) + " (online)")
        else:
            lines.append(f"**{record['name']}** — no reply, looks offline ({record['host']}:{record['game_port']})")
    return "DayZ servers right now:\n" + "\n".join(lines)


TOOL = AgentTool(
    config_key="dayz_status",
    schema=SCHEMA,
    executor=execute_dayz_status,
    channel_aware=True,
)
