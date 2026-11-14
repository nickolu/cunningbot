"""Tests for DayZ server monitoring: A2S parsing, the domain service, the cog, the worker."""

import asyncio
import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import discord
import pytest

from bot.api.steam import a2s_client
from bot.api.steam.a2s_client import A2SError, ServerInfo, parse_info_response
from bot.domain.dayz import dayz_service as svc


def _info(**over) -> ServerInfo:
    base = dict(
        name="DayzUnderground.com - Chernarus", map="chernarusplus", game="", players=32,
        max_players=95, bots=0, version="1.30.164289", password=True, vac=True,
        game_port=2502, query_port=27015,
    )
    base.update(over)
    return ServerInfo(**base)


def _build_info_packet(players=32, max_players=95, game_port=2502, with_edf=True) -> bytes:
    body = b"\xFF\xFF\xFF\xFFI\x11"
    body += b"DUG Chernarus\x00" + b"chernarusplus\x00" + b"dayz\x00" + b"DayZ\x00"
    body += (0).to_bytes(2, "little")
    body += bytes([players, max_players, 0]) + b"dw" + bytes([1, 1])
    body += b"1.30.164289\x00"
    if with_edf:
        body += bytes([0x80 | 0x20]) + game_port.to_bytes(2, "little") + b"battleye,mod\x00"
    return body


# --------------------------------------------------------------------------- A2S


class TestA2SParsing:
    def test_parses_full_response(self):
        info = parse_info_response(_build_info_packet())
        assert info.name == "DUG Chernarus"
        assert info.map == "chernarusplus"
        assert (info.players, info.max_players) == (32, 95)
        assert info.version == "1.30.164289"
        assert info.password and info.vac
        assert info.game_port == 2502
        assert info.keywords == "battleye,mod"

    def test_response_without_extra_data(self):
        info = parse_info_response(_build_info_packet(with_edf=False))
        assert info.game_port is None
        assert info.players == 32

    def test_rejects_wrong_header(self):
        with pytest.raises(A2SError):
            parse_info_response(b"\xFF\xFF\xFF\xFFA\x01\x02\x03\x04")

    def test_rejects_truncated(self):
        with pytest.raises(A2SError):
            parse_info_response(_build_info_packet()[:20])

    def test_candidate_ports_prefer_conventional_offset(self):
        ports = a2s_client.candidate_query_ports(2502)
        assert ports[0] == 2502 + 24714
        assert 27015 in ports
        assert len(ports) == len(set(ports))


class TestDiscovery:
    @pytest.mark.asyncio
    async def test_picks_responder_with_matching_game_port(self):
        async def fake_query(host, port, timeout):
            if port == 27014:
                return _info(game_port=2402, query_port=27014)
            if port == 27015:
                return _info(game_port=2502, query_port=27015)
            raise A2SError("timeout")

        with patch.object(a2s_client, "query_server_info", side_effect=fake_query):
            found = await a2s_client.discover_query_port("h", 2502)
        assert found is not None and found.query_port == 27015

    @pytest.mark.asyncio
    async def test_falls_back_to_conventional_port_when_no_game_port_reported(self):
        async def fake_query(host, port, timeout):
            if port == 2502 + 24714:
                return _info(game_port=None, query_port=port)
            raise A2SError("timeout")

        with patch.object(a2s_client, "query_server_info", side_effect=fake_query):
            found = await a2s_client.discover_query_port("h", 2502)
        assert found is not None and found.query_port == 27216

    @pytest.mark.asyncio
    async def test_none_when_nothing_answers(self):
        async def fake_query(host, port, timeout):
            raise A2SError("timeout")

        with patch.object(a2s_client, "query_server_info", side_effect=fake_query):
            assert await a2s_client.discover_query_port("h", 2502) is None


# ----------------------------------------------------------------------- service


class TestService:
    def test_parse_address(self):
        assert svc.parse_address(" 51.222.245.31:2502 ") == ("51.222.245.31", 2502)

    @pytest.mark.parametrize("bad", ["51.222.245.31", "host:abc", "host:70000", ":2502", ""])
    def test_parse_address_rejects(self, bad):
        with pytest.raises(ValueError):
            svc.parse_address(bad)

    def test_server_key_normalises(self):
        assert svc.server_key("  Chernarus   Main ") == "chernarus main"

    def test_render_online_name_default_template(self):
        now = dt.datetime(2026, 11, 14, 15, 45, tzinfo=ZoneInfo("America/Los_Angeles"))
        assert svc.render_online_name(svc.DEFAULT_TEMPLATE, "Chernarus", _info(), now) == (
            "🟢 Chernarus 32/95 · 3:45pm"
        )

    def test_render_online_name_custom_template_and_truncation(self):
        now = dt.datetime(2026, 11, 14, 0, 5, tzinfo=ZoneInfo("UTC"))
        name = svc.render_online_name("{map} {players} " + "x" * 200, "C", _info(), now)
        assert name.startswith("chernarusplus 32 ")
        assert len(name) == svc.DISCORD_CHANNEL_NAME_MAX

    def test_render_offline_name_has_no_clock(self):
        assert svc.render_offline_name("Chernarus") == "🔴 Chernarus offline"

    def test_validate_template(self):
        assert svc.validate_template("{emoji} {name}") is None
        assert svc.validate_template("{nope}") is not None
        assert svc.validate_template("{") is not None

    def test_resolve_timezone_falls_back_to_utc(self):
        assert svc.resolve_timezone("Not/AZone").key == "UTC"
        assert svc.resolve_timezone(None).key == svc.DEFAULT_TIMEZONE

    def test_state_machine_needs_three_misses(self):
        state, failures = svc.STATE_ONLINE, 0
        for expected in (svc.STATE_ONLINE, svc.STATE_ONLINE, svc.STATE_OFFLINE):
            state, failures = svc.advance_state(state, failures, success=False)
            assert state == expected
        assert svc.advance_state(state, failures, success=True) == (svc.STATE_ONLINE, 0)

    def test_unknown_stays_unknown_until_threshold(self):
        assert svc.advance_state(svc.STATE_UNKNOWN, 0, False) == (svc.STATE_UNKNOWN, 1)
        assert svc.advance_state(svc.STATE_UNKNOWN, 2, False) == (svc.STATE_OFFLINE, 3)

    def test_should_notify(self):
        assert not svc.should_notify(svc.STATE_UNKNOWN, svc.STATE_ONLINE)
        assert svc.should_notify(svc.STATE_UNKNOWN, svc.STATE_OFFLINE)
        assert svc.should_notify(svc.STATE_ONLINE, svc.STATE_OFFLINE)
        assert svc.should_notify(svc.STATE_OFFLINE, svc.STATE_ONLINE)
        assert not svc.should_notify(svc.STATE_ONLINE, svc.STATE_ONLINE)


# --------------------------------------------------------------------------- cog

SERVER = {"name": "Chernarus", "host": "h", "game_port": 2502, "query_port": 27015}


def _interaction(guild_id=1, user_id=7):
    inter = AsyncMock(spec=discord.Interaction)
    inter.guild_id = guild_id
    inter.user = SimpleNamespace(id=user_id)
    inter.response.defer = AsyncMock()
    inter.followup.send = AsyncMock()
    return inter


def _cog():
    from bot.app.commands.dayz.dayz import DayzCog

    cog = DayzCog(MagicMock())
    store = MagicMock()
    for m in ("get_server", "save_server", "delete_server", "get_all_servers", "get_watch",
              "save_watch", "delete_watch", "delete_watches_for_server"):
        setattr(store, m, AsyncMock())
    cog._store = store
    return cog, store


def _sent(inter):
    call = inter.followup.send.call_args
    return call.args[0] if call.args else call.kwargs.get("embed")


class TestRegisterCommand:
    @pytest.mark.asyncio
    async def test_registers_with_discovered_query_port(self):
        cog, store = _cog()
        store.get_server.return_value = None
        inter = _interaction()
        with patch("bot.app.commands.dayz.dayz.discover_query_port", AsyncMock(return_value=_info())):
            await cog.register.callback(cog, inter, "51.222.245.31:2502", "Chernarus", None)
        key, record = store.save_server.call_args.args[1], store.save_server.call_args.args[2]
        assert key == "chernarus"
        assert record["host"] == "51.222.245.31"
        assert record["game_port"] == 2502
        assert record["query_port"] == 27015
        assert record["name"] == "Chernarus"
        embed = inter.followup.send.call_args.kwargs["embed"]
        assert "Registered Chernarus" in embed.title

    @pytest.mark.asyncio
    async def test_name_defaults_to_address(self):
        cog, store = _cog()
        store.get_server.return_value = None
        inter = _interaction()
        with patch("bot.app.commands.dayz.dayz.discover_query_port", AsyncMock(return_value=_info())):
            await cog.register.callback(cog, inter, "51.222.245.31:2502", None, None)
        assert store.save_server.call_args.args[2]["name"] == "51.222.245.31:2502"

    @pytest.mark.asyncio
    async def test_explicit_query_port_skips_discovery(self):
        cog, store = _cog()
        store.get_server.return_value = None
        inter = _interaction()
        q = AsyncMock(return_value=_info(query_port=27015))
        with patch("bot.app.commands.dayz.dayz.query_server_info", q), \
             patch("bot.app.commands.dayz.dayz.discover_query_port", AsyncMock()) as disc:
            await cog.register.callback(cog, inter, "1.2.3.4:2302", "X", 27015)
        q.assert_awaited_once_with("1.2.3.4", 27015)
        disc.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_bad_address(self):
        cog, store = _cog()
        inter = _interaction()
        await cog.register.callback(cog, inter, "nonsense", None, None)
        store.save_server.assert_not_called()
        assert "IP:PORT" in _sent(inter)

    @pytest.mark.asyncio
    async def test_rejects_duplicate_name(self):
        cog, store = _cog()
        store.get_server.return_value = {"name": "Chernarus"}
        inter = _interaction()
        await cog.register.callback(cog, inter, "1.2.3.4:2302", "chernarus", None)
        store.save_server.assert_not_called()
        assert "already registered" in _sent(inter)

    @pytest.mark.asyncio
    async def test_no_reply_is_not_saved(self):
        cog, store = _cog()
        store.get_server.return_value = None
        inter = _interaction()
        with patch("bot.app.commands.dayz.dayz.discover_query_port", AsyncMock(return_value=None)):
            await cog.register.callback(cog, inter, "1.2.3.4:2302", "X", None)
        store.save_server.assert_not_called()
        assert "Nothing answered" in _sent(inter)


class TestInfoCommand:
    @pytest.mark.asyncio
    async def test_lists_live_status_and_offline(self):
        cog, store = _cog()
        store.get_all_servers.return_value = {
            "chernarus": {"name": "Chernarus", "host": "h", "game_port": 2502, "query_port": 27015},
            "livonia": {"name": "Livonia", "host": "h", "game_port": 2402, "query_port": 27014},
        }
        inter = _interaction()

        async def fake_query(host, port, timeout=3.0):
            if port == 27015:
                return _info()
            raise A2SError("timeout")

        with patch("bot.app.commands.dayz.dayz.query_server_info", side_effect=fake_query):
            await cog.info.callback(cog, inter, None)
        embed = inter.followup.send.call_args.kwargs["embed"]
        names = [f.name for f in embed.fields]
        assert names == ["Chernarus", "Livonia"]
        assert "32/95" in embed.fields[0].value
        assert "no reply" in embed.fields[1].value

    @pytest.mark.asyncio
    async def test_filters_by_name(self):
        cog, store = _cog()
        store.get_all_servers.return_value = {
            "chernarus": {"name": "Chernarus", "host": "h", "game_port": 2502, "query_port": 27015},
            "livonia": {"name": "Livonia", "host": "h", "game_port": 2402, "query_port": 27014},
        }
        inter = _interaction()
        with patch("bot.app.commands.dayz.dayz.query_server_info", AsyncMock(return_value=_info())):
            await cog.info.callback(cog, inter, "LIV")
        embed = inter.followup.send.call_args.kwargs["embed"]
        assert [f.name for f in embed.fields] == ["Livonia"]

    @pytest.mark.asyncio
    async def test_nothing_registered(self):
        cog, store = _cog()
        store.get_all_servers.return_value = {}
        inter = _interaction()
        await cog.info.callback(cog, inter, None)
        assert "No DayZ servers are registered" in _sent(inter)


def _voice_channel(cid=55, name="General", can_manage=True, fetch_fails=False, can_connect=True):
    ch = MagicMock(spec=discord.VoiceChannel)
    ch.id = cid
    ch.name = name
    ch.mention = f"<#{cid}>"
    ch.guild = MagicMock()
    ch.guild.fetch_member = AsyncMock(
        side_effect=discord.HTTPException(MagicMock(status=500), "boom") if fetch_fails else None,
        return_value=SimpleNamespace(id=1),
    )
    ch.permissions_for.return_value = SimpleNamespace(manage_channels=can_manage, connect=can_connect)
    ch.edit = AsyncMock()
    return ch


def _forbidden():
    return discord.Forbidden(MagicMock(status=403), {"code": 50013, "message": "Missing Permissions"})


class TestWatchCommand:
    @pytest.mark.asyncio
    async def test_saves_watch_with_defaults(self):
        cog, store = _cog()
        store.get_server.return_value = dict(SERVER)
        store.get_watch.return_value = None
        inter = _interaction()
        notify = MagicMock(spec=discord.TextChannel)
        notify.id = 99
        notify.mention = "<#99>"
        channel = _voice_channel()
        with patch("bot.app.commands.dayz.dayz.query_server_info", AsyncMock(return_value=_info())):
            await cog.watch.callback(cog, inter, "Chernarus", channel, notify, None, None)
        guild_id, channel_id, watch = store.save_watch.call_args.args
        assert (guild_id, channel_id) == ("1", "55")
        assert watch["server"] == "chernarus"
        assert watch["template"] == svc.DEFAULT_TEMPLATE
        assert watch["timezone"] == svc.DEFAULT_TIMEZONE
        assert watch["notify_channel_id"] == "99"
        assert watch["original_name"] == "General"
        # First rename happens immediately and the watch reflects it
        new_name = channel.edit.call_args.kwargs["name"]
        assert new_name.startswith("🟢 Chernarus 32/95 · ")
        assert watch["state"] == svc.STATE_ONLINE
        assert watch["last_name"] == new_name
        assert "Renamed it to" in _sent(inter)

    @pytest.mark.asyncio
    async def test_permission_check_uses_fresh_member_not_cache(self):
        cog, store = _cog()
        store.get_server.return_value = dict(SERVER)
        store.get_watch.return_value = None
        inter = _interaction()
        channel = _voice_channel()
        with patch("bot.app.commands.dayz.dayz.query_server_info", AsyncMock(return_value=_info())):
            await cog.watch.callback(cog, inter, "Chernarus", channel, None, None, None)
        channel.guild.fetch_member.assert_awaited_once()
        fetched = channel.guild.fetch_member.return_value
        channel.permissions_for.assert_called_once_with(fetched)

    @pytest.mark.asyncio
    async def test_fetch_failure_falls_through_to_rename_attempt(self):
        cog, store = _cog()
        store.get_server.return_value = dict(SERVER)
        store.get_watch.return_value = None
        inter = _interaction()
        channel = _voice_channel(can_manage=False, fetch_fails=True)
        with patch("bot.app.commands.dayz.dayz.query_server_info", AsyncMock(return_value=_info())):
            await cog.watch.callback(cog, inter, "Chernarus", channel, None, None, None)
        channel.permissions_for.assert_not_called()
        channel.edit.assert_awaited_once()
        store.save_watch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_forbidden_rename_saves_nothing(self):
        cog, store = _cog()
        store.get_server.return_value = dict(SERVER)
        store.get_watch.return_value = None
        inter = _interaction()
        channel = _voice_channel()
        channel.edit = AsyncMock(side_effect=_forbidden())
        with patch("bot.app.commands.dayz.dayz.query_server_info", AsyncMock(return_value=_info())):
            await cog.watch.callback(cog, inter, "Chernarus", channel, None, None, None)
        store.save_watch.assert_not_called()
        assert "Discord refused" in _sent(inter)

    @pytest.mark.asyncio
    async def test_no_reply_still_saves_watch(self):
        cog, store = _cog()
        store.get_server.return_value = dict(SERVER)
        store.get_watch.return_value = None
        inter = _interaction()
        channel = _voice_channel()
        with patch("bot.app.commands.dayz.dayz.query_server_info", AsyncMock(side_effect=A2SError("timeout"))):
            await cog.watch.callback(cog, inter, "Chernarus", channel, None, None, None)
        channel.edit.assert_not_called()
        watch = store.save_watch.call_args.args[2]
        assert watch["state"] == svc.STATE_UNKNOWN
        assert "didn't answer" in _sent(inter)

    @pytest.mark.asyncio
    async def test_one_server_per_channel(self):
        cog, store = _cog()
        store.get_server.return_value = dict(SERVER)
        store.get_watch.return_value = {"server": "livonia"}
        inter = _interaction()
        await cog.watch.callback(cog, inter, "Chernarus", _voice_channel(), None, None, None)
        store.save_watch.assert_not_called()
        assert "one server per channel" in _sent(inter)

    @pytest.mark.asyncio
    async def test_rejects_bad_template_and_timezone(self):
        cog, store = _cog()
        store.get_server.return_value = dict(SERVER)
        store.get_watch.return_value = None
        inter = _interaction()
        await cog.watch.callback(cog, inter, "Chernarus", _voice_channel(), None, "{bogus}", None)
        assert "placeholder" in _sent(inter)
        await cog.watch.callback(cog, inter, "Chernarus", _voice_channel(), None, None, "Mars/Olympus")
        assert "Unknown timezone" in _sent(inter)
        store.save_watch.assert_not_called()

    @pytest.mark.asyncio
    async def test_requires_manage_channels_on_target(self):
        cog, store = _cog()
        store.get_server.return_value = dict(SERVER)
        store.get_watch.return_value = None
        inter = _interaction()
        await cog.watch.callback(cog, inter, "Chernarus", _voice_channel(can_manage=False), None, None, None)
        store.save_watch.assert_not_called()
        assert "Manage Channels" in _sent(inter)

    @pytest.mark.asyncio
    async def test_voice_channel_without_connect_explains_the_rule(self):
        cog, store = _cog()
        store.get_server.return_value = dict(SERVER)
        store.get_watch.return_value = None
        inter = _interaction()
        channel = _voice_channel(can_manage=False, can_connect=False)
        await cog.watch.callback(cog, inter, "Chernarus", channel, None, None, None)
        store.save_watch.assert_not_called()
        assert "allow **Connect**" in _sent(inter)

    @pytest.mark.asyncio
    async def test_unwatch_restores_original_name(self):
        cog, store = _cog()
        store.get_watch.return_value = {"server": "chernarus", "original_name": "General"}
        inter = _interaction()
        channel = _voice_channel(name="🟢 Chernarus 32/95 · 3:45pm")
        channel.edit = AsyncMock()
        await cog.unwatch.callback(cog, inter, channel)
        store.delete_watch.assert_awaited_once_with("1", "55")
        channel.edit.assert_awaited_once()
        assert channel.edit.call_args.kwargs["name"] == "General"
        assert "Renamed it back to **General**" in _sent(inter)

    @pytest.mark.asyncio
    async def test_unwatch_can_keep_current_name(self):
        cog, store = _cog()
        store.get_watch.return_value = {"server": "chernarus", "original_name": "General"}
        inter = _interaction()
        channel = _voice_channel(name="🟢 Chernarus 32/95 · 3:45pm")
        channel.edit = AsyncMock()
        await cog.unwatch.callback(cog, inter, channel, False)
        channel.edit.assert_not_called()
        assert "name stays" in _sent(inter)

    @pytest.mark.asyncio
    async def test_unwatch_nothing_to_remove(self):
        cog, store = _cog()
        store.get_watch.return_value = None
        inter = _interaction()
        await cog.unwatch.callback(cog, inter, _voice_channel())
        store.delete_watch.assert_not_called()
        assert "isn't watching anything" in _sent(inter)


class TestUnregisterCommand:
    @pytest.mark.asyncio
    async def test_removes_server_and_its_watches(self):
        cog, store = _cog()
        store.get_server.return_value = {"name": "Chernarus"}
        store.delete_watches_for_server.return_value = ["55"]
        inter = _interaction()
        await cog.unregister.callback(cog, inter, "chernarus")
        store.delete_server.assert_awaited_once_with("1", "chernarus")
        assert "1 channel watch" in _sent(inter)


# ------------------------------------------------------------------------ worker


def _worker_store(server, watch):
    store = MagicMock()
    store.get_server = AsyncMock(return_value=server)
    store.save_watch = AsyncMock()
    store.delete_watch = AsyncMock()
    return store


def _worker_channel(name="General"):
    ch = MagicMock()
    ch.name = name
    ch.edit = AsyncMock()
    ch.send = AsyncMock()
    return ch


class TestWorker:
    @pytest.mark.asyncio
    async def test_renames_when_online(self):
        from bot.app.tasks import dayz_channel_updater as w

        store = _worker_store(SERVER, None)
        channel = _worker_channel()
        client = MagicMock()
        watch = svc.new_watch_record("chernarus", None, "UTC", None)
        with patch.object(w, "fetch_channel", AsyncMock(return_value=channel)), \
             patch.object(w, "query_server_info", AsyncMock(return_value=_info())):
            await w.process_watch(client, store, "1", "55", watch)
        new_name = channel.edit.call_args.kwargs["name"]
        assert new_name.startswith("🟢 Chernarus 32/95 · ")
        saved = store.save_watch.call_args.args[2]
        assert saved["state"] == svc.STATE_ONLINE
        assert saved["last_name"] == new_name
        assert saved["last_players"] == 32

    @pytest.mark.asyncio
    async def test_skips_rename_when_unchanged(self):
        from bot.app.tasks import dayz_channel_updater as w

        store = _worker_store(SERVER, None)
        client = MagicMock()
        watch = svc.new_watch_record("chernarus", "{name} {players}", "UTC", None)
        watch.update(state=svc.STATE_ONLINE, last_name="Chernarus 32")
        channel = _worker_channel(name="Chernarus 32")
        with patch.object(w, "fetch_channel", AsyncMock(return_value=channel)), \
             patch.object(w, "query_server_info", AsyncMock(return_value=_info())):
            await w.process_watch(client, store, "1", "55", watch)
        channel.edit.assert_not_called()

    @pytest.mark.asyncio
    async def test_single_miss_leaves_name_alone(self):
        from bot.app.tasks import dayz_channel_updater as w

        store = _worker_store(SERVER, None)
        channel = _worker_channel()
        watch = svc.new_watch_record("chernarus", None, "UTC", "99")
        watch.update(state=svc.STATE_ONLINE)
        with patch.object(w, "fetch_channel", AsyncMock(return_value=channel)), \
             patch.object(w, "query_server_info", AsyncMock(side_effect=A2SError("timeout"))):
            await w.process_watch(MagicMock(), store, "1", "55", watch)
        channel.edit.assert_not_called()
        channel.send.assert_not_called()
        saved = store.save_watch.call_args.args[2]
        assert (saved["state"], saved["failures"]) == (svc.STATE_ONLINE, 1)

    @pytest.mark.asyncio
    async def test_third_miss_goes_offline_and_notifies(self):
        from bot.app.tasks import dayz_channel_updater as w

        store = _worker_store(SERVER, None)
        channel = _worker_channel()
        notify = _worker_channel()
        watch = svc.new_watch_record("chernarus", None, "UTC", "99")
        watch.update(state=svc.STATE_ONLINE, failures=2)

        async def fetch(client, cid):
            return channel if cid == 55 else notify

        with patch.object(w, "fetch_channel", side_effect=fetch), \
             patch.object(w, "query_server_info", AsyncMock(side_effect=A2SError("timeout"))):
            await w.process_watch(MagicMock(), store, "1", "55", watch)
        assert channel.edit.call_args.kwargs["name"] == "🔴 Chernarus offline"
        embed = notify.send.call_args.kwargs["embed"]
        assert "went offline" in embed.title
        assert store.save_watch.call_args.args[2]["state"] == svc.STATE_OFFLINE

    @pytest.mark.asyncio
    async def test_recovery_notifies_once(self):
        from bot.app.tasks import dayz_channel_updater as w

        store = _worker_store(SERVER, None)
        channel = _worker_channel(name="🔴 Chernarus offline")
        notify = _worker_channel()
        watch = svc.new_watch_record("chernarus", None, "UTC", "99")
        watch.update(state=svc.STATE_OFFLINE, failures=5, last_name="🔴 Chernarus offline")

        async def fetch(client, cid):
            return channel if cid == 55 else notify

        with patch.object(w, "fetch_channel", side_effect=fetch), \
             patch.object(w, "query_server_info", AsyncMock(return_value=_info())):
            await w.process_watch(MagicMock(), store, "1", "55", watch)
        assert "back online" in notify.send.call_args.kwargs["embed"].title
        assert "32/95" in notify.send.call_args.kwargs["embed"].description

    @pytest.mark.asyncio
    async def test_first_online_tick_does_not_notify(self):
        from bot.app.tasks import dayz_channel_updater as w

        store = _worker_store(SERVER, None)
        channel = _worker_channel()
        notify = _worker_channel()
        watch = svc.new_watch_record("chernarus", None, "UTC", "99")

        async def fetch(client, cid):
            return channel if cid == 55 else notify

        with patch.object(w, "fetch_channel", side_effect=fetch), \
             patch.object(w, "query_server_info", AsyncMock(return_value=_info())):
            await w.process_watch(MagicMock(), store, "1", "55", watch)
        notify.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_deleted_channel_removes_watch(self):
        from bot.app.tasks import dayz_channel_updater as w

        store = _worker_store(SERVER, None)
        exc = discord.HTTPException(MagicMock(status=404), {"code": 10003, "message": "Unknown Channel"})
        watch = svc.new_watch_record("chernarus", None, "UTC", None)
        with patch.object(w, "fetch_channel", AsyncMock(side_effect=exc)):
            await w.process_watch(MagicMock(), store, "1", "55", watch)
        store.delete_watch.assert_awaited_once_with("1", "55")
        store.save_watch.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_server_removes_watch(self):
        from bot.app.tasks import dayz_channel_updater as w

        store = _worker_store(None, None)
        watch = svc.new_watch_record("ghost", None, "UTC", None)
        await w.process_watch(MagicMock(), store, "1", "55", watch)
        store.delete_watch.assert_awaited_once_with("1", "55")


# -------------------------------------------------------------------- agent tool


class TestAgentTool:
    @pytest.mark.asyncio
    async def test_reports_live_status(self):
        from bot.domain.agent.tools import dayz_status as tool

        store = MagicMock()
        store.get_all_servers = AsyncMock(return_value={
            "chernarus": {"name": "Chernarus", "host": "h", "game_port": 2502, "query_port": 27015},
        })
        channel = SimpleNamespace(guild=SimpleNamespace(id=1))
        with patch.object(tool, "DayzRedisStore", return_value=store), \
             patch.object(tool, "query_server_info", AsyncMock(return_value=_info())):
            out = await tool.execute_dayz_status({}, channel)
        assert "Chernarus" in out and "32/95 players" in out and "online" in out

    @pytest.mark.asyncio
    async def test_nothing_registered(self):
        from bot.domain.agent.tools import dayz_status as tool

        store = MagicMock()
        store.get_all_servers = AsyncMock(return_value={})
        channel = SimpleNamespace(guild=SimpleNamespace(id=1))
        with patch.object(tool, "DayzRedisStore", return_value=store):
            out = await tool.execute_dayz_status({}, channel)
        assert "No DayZ servers are registered" in out
