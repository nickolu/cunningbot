"""/framed — track results for the daily framed.wtf movie game.

The bot reads a registered results channel once each day is over (see
bot/app/tasks/framed_sync.py) and computes stats from what it saved.
"""

from datetime import date, datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot.app.framed_runtime import (
    EMBED_COLOR, add_warning, run_sync, start_backfill,
)
from bot.app.redis.exceptions import LockAcquisitionError
from bot.app.redis.framed_store import FramedRedisStore
from bot.app.redis.serialization import guild_id_to_str
from bot.app.utils.logger import get_logger
from bot.domain.framed import stats_service
from bot.domain.framed.puzzle import (
    DEFAULT_TIMEZONE, FAIL, FRAMED_EPOCH, get_tz, latest_complete_day,
    local_date, puzzle_for_date,
)
from bot.domain.framed.stats import head_to_head, player_stats
from bot.domain.framed.sync_service import prepare_backfill

logger = get_logger()

NOT_REGISTERED = (
    "Framed tracking isn't set up here. An admin can run `/framed register` "
    "in the channel where results are posted."
)

PERIOD_CHOICES = [
    app_commands.Choice(name="Last 7 days", value="week"),
    app_commands.Choice(name="This month", value="month"),
    app_commands.Choice(name="This year", value="year"),
    app_commands.Choice(name="All time", value="all"),
]

SCORE_CHOICES = [app_commands.Choice(name=str(n), value=str(n)) for n in range(1, 7)] + [
    app_commands.Choice(name="X (missed)", value="X"),
    app_commands.Choice(name="Didn't play (remove result)", value="remove"),
    app_commands.Choice(name="Undo my fix (use what was posted)", value="undo"),
]


def _plural(n: int, word: str) -> str:
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


class FramedCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    framed = app_commands.Group(name="framed", description="Framed movie game stats")

    async def _load(self, interaction: discord.Interaction) -> Optional[stats_service.FramedData]:
        store = FramedRedisStore()
        data = await stats_service.load(store, guild_id_to_str(interaction.guild_id))
        if data is None:
            await interaction.followup.send(NOT_REGISTERED, ephemeral=True)
        return data

    # --- Setup ---

    @framed.command(name="register", description="Track Framed results posted in a channel (Admin)")
    @app_commands.describe(
        channel="Where results are posted (default: this channel)",
        recap="Post yesterday's results each morning (default: on)",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def register(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
        recap: bool = True,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        target = channel or interaction.channel
        if not isinstance(target, (discord.TextChannel, discord.Thread)):
            await interaction.followup.send("Pick a text channel.", ephemeral=True)
            return
        store = FramedRedisStore()
        guild_id = guild_id_to_str(interaction.guild_id)
        existing = await store.get_config(guild_id) or {}
        tz = get_tz(existing.get("timezone") or DEFAULT_TIMEZONE)
        yesterday = latest_complete_day(datetime.now(timezone.utc), tz)
        config = {
            "channel_id": str(target.id),
            "timezone": existing.get("timezone") or DEFAULT_TIMEZONE,
            "recap": recap,
            # Keep history already tracked; otherwise start with yesterday.
            "first_day": existing.get("first_day") or yesterday.isoformat(),
            "registered_at": datetime.now(timezone.utc).isoformat(),
        }
        await store.save_config(guild_id, config)
        await interaction.followup.send(
            f"✅ Tracking Framed results in {target.mention} (Pacific time). "
            f"Each day is read shortly after midnight"
            f"{' and recapped in that channel' if recap else ''}.\n"
            f"Run `/framed backfill` to read the channel's older results.",
            ephemeral=True,
        )

    @framed.command(name="unregister", description="Stop tracking Framed results (Admin)")
    @app_commands.checks.has_permissions(administrator=True)
    async def unregister(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        removed = await FramedRedisStore().delete_config(guild_id_to_str(interaction.guild_id))
        await interaction.followup.send(
            "Stopped tracking. Saved results are kept, so registering again picks up where it left off."
            if removed else NOT_REGISTERED,
            ephemeral=True,
        )

    @framed.command(name="status", description="Show whether Framed results are up to date")
    async def status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        data = await self._load(interaction)
        if data is None:
            return
        st = data.status
        lines = [
            f"**Channel:** <#{data.config['channel_id']}>",
            f"**Tracking since:** {data.config.get('first_day')}",
            f"**Daily recap:** {'on' if data.config.get('recap', True) else 'off'}",
            f"**Read through:** {data.as_of.isoformat()}",
        ]
        if data.pending:
            shown = ", ".join(d.isoformat() for d in data.pending[:10])
            if len(data.pending) > 10:
                shown += ", …"
            lines.append(f"**Waiting to be read:** {_plural(len(data.pending), 'day')} ({shown})")
        else:
            lines.append("**Waiting to be read:** none ✅")
        if data.unreadable:
            lines.append(
                "**Couldn't read posts on:** "
                + ", ".join(d.isoformat() for d in data.unreadable[-10:])
                + " — run `/framed backfill` for those dates to retry, or `/framed fix`."
            )
        lines.append(f"**Sync worker last ran:** {_ago(st.get('last_worker_run_at'))}")
        lines.append(f"**Last successful read:** {_ago(st.get('last_sync_at'))}")
        if st.get("last_error"):
            lines.append(f"**Last error** ({_ago(st.get('last_error_at'))}): {st['last_error']}")
        warning = stats_service.sync_warning(data)
        if warning:
            lines.append("")
            lines.append(warning)
        embed = discord.Embed(title="Framed tracking status", description="\n".join(lines), color=EMBED_COLOR)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @framed.command(name="sync", description="Read any Framed days the bot hasn't read yet, now")
    async def sync(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        store = FramedRedisStore()
        guild_id = guild_id_to_str(interaction.guild_id)
        if not await store.get_config(guild_id):
            await interaction.followup.send(NOT_REGISTERED, ephemeral=True)
            return
        try:
            report = await run_sync(self.bot, store, guild_id)
        except LockAcquisitionError:
            await interaction.followup.send(
                "A sync is already running. Check `/framed status` in a few minutes.", ephemeral=True)
            return
        except Exception as e:
            logger.error(f"/framed sync failed: {e}")
            await interaction.followup.send(f"❌ Sync failed: {e}", ephemeral=True)
            return
        if not report.synced_days:
            text = "Already up to date ✅"
        else:
            text = f"✅ Read {_plural(len(report.synced_days), 'day')}."
        if report.llm_failed_days:
            text += (f"\n⚠️ Couldn't read some posts on {_plural(len(report.llm_failed_days), 'day')}; "
                     "they'll be retried automatically.")
        if report.remaining_days:
            text += (f"\n{_plural(report.remaining_days, 'day')} still to go — the sync worker will "
                     "continue, or run `/framed sync` again.")
        await interaction.followup.send(text, ephemeral=True)

    @framed.command(name="backfill", description="Re-read Framed results from the channel's history (Admin)")
    @app_commands.describe(
        start="First date to read, YYYY-MM-DD (default: when the channel was created)",
        end="Last date to read, YYYY-MM-DD (default: yesterday)",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def backfill(
        self,
        interaction: discord.Interaction,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        store = FramedRedisStore()
        guild_id = guild_id_to_str(interaction.guild_id)
        config = await store.get_config(guild_id)
        if not config:
            await interaction.followup.send(NOT_REGISTERED, ephemeral=True)
            return
        tz = get_tz(config.get("timezone"))
        yesterday = latest_complete_day(datetime.now(timezone.utc), tz)
        try:
            if start:
                start_day = date.fromisoformat(start.strip())
            else:
                results_channel = self.bot.get_channel(int(config["channel_id"])) or \
                    await self.bot.fetch_channel(int(config["channel_id"]))
                start_day = max(local_date(results_channel.created_at, tz), FRAMED_EPOCH)
            end_day = date.fromisoformat(end.strip()) if end else yesterday
        except ValueError:
            await interaction.followup.send("Dates must look like 2026-09-16.", ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.followup.send(f"Couldn't open the results channel: {e}", ephemeral=True)
            return
        end_day = min(end_day, yesterday)
        if start_day > end_day:
            await interaction.followup.send("The start date is after the end date.", ephemeral=True)
            return

        days = await prepare_backfill(store, guild_id, start_day, end_day)
        if not isinstance(interaction.channel, discord.abc.Messageable):
            await interaction.followup.send("Run this in a text channel.", ephemeral=True)
            return
        # Progress goes in a normal message: interaction followups expire after 15 minutes.
        status_message = await interaction.channel.send(
            f"⏳ Framed backfill: reading {_plural(days, 'day')} "
            f"({start_day.isoformat()} to {end_day.isoformat()})…"
        )
        start_backfill(self.bot, store, guild_id, status_message)
        await interaction.followup.send(
            "Backfill started. Progress is posted in this channel.", ephemeral=True)

    @framed.command(name="fix", description="Correct someone's Framed result for a day (Admin)")
    @app_commands.describe(
        player="Whose result to fix",
        score="The right result",
        day="Puzzle number or date YYYY-MM-DD (default: yesterday)",
    )
    @app_commands.choices(score=SCORE_CHOICES)
    @app_commands.checks.has_permissions(administrator=True)
    async def fix(
        self,
        interaction: discord.Interaction,
        player: discord.Member,
        score: app_commands.Choice[str],
        day: Optional[str] = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        store = FramedRedisStore()
        guild_id = guild_id_to_str(interaction.guild_id)
        data = await self._load(interaction)
        if data is None:
            return
        target = stats_service.parse_day(day, data.latest_day)
        if target is None:
            await interaction.followup.send("Give a puzzle number like 1650 or a date like 2026-09-16.", ephemeral=True)
            return
        uid = str(player.id)
        if score.value == "undo":
            removed = await store.delete_override(guild_id, target.isoformat(), uid)
            text = "Removed the fix." if removed else "There was no fix to undo."
        else:
            value = None if score.value == "remove" else (FAIL if score.value == "X" else int(score.value))
            await store.set_override(guild_id, target.isoformat(), uid, {
                "score": value,
                "by": str(interaction.user.id),
                "at": datetime.now(timezone.utc).isoformat(),
            })
            await store.set_players(guild_id, {uid: player.display_name})
            text = (
                f"Removed {player.display_name}'s result"
                if value is None else
                f"Set {player.display_name} to {stats_service.points_label(value)}"
            )
        await interaction.followup.send(
            f"{text} for Framed #{puzzle_for_date(target)} ({target.isoformat()}).", ephemeral=True)

    # --- Stats ---

    @framed.command(name="stats", description="A player's Framed stats and streaks")
    @app_commands.describe(player="Whose stats (default: you)")
    async def stats(self, interaction: discord.Interaction, player: Optional[discord.Member] = None) -> None:
        await interaction.response.defer()
        data = await self._load(interaction)
        if data is None:
            return
        member = player or interaction.user
        uid = str(member.id)
        data.names.setdefault(uid, member.display_name)
        ps = player_stats(data.scores, uid, data.as_of)
        embed = discord.Embed(
            title=f"🎬 Framed stats — {data.name(uid)}",
            description=stats_service.format_player(data, ps),
            color=EMBED_COLOR,
        )
        await interaction.followup.send(embed=add_warning(embed, data))

    @framed.command(name="leaderboard", description="Framed rankings by total points")
    @app_commands.describe(period="Time range (default: this year)", year="A specific past year, e.g. 2024")
    @app_commands.choices(period=PERIOD_CHOICES)
    async def leaderboard(
        self,
        interaction: discord.Interaction,
        period: Optional[app_commands.Choice[str]] = None,
        year: Optional[int] = None,
    ) -> None:
        await interaction.response.defer()
        data = await self._load(interaction)
        if data is None:
            return
        label, rows = stats_service.leaderboard_for(data, period.value if period else "year", year)
        embed = discord.Embed(
            title=f"🏆 Framed leaderboard — {label}",
            description=stats_service.format_leaderboard(rows, data),
            color=EMBED_COLOR,
        )
        await interaction.followup.send(embed=add_warning(embed, data))

    @framed.command(name="day", description="One day's Framed ranking")
    @app_commands.describe(day="Puzzle number or date YYYY-MM-DD (default: yesterday)")
    async def day(self, interaction: discord.Interaction, day: Optional[str] = None) -> None:
        await interaction.response.defer()
        data = await self._load(interaction)
        if data is None:
            return
        target = stats_service.parse_day(day, data.latest_day)
        if target is None:
            await interaction.followup.send("Give a puzzle number like 1650 or a date like 2026-09-16.", ephemeral=True)
            return
        title, body = stats_service.format_day(data, target)
        embed = discord.Embed(title=f"🎬 {title}", description=body, color=EMBED_COLOR)
        await interaction.followup.send(embed=add_warning(embed, data))

    @framed.command(name="h2h", description="Head-to-head Framed record between two players")
    @app_commands.describe(player="Opponent", other="Second player (default: you)")
    async def h2h(
        self,
        interaction: discord.Interaction,
        player: discord.Member,
        other: Optional[discord.Member] = None,
    ) -> None:
        await interaction.response.defer()
        data = await self._load(interaction)
        if data is None:
            return
        a = str((other or interaction.user).id)
        b = str(player.id)
        data.names.setdefault(a, (other or interaction.user).display_name)
        data.names.setdefault(b, player.display_name)
        embed = discord.Embed(
            title=f"⚔️ {data.name(a)} vs {data.name(b)}",
            description=stats_service.format_head_to_head(data, a, b, head_to_head(data.scores, a, b)),
            color=EMBED_COLOR,
        )
        await interaction.followup.send(embed=add_warning(embed, data))


def _ago(iso: Optional[str]) -> str:
    if not iso:
        return "never"
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    minutes = int((datetime.now(timezone.utc) - then).total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 120:
        return f"{minutes} min ago"
    if minutes < 48 * 60:
        return f"{minutes // 60} hours ago"
    return f"{minutes // 1440} days ago"


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(FramedCog(bot))
