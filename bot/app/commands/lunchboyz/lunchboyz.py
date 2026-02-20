"""Lunch Boyz rotation command cog for CunningBot.

Provides /lunchboyz setup, status, skip, plan, and advance commands
for managing a bi-weekly rotating lunch/happy-hour responsibility.
"""

import datetime
import logging
import re
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot.app.redis.lunchboyz_store import LunchboyzRedisStore
from bot.app.redis.serialization import guild_id_to_str

logger = logging.getLogger("LunchboyzCommands")


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def parse_rotation_mentions(rotation_str: str) -> list[str]:
    """Extract user IDs from a string of Discord @mentions."""
    return re.findall(r"<@!?(\d+)>", rotation_str)


def parse_date(date_str: str) -> Optional[datetime.date]:
    """Parse MM/DD or MM/DD/YYYY into a date. Uses current year for MM/DD."""
    s = date_str.strip()
    for fmt in ("%m/%d/%Y", "%m/%d"):
        try:
            d = datetime.datetime.strptime(s, fmt)
            if fmt == "%m/%d":
                d = d.replace(year=datetime.date.today().year)
            return d.date()
        except ValueError:
            continue
    return None


def parse_time(time_str: str) -> Optional[str]:
    """Parse HH:MM or H:MM AM/PM into 'HH:MM' 24-hour string."""
    s = time_str.strip().upper()
    for fmt in ("%I:%M %p", "%I:%M%p", "%H:%M"):
        try:
            t = datetime.datetime.strptime(s, fmt)
            return t.strftime("%H:%M")
        except ValueError:
            continue
    return None


def get_member_name(guild: discord.Guild, user_id: str) -> str:
    """Return display name for a user ID, falling back to mention."""
    member = guild.get_member(int(user_id))
    if member:
        return member.display_name
    return f"<@{user_id}>"


def make_deadline(last_advanced: str, frequency_days: int) -> datetime.date:
    return datetime.date.fromisoformat(last_advanced) + datetime.timedelta(days=frequency_days)


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class LunchboyzCog(commands.Cog):
    """Cog for Lunch Boyz rotation commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    lunchboyz = app_commands.Group(
        name="lunchboyz",
        description="Manage the bi-weekly Lunch Boyz rotation.",
    )

    # ------------------------------------------------------------------
    # /lunchboyz setup
    # ------------------------------------------------------------------

    @lunchboyz.command(
        name="setup",
        description="Set up the Lunch Boyz rotation in this channel.",
    )
    @app_commands.describe(
        rotation="Mention all participants in order (e.g. @Alice @Bob @Carol)",
        frequency="Rotation period in days (default: 14)",
        start_date="When the current rotation started, MM/DD or MM/DD/YYYY (default: today)",
        timezone="IANA timezone for reminders (default: America/Los_Angeles)",
    )
    async def setup(
        self,
        interaction: discord.Interaction,
        rotation: str,
        frequency: int = 14,
        start_date: Optional[str] = None,
        timezone: str = "America/Los_Angeles",
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        user_ids = parse_rotation_mentions(rotation)
        if not user_ids:
            await interaction.followup.send(
                "No users found. Please @mention at least one participant.", ephemeral=True
            )
            return

        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.followup.send(
                "This command can only be used in a text channel.", ephemeral=True
            )
            return

        if start_date is not None:
            parsed_start = parse_date(start_date)
            if not parsed_start:
                await interaction.followup.send(
                    f"Invalid start_date `{start_date}`. Use MM/DD or MM/DD/YYYY (e.g. `02/10` or `02/10/2026`).",
                    ephemeral=True,
                )
                return
            today = parsed_start.isoformat()
        else:
            today = datetime.date.today().isoformat()

        config = {
            "channel_id": str(interaction.channel.id),
            "frequency_days": frequency,
            "timezone": timezone,
        }
        state = {
            "current_index": 0,
            "last_advanced": today,
            "event": None,
            "reminders_sent": [],
        }

        store = LunchboyzRedisStore()
        await store.save_config(guild_id_str, config)
        await store.save_rotation(guild_id_str, user_ids)
        await store.save_state(guild_id_str, state)

        first_name = get_member_name(interaction.guild, user_ids[0])
        await interaction.channel.send(
            f"🍽️ Lunch Boyz rotation set! <@{user_ids[0]}> ({first_name}) is up first."
        )
        deadline = make_deadline(today, frequency)
        await interaction.followup.send(
            f"Rotation configured with {len(user_ids)} participant(s), "
            f"every {frequency} days. Deadline: {deadline.strftime('%m/%d/%Y')}.",
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # /lunchboyz status
    # ------------------------------------------------------------------

    @lunchboyz.command(
        name="status",
        description="Show the current Lunch Boyz rotation status.",
    )
    async def status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        guild_id_str = guild_id_to_str(interaction.guild_id)
        store = LunchboyzRedisStore()
        config = await store.get_config(guild_id_str)
        rotation = await store.get_rotation(guild_id_str)
        state = await store.get_state(guild_id_str)

        if not config or not rotation or not state:
            await interaction.followup.send(
                "Lunch Boyz hasn't been set up yet. Use `/lunchboyz setup` to get started."
            )
            return

        idx = state.get("current_index", 0) % len(rotation)
        current_id = rotation[idx]
        current_name = get_member_name(interaction.guild, current_id)

        frequency_days = config.get("frequency_days", 14)
        last_advanced = state.get("last_advanced", datetime.date.today().isoformat())
        deadline = make_deadline(last_advanced, frequency_days)

        embed = discord.Embed(title="🍽️ Lunch Boyz Status", color=0xF4A460)
        embed.add_field(
            name="Currently Up",
            value=f"<@{current_id}> ({current_name})",
            inline=False,
        )

        rotation_lines = []
        for i, uid in enumerate(rotation):
            name = get_member_name(interaction.guild, uid)
            marker = " ◀ current" if i == idx else ""
            rotation_lines.append(f"{i + 1}. {name}{marker}")
        embed.add_field(name="Rotation Order", value="\n".join(rotation_lines), inline=False)

        event = state.get("event")
        if event:
            event_date = datetime.date.fromisoformat(event["date"])
            event_lines = [
                f"📍 {event['location']}",
                f"🗓️ {event_date.strftime('%m/%d/%Y')} at {event['time']}",
            ]
            if event.get("notes"):
                event_lines.append(f"📝 {event['notes']}")
            embed.add_field(name="Planned Event", value="\n".join(event_lines), inline=False)
        else:
            embed.add_field(name="Event", value="No event scheduled yet.", inline=False)

        embed.add_field(
            name="Rotation Deadline",
            value=deadline.strftime("%m/%d/%Y"),
            inline=True,
        )
        embed.add_field(
            name="Frequency",
            value=f"Every {frequency_days} days",
            inline=True,
        )

        await interaction.followup.send(embed=embed)

    # ------------------------------------------------------------------
    # /lunchboyz skip
    # ------------------------------------------------------------------

    @lunchboyz.command(
        name="skip",
        description="Skip the current person and advance the rotation.",
    )
    async def skip(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        guild_id_str = guild_id_to_str(interaction.guild_id)
        store = LunchboyzRedisStore()
        config = await store.get_config(guild_id_str)
        rotation = await store.get_rotation(guild_id_str)
        state = await store.get_state(guild_id_str)

        if not config or not rotation or not state:
            await interaction.followup.send(
                "Lunch Boyz hasn't been set up yet. Use `/lunchboyz setup` first.",
                ephemeral=True,
            )
            return

        current_idx = state.get("current_index", 0)
        new_idx = (current_idx + 1) % len(rotation)
        state["current_index"] = new_idx
        state["last_advanced"] = datetime.date.today().isoformat()
        state["event"] = None
        state["reminders_sent"] = []
        await store.save_state(guild_id_str, state)

        next_id = rotation[new_idx]
        next_name = get_member_name(interaction.guild, next_id)

        channel_id = int(config["channel_id"])
        channel = self.bot.get_channel(channel_id)
        if channel and isinstance(channel, discord.TextChannel):
            await channel.send(
                f"⏭️ Skipped! <@{next_id}> ({next_name}) is now up for Lunch Boyz."
            )

        await interaction.followup.send("Rotation skipped.", ephemeral=True)

    # ------------------------------------------------------------------
    # /lunchboyz plan
    # ------------------------------------------------------------------

    @lunchboyz.command(
        name="plan",
        description="Announce the upcoming Lunch Boyz event.",
    )
    @app_commands.describe(
        location="Where are we going? (e.g. Chipotle)",
        date="Date in MM/DD or MM/DD/YYYY format",
        time="Time in HH:MM or H:MM AM/PM format",
        notes="Optional extra details",
    )
    async def plan(
        self,
        interaction: discord.Interaction,
        location: str,
        date: str,
        time: str,
        notes: Optional[str] = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        parsed_date = parse_date(date)
        if not parsed_date:
            await interaction.followup.send(
                f"Invalid date `{date}`. Use MM/DD or MM/DD/YYYY (e.g. `02/25` or `02/25/2026`).",
                ephemeral=True,
            )
            return

        parsed_time = parse_time(time)
        if not parsed_time:
            await interaction.followup.send(
                f"Invalid time `{time}`. Use HH:MM or H:MM AM/PM (e.g. `12:30` or `12:30 PM`).",
                ephemeral=True,
            )
            return

        guild_id_str = guild_id_to_str(interaction.guild_id)
        store = LunchboyzRedisStore()
        config = await store.get_config(guild_id_str)
        state = await store.get_state(guild_id_str)

        if not config or not state:
            await interaction.followup.send(
                "Lunch Boyz hasn't been set up yet. Use `/lunchboyz setup` first.",
                ephemeral=True,
            )
            return

        state["event"] = {
            "location": location,
            "date": parsed_date.isoformat(),
            "time": parsed_time,
            "notes": notes,
        }
        await store.save_state(guild_id_str, state)

        display_time_12h = datetime.datetime.strptime(parsed_time, "%H:%M").strftime("%I:%M %p").lstrip("0")

        embed = discord.Embed(title="📅 Next Lunch Boyz", color=0x2ECC71)
        embed.add_field(name="📍 Location", value=location, inline=False)
        embed.add_field(
            name="🗓️ When",
            value=f"{parsed_date.strftime('%m/%d/%Y')} at {display_time_12h}",
            inline=False,
        )
        if notes:
            embed.add_field(name="📝 Notes", value=notes, inline=False)
        embed.set_footer(text=f"Planned by {interaction.user.display_name}")

        channel_id = int(config["channel_id"])
        channel = self.bot.get_channel(channel_id)
        if channel and isinstance(channel, discord.TextChannel):
            await channel.send(embed=embed)

        await interaction.followup.send("Event posted!", ephemeral=True)

    # ------------------------------------------------------------------
    # /lunchboyz advance
    # ------------------------------------------------------------------

    @lunchboyz.command(
        name="advance",
        description="Manually advance the rotation to the next person.",
    )
    async def advance(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        guild_id_str = guild_id_to_str(interaction.guild_id)
        store = LunchboyzRedisStore()
        config = await store.get_config(guild_id_str)
        rotation = await store.get_rotation(guild_id_str)
        state = await store.get_state(guild_id_str)

        if not config or not rotation or not state:
            await interaction.followup.send(
                "Lunch Boyz hasn't been set up yet. Use `/lunchboyz setup` first.",
                ephemeral=True,
            )
            return

        current_idx = state.get("current_index", 0)
        new_idx = (current_idx + 1) % len(rotation)
        state["current_index"] = new_idx
        state["last_advanced"] = datetime.date.today().isoformat()
        state["event"] = None
        state["reminders_sent"] = []
        await store.save_state(guild_id_str, state)

        next_id = rotation[new_idx]
        next_name = get_member_name(interaction.guild, next_id)

        channel_id = int(config["channel_id"])
        channel = self.bot.get_channel(channel_id)
        if channel and isinstance(channel, discord.TextChannel):
            embed = discord.Embed(
                title="🔄 Rotation Update — Lunch Boyz",
                description=(
                    f"<@{next_id}> ({next_name}), you're up! "
                    "Pick a spot and use `/lunchboyz plan` to let the crew know."
                ),
                color=0x3498DB,
            )
            await channel.send(embed=embed)

        await interaction.followup.send("Rotation advanced.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LunchboyzCog(bot))
