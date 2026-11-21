"""The `add_calendar_event` agent tool: put something on the server calendar.

Not offered to scheduled runs (`scheduled_ok=False`): a job that adds calendar
entries unattended would fill the calendar with itself.
"""

import datetime as dt
from typing import Any, Dict, Optional

import discord

from bot.app.redis.calendar_store import CalendarRedisStore
from bot.app.redis.serialization import channel_id_to_str, guild_id_to_str
from bot.app.utils.logger import get_logger
from bot.domain.agent.tools.base import AgentTool
from bot.domain.calendar import service as svc
from bot.domain.calendar.models import CalendarEvent, zone

logger = get_logger()

SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "add_calendar_event",
        "description": (
            "Add one entry to this server's shared calendar when someone asks for it "
            "('add game night on Friday', 'put the trip on the calendar'). The date "
            "must be a real YYYY-MM-DD: if it is given in words, call calendar_events "
            "first to learn today's date and work it out. Do not use this for things "
            "the bot noticed on its own; only when asked."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "A few words naming the plan, no date in it."},
                "date": {"type": "string", "description": "Start day, YYYY-MM-DD."},
                "time": {"type": "string", "description": "Optional start time, HH:MM 24-hour, local to the server."},
                "end_date": {"type": "string", "description": "Optional last day, YYYY-MM-DD, for multi-day plans."},
            },
            "required": ["title", "date"],
        },
    },
}


async def execute_add_calendar_event(
    arguments: Dict[str, Any], channel: discord.TextChannel, user: Any
) -> str:
    guild = getattr(channel, "guild", None)
    if guild is None:
        return "The calendar only exists inside a server."
    store = CalendarRedisStore()
    guild_id = guild_id_to_str(guild.id)
    config = await store.get_config(guild_id)
    tz_name = str(config["timezone"])
    now = dt.datetime.now(dt.timezone.utc)
    today = now.astimezone(zone(tz_name)).date()

    title = " ".join(str(arguments.get("title") or "").split())[:80]
    if not title:
        return "The event needs a title."
    day = svc.parse_date_input(str(arguments.get("date") or ""), today)
    if day is None:
        return "The date has to be YYYY-MM-DD. Today is %s." % today.isoformat()
    if day < today:
        return "%s is in the past; today is %s (%s)." % (day.isoformat(), today.isoformat(), today.strftime("%A"))
    clock: Optional[str] = svc.parse_time_input(arguments.get("time"))
    if clock == "":
        return "The time has to be HH:MM in 24-hour form, or left out."
    last: Optional[dt.date] = None
    raw_end = arguments.get("end_date")
    if raw_end:
        last = svc.parse_date_input(str(raw_end), today)
        if last is None or last <= day:
            return "end_date has to be a YYYY-MM-DD after the start date."

    try:
        same_day = await store.get_events_on(guild_id, day.isoformat(), tz_name)
        twin = svc.is_duplicate(title, day.isoformat(), same_day)
        if twin is not None:
            return "Already on the calendar: %s on %s." % (svc.describe_event(twin), twin.date)
        event_id = svc.new_event_id()
        while await store.event_id_exists(guild_id, event_id):
            event_id = svc.new_event_id()
        event = CalendarEvent(
            id=event_id,
            title=title,
            date=day.isoformat(),
            time=clock,
            end_date=last.isoformat() if last else None,
            timezone=tz_name,
            channel_id=channel_id_to_str(channel.id),
            author_id=str(getattr(user, "id", "")) or None,
            author_name=getattr(user, "display_name", None),
            created_at=now.isoformat(),
            source="manual",
        )
        await store.add_event(guild_id, event)
    except Exception as exc:
        logger.error(f"add_calendar_event: store error: {exc}")
        return "Couldn't write to the calendar right now."

    logger.info({"event": "calendar_agent_add", "guild": guild_id, "title": title, "date": event.date})
    return "Added to the calendar for %s (%s): %s" % (
        day.strftime("%A"), day.isoformat(), svc.describe_event(event)
    )


TOOL = AgentTool(
    config_key="add_calendar_event",
    schema=SCHEMA,
    executor=execute_add_calendar_event,
    channel_aware=True,
    user_aware=True,
    scheduled_ok=False,
)
