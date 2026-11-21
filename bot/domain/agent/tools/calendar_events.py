"""The `calendar_events` agent tool: what is on this server's calendar."""

import datetime as dt
from typing import Any, Dict

import discord

from bot.app.redis.calendar_store import CalendarRedisStore
from bot.app.redis.serialization import guild_id_to_str
from bot.app.utils.logger import get_logger
from bot.domain.agent.tools.base import AgentTool
from bot.domain.calendar import service as svc
from bot.domain.calendar.models import zone

logger = get_logger()

SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "calendar_events",
        "description": (
            "What is on this server's shared calendar: plans people mentioned in chat "
            "and things added with /calendar add. Use when someone asks what's "
            "happening, what's coming up, when something is, or whether a day is free. "
            "The result starts with today's date, so call this before add_calendar_event "
            "when a date has to be worked out from words like 'Friday'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "How many days ahead to look, from today. Default 14, max 365.",
                },
                "query": {
                    "type": "string",
                    "description": "Only events whose title contains this. Omit for all.",
                },
            },
            "required": [],
        },
    },
}


async def execute_calendar_events(arguments: Dict[str, Any], channel: discord.TextChannel) -> str:
    guild = getattr(channel, "guild", None)
    if guild is None:
        return "The calendar only exists inside a server."
    store = CalendarRedisStore()
    guild_id = guild_id_to_str(guild.id)
    try:
        config = await store.get_config(guild_id)
        tz_name = str(config["timezone"])
        now = dt.datetime.now(dt.timezone.utc)
        try:
            days = int(arguments.get("days") or svc.DEFAULT_UPCOMING_DAYS)
        except (TypeError, ValueError):
            days = svc.DEFAULT_UPCOMING_DAYS
        days = max(1, min(days, svc.MAX_UPCOMING_DAYS))
        start, end = svc.upcoming_window(now, tz_name, days)
        events = await store.get_events_between(guild_id, start, end)
    except Exception as exc:
        logger.error(f"calendar_events: store error: {exc}")
        return "Couldn't read the calendar right now."

    needle = str(arguments.get("query") or "").strip().lower()
    if needle:
        events = [e for e in events if needle in e.title.lower()]

    today = now.astimezone(zone(tz_name))
    header = "Today is %s %s (%s)." % (today.strftime("%A"), today.strftime("%Y-%m-%d"), tz_name)
    if not events:
        what = "matching '%s' " % arguments.get("query") if needle else ""
        tail = "" if config.get("enabled") else " Capture from chat is off here; an admin can turn it on with /calendar enable."
        return "%s Nothing %son the calendar for the next %d days.%s" % (header, what, days, tail)
    body = svc.render_upcoming(events, now, tz_name, show_ids=True, with_links=True)
    return "%s Calendar for the next %d days (ids in backticks, ↗ links to the message it came from):\n%s" % (
        header, days, body
    )


TOOL = AgentTool(
    config_key="calendar_events",
    schema=SCHEMA,
    executor=execute_calendar_events,
    channel_aware=True,
)
