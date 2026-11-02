"""Tests for the scan_channel_history tool and the UX around a running scan.

The engine itself is covered by tests/test_scan_engine.py. What is exercised
here is everything a person sees or does: who may start a scan, that starting
one returns straight away instead of waiting hours, the status message and its
progress edits, stopping a scan by word or by reaction, and what the final
report says when a scan finishes, is stopped, or fails.

No network and no Discord: the channel is a fake with an async `send`, the
store is a stub, and the runtime's `start_scan` is patched out — this is about
the messages, not the paging.
"""

import asyncio
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from bot.app import scan_ux
from bot.app.commands.agent.scan_listener import ScanListenerCog, is_stop_word
from bot.app.scan_access import (
    ALLOWED_IDS_ENV, REFUSAL, can_start_scan, set_scan_client,
)
from bot.app.scan_runtime import ScanAlreadyRunning
from bot.app.scan_ux import (
    make_finish_callback, make_progress_callback, post_status, resume_callbacks,
)
from bot.domain.agent.tools.scan_channel_history import (
    TOOL, execute_scan_channel_history, parse_bound,
)
from bot.domain.scan import report
from bot.domain.scan.scan_service import ScanProgress, ScanSummary

GUILD = 111111111111111111
CHANNEL = 222222222222222222
REQUESTER = 333333333333333333
OTHER_USER = 444444444444444444


# --- fakes -------------------------------------------------------------


class FakeMessage:
    def __init__(self, message_id: int = 900, content: str = "") -> None:
        self.id = message_id
        self.content = content
        self.edits: List[str] = []
        self.edit_error: Optional[Exception] = None

    async def edit(self, content: str) -> None:
        if self.edit_error is not None:
            raise self.edit_error
        self.edits.append(content)
        self.content = content


class FakeChannel:
    """A channel that records what the bot said in it."""

    def __init__(self, name: str = "foodchat", readable: bool = True) -> None:
        self.id = CHANNEL
        self.name = name
        self.guild = SimpleNamespace(id=GUILD, name="The Server", me=object(),
                                     text_channels=[self], threads=[])
        self.sent: List[str] = []
        self.messages: List[FakeMessage] = []
        self._readable = readable

    def permissions_for(self, _member: Any) -> Any:
        return SimpleNamespace(read_message_history=self._readable)

    async def send(self, content: str, **kwargs: Any) -> FakeMessage:
        self.sent.append(content)
        message = FakeMessage(900 + len(self.messages), content)
        self.messages.append(message)
        return message


def make_job(**overrides: Any) -> Dict[str, Any]:
    job = {
        "job_id": "abc123",
        "guild_id": str(GUILD),
        "channel_id": str(CHANNEL),
        "requester_id": str(REQUESTER),
        "instruction": "collect every restaurant anyone recommended",
        "status": "running",
        "scanned": 0,
        "matched": 0,
    }
    job.update(overrides)
    return job


def make_items(count: int) -> List[Dict[str, Any]]:
    return [
        {
            "key": "restaurant {}".format(i),
            "text": "someone recommended restaurant {}".format(i),
            "source_url": "https://discord.com/channels/1/2/{}".format(i),
        }
        for i in range(count)
    ]


def make_store(items: Optional[List[Dict[str, Any]]] = None) -> MagicMock:
    store = MagicMock()
    store.get_results = AsyncMock(return_value=items or [])
    store.get_active_job_for_channel = AsyncMock(return_value=None)
    store.get_job = AsyncMock(return_value=None)
    return store


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    """The status-message registry and the gate's client are process-global."""
    scan_ux.STATUS_MESSAGES.clear()
    scan_ux.STATUS_MESSAGE_JOBS.clear()
    monkeypatch.delenv(ALLOWED_IDS_ENV, raising=False)
    set_scan_client(None)
    yield
    scan_ux.STATUS_MESSAGES.clear()
    scan_ux.STATUS_MESSAGE_JOBS.clear()
    set_scan_client(None)


# --- who may start a scan ----------------------------------------------


class TestScanGate:
    @pytest.mark.asyncio
    async def test_allowlisted_user_may_start(self, monkeypatch) -> None:
        monkeypatch.setenv(ALLOWED_IDS_ENV, " {} , 12345 ".format(REQUESTER))
        assert await can_start_scan(SimpleNamespace(id=REQUESTER)) is True

    @pytest.mark.asyncio
    async def test_user_off_the_allowlist_may_not(self, monkeypatch) -> None:
        monkeypatch.setenv(ALLOWED_IDS_ENV, str(REQUESTER))
        assert await can_start_scan(SimpleNamespace(id=OTHER_USER)) is False

    @pytest.mark.asyncio
    async def test_an_allowlist_wins_over_ownership(self, monkeypatch) -> None:
        """With the env var set, is_owner is not consulted at all."""
        monkeypatch.setenv(ALLOWED_IDS_ENV, str(REQUESTER))
        client = SimpleNamespace(is_owner=AsyncMock(return_value=True))
        set_scan_client(client)

        assert await can_start_scan(SimpleNamespace(id=OTHER_USER)) is False
        client.is_owner.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unset_falls_back_to_is_owner(self) -> None:
        owner = SimpleNamespace(id=REQUESTER)
        client = SimpleNamespace(is_owner=AsyncMock(return_value=True))
        set_scan_client(client)

        assert await can_start_scan(owner) is True
        client.is_owner.assert_awaited_once_with(owner)

        client.is_owner.return_value = False
        assert await can_start_scan(SimpleNamespace(id=OTHER_USER)) is False

    @pytest.mark.asyncio
    async def test_empty_env_var_is_the_same_as_unset(self, monkeypatch) -> None:
        monkeypatch.setenv(ALLOWED_IDS_ENV, "   ")
        client = SimpleNamespace(is_owner=AsyncMock(return_value=True))
        set_scan_client(client)
        assert await can_start_scan(SimpleNamespace(id=REQUESTER)) is True

    @pytest.mark.asyncio
    async def test_no_client_and_no_allowlist_refuses(self) -> None:
        """Nothing configured must fail closed, not open."""
        assert await can_start_scan(SimpleNamespace(id=REQUESTER)) is False


# --- the tool ----------------------------------------------------------


class TestScanTool:
    def test_registered_as_opt_in_and_user_aware(self) -> None:
        assert TOOL.default_enabled is False
        assert TOOL.channel_aware is True
        assert TOOL.user_aware is True

    @pytest.mark.asyncio
    async def test_refuses_a_user_who_is_not_allowed(self, monkeypatch) -> None:
        monkeypatch.setenv(ALLOWED_IDS_ENV, str(REQUESTER))
        channel = FakeChannel()

        with patch(
            "bot.domain.agent.tools.scan_channel_history.start_scan", new=AsyncMock()
        ) as start:
            result = await execute_scan_channel_history(
                {"instruction": "find restaurants"},
                channel,
                SimpleNamespace(id=OTHER_USER),
            )

        assert result == REFUSAL
        start.assert_not_awaited()
        assert channel.sent == []

    @pytest.mark.asyncio
    async def test_starts_a_scan_and_returns_without_waiting(self, monkeypatch) -> None:
        monkeypatch.setenv(ALLOWED_IDS_ENV, str(REQUESTER))
        channel = FakeChannel()
        job = make_job()
        running: List[asyncio.Task] = []

        async def fake_start_scan(target, requester_id, instruction, **kwargs):
            # Stand in for a scan that will run for hours: if the tool waited on
            # anything, this test would time out instead of returning.
            running.append(asyncio.ensure_future(asyncio.sleep(30)))
            return job

        with patch(
            "bot.domain.agent.tools.scan_channel_history.start_scan",
            new=AsyncMock(side_effect=fake_start_scan),
        ) as start:
            result = await asyncio.wait_for(
                execute_scan_channel_history(
                    {"instruction": "collect every restaurant anyone recommended"},
                    channel,
                    SimpleNamespace(id=REQUESTER),
                ),
                timeout=2,
            )

        for task in running:
            task.cancel()

        assert "Started scanning #foodchat" in result
        assert "results" in result
        kwargs = start.await_args.kwargs
        assert kwargs["on_progress"] is not None and kwargs["on_finish"] is not None
        assert start.await_args.args[1] == REQUESTER
        # The status message is posted in the channel being scanned.
        assert channel.sent == [
            "🔎 Scanning #foodchat for collect every restaurant anyone recommended "
            "— I'll post when I'm done. Say \"stop\" or react 🛑 to stop it."
        ]
        assert scan_ux.job_for_status_message(channel.messages[0].id) == (
            str(GUILD), "abc123",
        )

    @pytest.mark.asyncio
    async def test_reports_a_scan_that_is_already_running(self, monkeypatch) -> None:
        monkeypatch.setenv(ALLOWED_IDS_ENV, str(REQUESTER))
        channel = FakeChannel()
        active = make_job(scanned=12400, matched=37, instruction="find restaurants")

        with patch(
            "bot.domain.agent.tools.scan_channel_history.start_scan",
            new=AsyncMock(side_effect=ScanAlreadyRunning(active)),
        ):
            result = await execute_scan_channel_history(
                {"instruction": "find restaurants"},
                channel,
                SimpleNamespace(id=REQUESTER),
            )

        assert "already running" in result
        assert "find restaurants" in result       # what it is looking for
        assert "12,400 messages scanned" in result  # and how far it has got
        assert "37 items found" in result
        assert channel.sent == []  # no second status message

    @pytest.mark.asyncio
    async def test_requires_an_instruction(self, monkeypatch) -> None:
        monkeypatch.setenv(ALLOWED_IDS_ENV, str(REQUESTER))
        result = await execute_scan_channel_history(
            {"instruction": "  "}, FakeChannel(), SimpleNamespace(id=REQUESTER)
        )
        assert "no instruction" in result.lower()

    @pytest.mark.asyncio
    async def test_scans_a_named_channel_and_checks_permissions(
        self, monkeypatch
    ) -> None:
        monkeypatch.setenv(ALLOWED_IDS_ENV, str(REQUESTER))
        channel = FakeChannel(readable=False)

        result = await execute_scan_channel_history(
            {"instruction": "find restaurants", "channel_name": "#FoodChat"},
            channel,
            SimpleNamespace(id=REQUESTER),
        )
        assert result == "I can't read the history of #foodchat."

    @pytest.mark.asyncio
    async def test_unknown_channel_name(self, monkeypatch) -> None:
        monkeypatch.setenv(ALLOWED_IDS_ENV, str(REQUESTER))
        result = await execute_scan_channel_history(
            {"instruction": "find restaurants", "channel_name": "nowhere"},
            FakeChannel(),
            SimpleNamespace(id=REQUESTER),
        )
        assert "Could not find a channel" in result

    @pytest.mark.asyncio
    async def test_a_bad_bound_is_a_sentence_not_an_exception(
        self, monkeypatch
    ) -> None:
        monkeypatch.setenv(ALLOWED_IDS_ENV, str(REQUESTER))
        result = await execute_scan_channel_history(
            {"instruction": "find restaurants", "after": "last tuesday"},
            FakeChannel(),
            SimpleNamespace(id=REQUESTER),
        )
        assert "isn't a message ID" in result

    @pytest.mark.asyncio
    async def test_bounds_reach_start_scan(self, monkeypatch) -> None:
        monkeypatch.setenv(ALLOWED_IDS_ENV, str(REQUESTER))
        with patch(
            "bot.domain.agent.tools.scan_channel_history.start_scan",
            new=AsyncMock(return_value=make_job()),
        ) as start:
            await execute_scan_channel_history(
                {
                    "instruction": "find restaurants",
                    "after": "2026-01-01",
                    "before": "https://discord.com/channels/1/2/987654321",
                },
                FakeChannel(),
                SimpleNamespace(id=REQUESTER),
            )
        kwargs = start.await_args.kwargs
        assert kwargs["before"] == 987654321
        assert kwargs["after"] == discord.utils.time_snowflake(
            __import__("datetime").datetime(2026, 1, 1, tzinfo=__import__("datetime").timezone.utc)
        )


class TestParseBound:
    def test_message_id_link_and_date(self) -> None:
        assert parse_bound("123456789")[0] == 123456789
        assert parse_bound("https://discord.com/channels/1/2/42")[0] == 42
        assert parse_bound("")[0] is None
        snowflake, error = parse_bound("2026-01-01")
        assert error is None and snowflake > 0

    def test_iso_with_a_z_suffix(self) -> None:
        snowflake, error = parse_bound("2026-01-01T00:00:00Z")
        assert error is None and snowflake > 0

    def test_nonsense_explains_itself(self) -> None:
        snowflake, error = parse_bound("sometime last year")
        assert snowflake is None
        assert "message ID" in error


# --- status message and progress ---------------------------------------


class TestStatusMessage:
    @pytest.mark.asyncio
    async def test_progress_edits_the_status_message(self) -> None:
        channel = FakeChannel()
        job = make_job()
        await post_status(channel, job)

        on_progress = make_progress_callback(channel)
        await on_progress(job, ScanProgress(scanned=12400, matched=37))

        assert channel.messages[0].edits == [
            "🔎 Scanning #foodchat for collect every restaurant anyone recommended "
            "— 12,400 messages scanned, 37 items found so far. "
            "Say \"stop\" or react 🛑 to stop it."
        ]

    @pytest.mark.asyncio
    async def test_a_deleted_status_message_does_not_stop_the_scan(self) -> None:
        channel = FakeChannel()
        job = make_job()
        await post_status(channel, job)
        channel.messages[0].edit_error = discord.NotFound(
            SimpleNamespace(status=404, reason="Not Found"), "gone"
        )

        on_progress = make_progress_callback(channel)
        await on_progress(job, ScanProgress(scanned=100, matched=1))  # no raise

        # It is forgotten, so later progress doesn't keep hitting a dead message.
        assert scan_ux.job_for_status_message(channel.messages[0].id) is None

    @pytest.mark.asyncio
    async def test_a_resumed_scan_says_it_picked_up_where_it_left_off(self) -> None:
        channel = FakeChannel()
        job = make_job(scanned=12400, matched=37)

        on_progress, on_finish = await resume_callbacks(job, channel)

        assert channel.sent == [
            "🔎 Picking up where I left off scanning #foodchat for collect every "
            "restaurant anyone recommended — 12,400 messages scanned so far, "
            "37 items found. Say \"stop\" or react 🛑 to stop it."
        ]
        assert on_progress is not None and on_finish is not None
        # And the fresh message is the one a 🛑 now applies to.
        assert scan_ux.job_for_status_message(channel.messages[0].id) == (
            str(GUILD), "abc123",
        )


# --- stopping ----------------------------------------------------------


def make_listener(job: Optional[Dict[str, Any]]) -> ScanListenerCog:
    bot = MagicMock()
    bot.user = SimpleNamespace(id=777)
    cog = ScanListenerCog(bot)
    store = make_store()
    store.get_active_job_for_channel = AsyncMock(return_value=job)
    store.get_job = AsyncMock(return_value=job)
    cog._store = store
    return cog


def make_discord_message(content: str, author_id: int, channel: Any) -> MagicMock:
    message = MagicMock()
    message.author.bot = False
    message.author.id = author_id
    message.content = content
    message.guild = SimpleNamespace(id=GUILD)
    message.channel = channel
    return message


class TestStopWord:
    def test_only_a_message_that_is_the_stop_word(self) -> None:
        assert is_stop_word("stop")
        assert is_stop_word("  Stop. ")
        assert is_stop_word("nvm")
        assert is_stop_word("cancel the scan")
        assert not is_stop_word("don't stop believing")
        assert not is_stop_word("")

    @pytest.mark.asyncio
    async def test_requester_stops_the_scan(self) -> None:
        job = make_job()
        cog = make_listener(job)
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = CHANNEL
        channel.send = AsyncMock()

        with patch(
            "bot.app.commands.agent.scan_listener.cancel_channel_scan", new=AsyncMock()
        ) as cancel:
            await cog.on_message(make_discord_message("stop", REQUESTER, channel))

        cancel.assert_awaited_once()
        assert cancel.await_args.args[:2] == (str(GUILD), str(CHANNEL))
        assert "Stopping the scan" in channel.send.await_args.args[0]

    @pytest.mark.asyncio
    async def test_someone_else_saying_stop_does_nothing(self) -> None:
        cog = make_listener(make_job())
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = CHANNEL
        channel.send = AsyncMock()

        with patch(
            "bot.app.commands.agent.scan_listener.cancel_channel_scan", new=AsyncMock()
        ) as cancel:
            await cog.on_message(make_discord_message("stop", OTHER_USER, channel))

        cancel.assert_not_awaited()
        channel.send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_scan_running_means_no_lookup_noise(self) -> None:
        cog = make_listener(None)
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = CHANNEL
        channel.send = AsyncMock()

        with patch(
            "bot.app.commands.agent.scan_listener.cancel_channel_scan", new=AsyncMock()
        ) as cancel:
            await cog.on_message(make_discord_message("stop", REQUESTER, channel))
            # An ordinary message never even asks Redis.
            await cog.on_message(make_discord_message("hello", REQUESTER, channel))

        cancel.assert_not_awaited()
        assert cog.store.get_active_job_for_channel.await_count == 1


class TestStopReaction:
    def _payload(self, user_id: int, message_id: int, emoji: str = "🛑") -> Any:
        return SimpleNamespace(
            emoji=emoji, user_id=user_id, message_id=message_id, channel_id=CHANNEL
        )

    @pytest.mark.asyncio
    async def test_requester_reacting_stops_the_scan(self) -> None:
        channel = FakeChannel()
        job = make_job()
        message = await post_status(channel, job)
        cog = make_listener(job)
        cog.bot.get_channel = MagicMock(return_value=channel)

        with patch(
            "bot.app.commands.agent.scan_listener.cancel_channel_scan", new=AsyncMock()
        ) as cancel:
            await cog.on_raw_reaction_add(self._payload(REQUESTER, message.id))

        cancel.assert_awaited_once()
        assert "Stopping the scan" in channel.sent[-1]

    @pytest.mark.asyncio
    async def test_someone_else_reacting_does_nothing(self) -> None:
        channel = FakeChannel()
        job = make_job()
        message = await post_status(channel, job)
        cog = make_listener(job)
        cog.bot.get_channel = MagicMock(return_value=channel)

        with patch(
            "bot.app.commands.agent.scan_listener.cancel_channel_scan", new=AsyncMock()
        ) as cancel:
            await cog.on_raw_reaction_add(self._payload(OTHER_USER, message.id))

        cancel.assert_not_awaited()
        assert len(channel.sent) == 1  # just the status message

    @pytest.mark.asyncio
    async def test_other_emoji_and_other_messages_are_ignored(self) -> None:
        channel = FakeChannel()
        job = make_job()
        message = await post_status(channel, job)
        cog = make_listener(job)
        cog.bot.get_channel = MagicMock(return_value=channel)

        with patch(
            "bot.app.commands.agent.scan_listener.cancel_channel_scan", new=AsyncMock()
        ) as cancel:
            await cog.on_raw_reaction_add(self._payload(REQUESTER, message.id, "👍"))
            await cog.on_raw_reaction_add(self._payload(REQUESTER, 123456))

        cancel.assert_not_awaited()


# --- the final report --------------------------------------------------


class TestFinishReport:
    @pytest.mark.asyncio
    async def test_a_short_list_is_posted_in_the_channel(self) -> None:
        channel = FakeChannel()
        job = make_job()
        await post_status(channel, job)
        items = make_items(3)
        on_finish = make_finish_callback(channel, make_store(items))

        with patch("bot.domain.pages.page_service.publish_page", new=AsyncMock()) as publish:
            await on_finish(job, ScanSummary(status="done", scanned=12400, matched=3))

        publish.assert_not_awaited()
        posted = channel.sent[-1]
        assert posted.startswith("<@{}> ✅ Finished scanning #foodchat".format(REQUESTER))
        assert "12,400 messages scanned, 3 items found" in posted
        assert "- **restaurant 0** — someone recommended restaurant 0" in posted
        # The status message stops claiming to be live.
        assert "Finished scanning" in channel.messages[0].content
        assert "stop" not in channel.messages[0].content

    @pytest.mark.asyncio
    async def test_a_long_list_becomes_a_page(self) -> None:
        channel = FakeChannel()
        job = make_job()
        items = make_items(40)
        on_finish = make_finish_callback(channel, make_store(items))

        with patch(
            "bot.domain.pages.page_service.publish_page",
            new=AsyncMock(return_value="https://pages.example/p/scan-collect-every-x"),
        ) as publish:
            await on_finish(job, ScanSummary(status="done", scanned=98000, matched=40))

        kwargs = publish.await_args.kwargs
        assert kwargs["slug"] == report.scan_slug(job["instruction"])
        assert "restaurant 39" in kwargs["markdown"]
        assert "98,000 messages" in kwargs["markdown"]
        posted = channel.sent[-1]
        assert "https://pages.example/p/scan-collect-every-x" in posted
        assert "98,000 messages scanned, 40 items found" in posted

    @pytest.mark.asyncio
    async def test_a_long_list_falls_back_to_the_channel_when_pages_are_off(self) -> None:
        channel = FakeChannel()
        job = make_job()
        on_finish = make_finish_callback(channel, make_store(make_items(40)))

        with patch(
            "bot.domain.pages.page_service.publish_page",
            new=AsyncMock(side_effect=EnvironmentError("not configured")),
        ):
            await on_finish(job, ScanSummary(status="done", scanned=98000, matched=40))

        assert len(channel.sent) > 1  # split across messages rather than lost
        assert "restaurant 39" in "".join(channel.sent)

    @pytest.mark.asyncio
    async def test_a_cancelled_scan_says_it_stopped_early(self) -> None:
        channel = FakeChannel()
        job = make_job()
        on_finish = make_finish_callback(channel, make_store(make_items(2)))

        await on_finish(job, ScanSummary(status="cancelled", scanned=4100, matched=2))

        posted = channel.sent[-1]
        assert "Stopped scanning #foodchat" in posted
        assert "early" in posted
        assert "4,100 messages scanned, 2 items found" in posted
        assert "- **restaurant 0**" in posted

    @pytest.mark.asyncio
    async def test_a_failed_scan_says_so_plainly(self) -> None:
        channel = FakeChannel()
        job = make_job()
        on_finish = make_finish_callback(channel, make_store([]))

        await on_finish(
            job,
            ScanSummary(status="failed", scanned=300, error="5 pages in a row could not be read"),
        )

        posted = channel.sent[-1]
        assert "failed after 300 messages" in posted
        assert "5 pages in a row could not be read" in posted

    @pytest.mark.asyncio
    async def test_nothing_found_is_still_a_report(self) -> None:
        channel = FakeChannel()
        on_finish = make_finish_callback(channel, make_store([]))

        await on_finish(make_job(), ScanSummary(status="done", scanned=500))

        assert "0 items found" in channel.sent[-1]
        assert "Nothing matched." in channel.sent[-1]

    @pytest.mark.asyncio
    async def test_the_report_survives_a_deleted_status_message(self) -> None:
        channel = FakeChannel()
        job = make_job()
        await post_status(channel, job)
        channel.messages[0].edit_error = discord.NotFound(
            SimpleNamespace(status=404, reason="Not Found"), "gone"
        )
        on_finish = make_finish_callback(channel, make_store(make_items(1)))

        await on_finish(job, ScanSummary(status="done", scanned=10, matched=1))

        assert "Finished scanning #foodchat" in channel.sent[-1]


class TestReportWording:
    def test_the_slug_is_stable_for_the_same_instruction(self) -> None:
        assert report.scan_slug("Collect every restaurant!") == report.scan_slug(
            "collect every restaurant"
        )
        assert report.scan_slug("").startswith("scan")

    def test_long_lists_do_not_fit_inline(self) -> None:
        assert report.fits_inline(make_items(3)) is True
        assert report.fits_inline(make_items(40)) is False
