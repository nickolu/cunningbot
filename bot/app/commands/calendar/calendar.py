"""The server calendar: /calendar enable | disable | status | upcoming | add | remove.

`enable` turns on silent capture from chat (see calendar_listener.py) for the
whole server and sets its timezone. `add` and `upcoming` work whether or not
capture is on: the calendar is the server's either way.
"""

import datetime as dt
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot.app.redis.calendar_store import CalendarRedisStore
from bot.app.redis.serialization import channel_id_to_str, guild_id_to_str
from bot.app.utils.logger import get_logger
from bot.domain.calendar import service as svc
from bot.domain.calendar.models import CalendarEvent, resolve_timezone_name, zone

logger = get_logger()

EMBED_COLOR = 0x5B8DEF
UNDO_HINT = "React ❌ on a message to take back what the bot added from it."


class CalendarCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._store: Optional[CalendarRedisStore] = None

    @property
    def store(self) -> CalendarRedisStore:
        if self._store is None:
            self._store = CalendarRedisStore()
        return self._store

    calendar = app_commands.Group(name="calendar", description="This server's shared calendar")

    # ------------------------------------------------------------- enable

    @calendar.command(name="enable", description="Start adding plans mentioned in chat to the calendar")
    @app_commands.describe(timezone="Server timezone: Pacific, Eastern, UTC, or an IANA name like Europe/London")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def enable(self, interaction: discord.Interaction, timezone: Optional[str] = None) -> None:
        await interaction.response.defer()
        guild_id = guild_id_to_str(interaction.guild_id)
        config = await self.store.get_config(guild_id)
        tz_name = resolve_timezone_name(timezone) if timezone else str(config.get("timezone"))
        if tz_name is None:
            await interaction.followup.send(
                f"I don't know the timezone **{timezone}**. Try Pacific, Eastern, UTC, or an IANA name like Europe/London.",
                ephemeral=True,
            )
            return
        config.update({
            "enabled": True,
            "timezone": tz_name,
            "enabled_by": str(interaction.user.id),
            "enabled_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        })
        await self.store.save_config(guild_id, config)
        logger.info({"event": "calendar_enabled", "guild": guild_id, "timezone": tz_name})
        embed = discord.Embed(
            title="📅 Calendar capture is on",
            description=(
                f"When someone mentions a plan with a day attached, I'll add it to the server calendar "
                f"and react 📅 on their message. Times are read in **{tz_name}**.\n"
                f"{UNDO_HINT} `/calendar upcoming` shows what's coming."
            ),
            color=EMBED_COLOR,
        )
        await interaction.followup.send(embed=embed)

    @calendar.command(name="disable", description="Stop adding plans from chat (keeps the calendar)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def disable(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        guild_id = guild_id_to_str(interaction.guild_id)
        config = await self.store.get_config(guild_id)
        config["enabled"] = False
        await self.store.save_config(guild_id, config)
        logger.info({"event": "calendar_disabled", "guild": guild_id})
        await interaction.followup.send(
            "📅 Calendar capture is off. Nothing new is taken from chat; `/calendar add` and `/calendar upcoming` still work."
        )

    # ------------------------------------------------------------- status

    @calendar.command(name="status", description="Whether capture is on, the timezone, and how much is scheduled")
    async def status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        guild_id = guild_id_to_str(interaction.guild_id)
        config = await self.store.get_config(guild_id)
        now = dt.datetime.now(dt.timezone.utc)
        start, _ = svc.upcoming_window(now, str(config["timezone"]), 1)
        upcoming = await self.store.count_upcoming(guild_id, start)
        state = "on" if config.get("enabled") else "off"
        embed = discord.Embed(title="📅 Calendar", color=EMBED_COLOR)
        embed.add_field(name="Capture from chat", value=state, inline=True)
        embed.add_field(name="Timezone", value=str(config["timezone"]), inline=True)
        embed.add_field(name="Upcoming events", value=str(upcoming), inline=True)
        embed.set_footer(text=UNDO_HINT)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ----------------------------------------------------------- upcoming

    @calendar.command(name="upcoming", description="What's on the calendar")
    @app_commands.describe(days="How many days ahead to show (default 14)")
    async def upcoming(
        self, interaction: discord.Interaction, days: Optional[app_commands.Range[int, 1, 365]] = None
    ) -> None:
        await interaction.response.defer()
        guild_id = guild_id_to_str(interaction.guild_id)
        config = await self.store.get_config(guild_id)
        tz_name = str(config["timezone"])
        span = days or svc.DEFAULT_UPCOMING_DAYS
        now = dt.datetime.now(dt.timezone.utc)
        start, end = svc.upcoming_window(now, tz_name, span)
        events = await self.store.get_events_between(guild_id, start, end)
        body = svc.render_upcoming(events, now, tz_name, show_ids=True, with_links=True)
        if not body:
            hint = "" if config.get("enabled") else " An admin can turn on capture from chat with `/calendar enable`."
            await interaction.followup.send(
                f"Nothing on the calendar for the next {span} days. Add something with `/calendar add`.{hint}"
            )
            return
        if len(body) > 4000:
            body = body[:3990].rsplit("\n", 1)[0] + "\n…"
        embed = discord.Embed(title=f"📅 Next {span} days", description=body, color=EMBED_COLOR)
        embed.set_footer(text=f"Times in {tz_name}. Remove one with /calendar remove <id>.")
        await interaction.followup.send(embed=embed)

    # ---------------------------------------------------------------- add

    @calendar.command(name="add", description="Put something on the calendar")
    @app_commands.describe(
        title="What it is",
        date="When: 2026-12-05, 12/5, today, tomorrow",
        time="Optional start time: 7pm, 19:30",
        end_date="Optional last day, for something spanning several days",
    )
    async def add(
        self,
        interaction: discord.Interaction,
        title: str,
        date: str,
        time: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> None:
        await interaction.response.defer()
        guild_id = guild_id_to_str(interaction.guild_id)
        config = await self.store.get_config(guild_id)
        tz_name = str(config["timezone"])
        now = dt.datetime.now(dt.timezone.utc)
        today = now.astimezone(zone(tz_name)).date()

        clean_title = " ".join(title.split())[:80]
        if not clean_title:
            await interaction.followup.send("The title is empty.", ephemeral=True)
            return
        day = svc.parse_date_input(date, today)
        if day is None:
            await interaction.followup.send(
                f"I couldn't read **{date}** as a day. Try 2026-12-05, 12/5, today, or tomorrow.", ephemeral=True
            )
            return
        if day < today:
            await interaction.followup.send(f"{day.isoformat()} is in the past.", ephemeral=True)
            return
        clock = svc.parse_time_input(time)
        if clock == "":
            await interaction.followup.send(f"I couldn't read **{time}** as a time. Try 7pm or 19:30.", ephemeral=True)
            return
        last: Optional[dt.date] = None
        if end_date:
            last = svc.parse_date_input(end_date, today)
            if last is None or last <= day:
                await interaction.followup.send("The end date has to be a day after the start.", ephemeral=True)
                return

        same_day = await self.store.get_events_on(guild_id, day.isoformat(), tz_name)
        twin = svc.is_duplicate(clean_title, day.isoformat(), same_day)
        if twin is not None:
            await interaction.followup.send(
                f"That looks like it's already there: {svc.describe_event(twin)}", ephemeral=True
            )
            return

        event_id = svc.new_event_id()
        while await self.store.event_id_exists(guild_id, event_id):
            event_id = svc.new_event_id()
        event = CalendarEvent(
            id=event_id,
            title=clean_title,
            date=day.isoformat(),
            time=clock,
            end_date=last.isoformat() if last else None,
            timezone=tz_name,
            channel_id=channel_id_to_str(interaction.channel_id) if interaction.channel_id else None,
            author_id=str(interaction.user.id),
            author_name=interaction.user.display_name,
            created_at=now.isoformat(),
            source="manual",
        )
        await self.store.add_event(guild_id, event)
        logger.info({"event": "calendar_manual_add", "guild": guild_id, "title": clean_title, "date": event.date})
        await interaction.followup.send(
            f"📅 Added for **{svc.day_heading(event.date, today)}**: {svc.describe_event(event)}"
        )

    # ------------------------------------------------------------- remove

    @calendar.command(name="remove", description="Take something off the calendar")
    @app_commands.describe(id="The event's id, shown in /calendar upcoming")
    async def remove(self, interaction: discord.Interaction, id: str) -> None:
        await interaction.response.defer()
        guild_id = guild_id_to_str(interaction.guild_id)
        event_id = id.strip().strip("`").lower()
        event = await self.store.get_event(guild_id, event_id)
        if event is None:
            await interaction.followup.send(f"No event with id `{event_id}`.", ephemeral=True)
            return
        perms = getattr(interaction.user, "guild_permissions", None)
        may_manage = bool(perms is not None and perms.manage_messages)
        if event.author_id != str(interaction.user.id) and not may_manage:
            who = event.author_name or "whoever added it"
            await interaction.followup.send(
                f"Only {who} or someone who can manage messages can remove that.", ephemeral=True
            )
            return
        await self.store.delete_event(guild_id, event_id)
        logger.info({"event": "calendar_manual_remove", "guild": guild_id, "id": event_id})
        await interaction.followup.send(f"🗑️ Removed: {svc.describe_event(event, show_id=False)}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CalendarCog(bot))
