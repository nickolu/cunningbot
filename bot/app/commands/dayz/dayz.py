"""DayZ server monitoring: /dayz server register | unregister | info | watch | unwatch.

`register` finds the server's Steam query port and saves it. `info` queries
every registered server live. `watch` binds a server to a channel whose name
the dayz_channel_updater worker rewrites with the live player count.
"""

import asyncio
import datetime as dt
from typing import Dict, Optional, Union

import discord
from discord import app_commands
from discord.ext import commands

from bot.api.steam.a2s_client import A2SError, ServerInfo, discover_query_port, query_server_info
from bot.app.redis.dayz_store import DayzRedisStore
from bot.app.redis.serialization import channel_id_to_str, guild_id_to_str
from bot.app.utils.logger import get_logger
from bot.domain.dayz import dayz_service as svc

logger = get_logger()

EMBED_COLOR = 0x4E7A3A  # DayZ olive
WATCHABLE_CHANNEL = Union[discord.VoiceChannel, discord.TextChannel, discord.StageChannel]


class DayzCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._store: Optional[DayzRedisStore] = None

    @property
    def store(self) -> DayzRedisStore:
        if self._store is None:
            self._store = DayzRedisStore()
        return self._store

    dayz = app_commands.Group(name="dayz", description="DayZ server monitoring")
    server = app_commands.Group(name="server", description="Registered DayZ servers", parent=dayz)

    # ------------------------------------------------------------------ register

    @server.command(name="register", description="Register a DayZ server to monitor")
    @app_commands.describe(
        address="Game address as IP:PORT, e.g. 51.222.245.31:2502",
        name="Display name (defaults to the address)",
        query_port="Steam query port, if you know it (otherwise it is discovered)",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def register(
        self,
        interaction: discord.Interaction,
        address: str,
        name: Optional[str] = None,
        query_port: Optional[app_commands.Range[int, 1, 65535]] = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            host, game_port = svc.parse_address(address)
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        display_name = (name or f"{host}:{game_port}").strip()
        key = svc.server_key(display_name)
        if not key:
            await interaction.followup.send("That name is empty.", ephemeral=True)
            return
        guild_id = guild_id_to_str(interaction.guild_id)
        if await self.store.get_server(guild_id, key):
            await interaction.followup.send(
                f"A server named **{display_name}** is already registered. "
                "Unregister it first or pick another name.", ephemeral=True,
            )
            return

        try:
            if query_port is not None:
                info: Optional[ServerInfo] = await query_server_info(host, query_port)
            else:
                info = await discover_query_port(host, game_port)
        except A2SError as exc:
            await interaction.followup.send(
                f"No reply from `{host}:{query_port}`: {exc}", ephemeral=True
            )
            return
        except Exception as exc:  # pragma: no cover - network edge cases
            logger.error(f"/dayz server register failed for {host}:{game_port}: {exc}")
            await interaction.followup.send(f"Couldn't query that server: {exc}", ephemeral=True)
            return

        if info is None or info.query_port is None:
            await interaction.followup.send(
                f"Nothing answered for `{host}:{game_port}`. The server may be down, or its "
                "query port is unusual — pass it with the `query_port` option.",
                ephemeral=True,
            )
            return

        record = svc.new_server_record(
            display_name, host, game_port, info.query_port, interaction.user.id
        )
        await self.store.save_server(guild_id, key, record)

        embed = discord.Embed(title=f"Registered {display_name}", color=EMBED_COLOR)
        embed.add_field(name="Address", value=f"`{host}:{game_port}` (query `{info.query_port}`)", inline=False)
        embed.add_field(name="Reports as", value=info.name or "—", inline=False)
        embed.add_field(name="Right now", value=f"{info.players}/{info.max_players} players on {info.map}", inline=False)
        embed.set_footer(text="Use /dayz server watch to show this in a channel name.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---------------------------------------------------------------- unregister

    @server.command(name="unregister", description="Stop monitoring a DayZ server")
    @app_commands.describe(name="The server's display name")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def unregister(self, interaction: discord.Interaction, name: str) -> None:
        await interaction.response.defer(ephemeral=True)
        guild_id = guild_id_to_str(interaction.guild_id)
        key = svc.server_key(name)
        record = await self.store.get_server(guild_id, key)
        if not record:
            await interaction.followup.send(f"No server named **{name}** is registered.", ephemeral=True)
            return
        removed = await self.store.delete_watches_for_server(guild_id, key)
        await self.store.delete_server(guild_id, key)
        note = f" Removed {len(removed)} channel watch(es) too." if removed else ""
        await interaction.followup.send(f"Unregistered **{record['name']}**.{note}", ephemeral=True)

    # ---------------------------------------------------------------------- info

    @server.command(name="info", description="Live status of monitored DayZ servers")
    @app_commands.describe(name="Only servers whose name contains this")
    async def info(self, interaction: discord.Interaction, name: Optional[str] = None) -> None:
        await interaction.response.defer()
        guild_id = guild_id_to_str(interaction.guild_id)
        servers = await self.store.get_all_servers(guild_id)
        if name:
            needle = svc.server_key(name)
            servers = {k: v for k, v in servers.items() if needle in k}
        if not servers:
            msg = (
                f"No registered server matches **{name}**." if name
                else "No DayZ servers are registered. An admin can add one with `/dayz server register`."
            )
            await interaction.followup.send(msg, ephemeral=True)
            return

        embed = await self.build_status_embed(servers)
        await interaction.followup.send(embed=embed)

    async def build_status_embed(self, servers: Dict[str, Dict]) -> discord.Embed:
        ordered = sorted(servers.values(), key=lambda r: r["name"].lower())
        results = await asyncio.gather(
            *(query_server_info(r["host"], r["query_port"]) for r in ordered),
            return_exceptions=True,
        )
        embed = discord.Embed(title="DayZ servers", color=EMBED_COLOR, timestamp=dt.datetime.now(dt.timezone.utc))
        for record, result in zip(ordered, results):
            addr = f"`{record['host']}:{record['game_port']}`"
            if isinstance(result, ServerInfo):
                value = (
                    f"{svc.ONLINE_EMOJI} **{result.players}/{result.max_players}** players · {result.map}\n"
                    f"{result.name}\n{addr} · v{result.version}"
                    + (" · 🔒" if result.password else "")
                )
            else:
                value = f"{svc.OFFLINE_EMOJI} no reply\n{addr}"
            embed.add_field(name=record["name"], value=value, inline=False)
        embed.set_footer(text="Queried live")
        return embed

    # --------------------------------------------------------------------- watch

    @server.command(name="watch", description="Keep a channel's name updated with a server's player count")
    @app_commands.describe(
        name="Registered server name",
        channel="Channel to rename (a voice channel keeps capitals and spaces)",
        notify="Text channel for online/offline notices",
        template="Name template; placeholders: {emoji} {name} {players} {max} {map} {time}",
        timezone="IANA timezone for {time}, e.g. America/New_York",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def watch(
        self,
        interaction: discord.Interaction,
        name: str,
        channel: WATCHABLE_CHANNEL,
        notify: Optional[discord.TextChannel] = None,
        template: Optional[str] = None,
        timezone: Optional[str] = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        guild_id = guild_id_to_str(interaction.guild_id)
        key = svc.server_key(name)
        record = await self.store.get_server(guild_id, key)
        if not record:
            await interaction.followup.send(f"No server named **{name}** is registered.", ephemeral=True)
            return

        channel_id = channel_id_to_str(channel.id)
        existing = await self.store.get_watch(guild_id, channel_id)
        if existing:
            await interaction.followup.send(
                f"{channel.mention} already shows **{existing['server']}**. "
                "Unwatch it first — one server per channel.", ephemeral=True,
            )
            return

        template = (template or svc.DEFAULT_TEMPLATE).strip()
        problem = svc.validate_template(template)
        if problem:
            await interaction.followup.send(problem, ephemeral=True)
            return
        if timezone and svc.resolve_timezone(timezone).key == "UTC" and timezone != "UTC":
            await interaction.followup.send(f"Unknown timezone `{timezone}`.", ephemeral=True)
            return

        # The gateway runs without the members intent, so the cached copy of
        # the bot's own member can have stale roles. Ask Discord for it fresh;
        # if that fails, skip the pre-check and let the rename below decide.
        me = None
        try:
            me = await channel.guild.fetch_member(self.bot.user.id)
        except (discord.HTTPException, AttributeError) as exc:
            logger.warning(f"/dayz server watch: could not fetch own member: {exc}")
        if me is not None:
            perms = channel.permissions_for(me)
            if not perms.manage_channels:
                if isinstance(channel, (discord.VoiceChannel, discord.StageChannel)) and not perms.connect:
                    msg = (
                        f"I can't rename {channel.mention}: Discord takes **Manage Channels** away on a "
                        "voice channel from anyone who can't **Connect** to it. In the channel's "
                        "permissions, allow **Connect** for my role as well (I never actually join)."
                    )
                else:
                    msg = (
                        f"I can't rename {channel.mention} — I need **Manage Channels** on that channel "
                        "(channel settings → Permissions → add the bot's role)."
                    )
                await interaction.followup.send(msg, ephemeral=True)
                return

        watch = svc.new_watch_record(
            key, template, timezone, channel_id_to_str(notify.id) if notify else None
        )
        watch["original_name"] = channel.name

        # Rename right away so the result is visible now, not at the next tick.
        first_result = await self._apply_first_rename(channel, record, watch)
        if first_result == "forbidden":
            await interaction.followup.send(
                f"Discord refused to let me rename {channel.mention}. I need **Manage Channels** "
                "on that channel. Nothing was saved.", ephemeral=True
            )
            return
        await self.store.save_watch(guild_id, channel_id, watch)

        lines = [
            f"{channel.mention} will track **{record['name']}**, refreshed about every 6 minutes.",
            f"Template: `{template}`",
        ]
        if first_result == "renamed":
            lines.append(f"Renamed it to **{watch['last_name']}** just now.")
        elif first_result == "no_reply":
            lines.append("The server didn't answer just now; the name updates at the next tick.")
        else:
            lines.append("The rename didn't go through just now; the worker retries at the next tick.")
        if notify:
            lines.append(f"Online/offline notices go to {notify.mention}.")
        if isinstance(channel, discord.TextChannel):
            lines.append(
                "Heads up: Discord lower-cases text channel names and replaces spaces with dashes. "
                "A voice channel keeps the name as rendered."
            )
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    async def _apply_first_rename(
        self, channel: discord.abc.GuildChannel, record: Dict, watch: Dict
    ) -> str:
        """Query the server once and rename the channel now.

        Returns "renamed", "forbidden", "no_reply", or "failed". Updates `watch`
        in place so the worker sees the state the channel is actually in.
        """
        try:
            info = await query_server_info(record["host"], int(record["query_port"]))
        except Exception as exc:
            logger.info(f"/dayz server watch: first query of {record['name']} failed: {exc}")
            return "no_reply"
        now = dt.datetime.now(svc.resolve_timezone(watch.get("timezone")))
        new_name = svc.render_online_name(watch["template"], record["name"], info, now)
        try:
            await channel.edit(name=new_name, reason="DayZ server status")
        except discord.Forbidden:
            return "forbidden"
        except discord.HTTPException as exc:
            logger.warning(f"/dayz server watch: first rename of {channel.id} failed: {exc}")
            return "failed"
        watch.update(
            state=svc.STATE_ONLINE, failures=0, last_name=new_name,
            last_players=info.players, last_checked_at=now.isoformat(),
        )
        return "renamed"

    # ------------------------------------------------------------------- unwatch

    @server.command(name="unwatch", description="Stop updating a channel's name")
    @app_commands.describe(
        channel="The channel being renamed",
        restore_name="Rename the channel back to what it was called before the watch (default yes)",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def unwatch(
        self,
        interaction: discord.Interaction,
        channel: WATCHABLE_CHANNEL,
        restore_name: bool = True,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        guild_id = guild_id_to_str(interaction.guild_id)
        channel_id = channel_id_to_str(channel.id)
        watch = await self.store.get_watch(guild_id, channel_id)
        if not watch:
            await interaction.followup.send(f"{channel.mention} isn't watching anything.", ephemeral=True)
            return
        await self.store.delete_watch(guild_id, channel_id)

        original = (watch.get("original_name") or "").strip()
        msg = f"Stopped updating {channel.mention}."
        if restore_name and original and original != channel.name:
            try:
                await channel.edit(name=original, reason="DayZ watch removed")
                msg += f" Renamed it back to **{original}**."
            except discord.HTTPException as exc:
                logger.warning(f"/dayz server unwatch: could not restore name of {channel_id}: {exc}")
                msg += f" Couldn't rename it back to **{original}** ({exc.text or exc}); rename it by hand."
        else:
            msg += " Its name stays as it is."
        await interaction.followup.send(msg, ephemeral=True)

    # --------------------------------------------------------------- error hook

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            msg = "You need **Manage Server** to do that."
        else:
            logger.error(f"/dayz command error: {error}")
            msg = f"Something went wrong: {error}"
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DayzCog(bot))
