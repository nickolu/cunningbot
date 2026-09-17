"""The `framed_stats` agent tool.

Answers questions about this server's Framed results ("who's winning this
year?", "what's Nick's streak?", "how did everyone do on #1650?") from the
results /framed tracks. Read-only; the data comes from the sync worker.
"""

from typing import Any, Dict

import discord

from bot.app.utils.logger import get_logger
from bot.domain.agent.tools.base import AgentTool

logger = get_logger()


SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "framed_stats",
        "description": (
            "Look up this server's results for Framed, the daily movie-guessing "
            "game (framed.wtf): rankings, one player's stats and streaks, one "
            "day's results, or a head-to-head record. Points: a 1 is 6 points, "
            "a 6 is 1, a miss is 0. Results are read after each day ends, so "
            "today's aren't included yet."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["leaderboard", "player", "day", "head_to_head"],
                },
                "period": {
                    "type": "string",
                    "enum": ["week", "month", "year", "all"],
                    "description": "For leaderboard. Default year.",
                },
                "year": {"type": "integer", "description": "For leaderboard: a specific year"},
                "player": {"type": "string", "description": "Player name, for player or head_to_head"},
                "other_player": {"type": "string", "description": "Second name, for head_to_head"},
                "day": {
                    "type": "string",
                    "description": "For day: puzzle number (1650) or date (2026-09-16). Default yesterday.",
                },
            },
            "required": ["action"],
        },
    },
}


def _resolve(data: Any, query: str):
    from bot.domain.framed.stats_service import find_players

    matches = find_players(data.names, query)
    if not matches:
        known = ", ".join(sorted(data.names.values())) or "none yet"
        return None, f"No Framed player matches '{query}'. Known players: {known}."
    if len(matches) > 1:
        return None, "'%s' matches several players (%s); ask which one." % (
            query, ", ".join(data.name(m) for m in matches))
    return matches[0], None


async def execute_framed_stats(
    arguments: Dict[str, Any], channel: discord.TextChannel
) -> str:
    guild = getattr(channel, "guild", None)
    if guild is None:
        return "Framed stats only work in a server."
    try:
        from bot.app.redis.framed_store import FramedRedisStore
        from bot.app.redis.serialization import guild_id_to_str
        from bot.domain.framed import stats_service
        from bot.domain.framed.stats import head_to_head, player_stats

        data = await stats_service.load(FramedRedisStore(), guild_id_to_str(guild.id))
    except Exception as e:
        logger.error(f"framed_stats failed to load results: {e}")
        return "Framed results couldn't be loaded right now."
    if data is None:
        return "Framed isn't tracked in this server. An admin can set it up with /framed register."

    action = arguments.get("action") or "leaderboard"
    if action == "player" or action == "head_to_head":
        uid, error = _resolve(data, str(arguments.get("player") or ""))
        if error:
            return error
        if action == "player":
            text = "Framed stats for %s:\n%s" % (
                data.name(uid),
                stats_service.format_player(data, player_stats(data.scores, uid, data.as_of)),
            )
        else:
            other, error = _resolve(data, str(arguments.get("other_player") or ""))
            if error:
                return error
            text = stats_service.format_head_to_head(
                data, uid, other, head_to_head(data.scores, uid, other))
    elif action == "day":
        day = stats_service.parse_day(arguments.get("day"), data.latest_day)
        if day is None:
            return "Couldn't understand that day; use a puzzle number or YYYY-MM-DD."
        title, body = stats_service.format_day(data, day)
        text = f"{title}\n{body}"
    else:
        year = arguments.get("year")
        label, rows = stats_service.leaderboard_for(
            data, str(arguments.get("period") or "year"), int(year) if year else None)
        text = "Framed leaderboard, %s (by total points):\n%s" % (
            label, stats_service.format_leaderboard(rows, data))

    warning = stats_service.sync_warning(data)
    return text + ("\n" + warning if warning else "")


TOOL = AgentTool(
    config_key="framed_stats",
    schema=SCHEMA,
    executor=execute_framed_stats,
    channel_aware=True,
)
