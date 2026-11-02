"""Tests for the scheduling tools, the plain-words read-back, and /schedule."""

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.app.commands.schedule.schedule import NOT_ALLOWED, NOT_FOUND, ScheduleCog
from bot.app.redis.schedule_store import STATUS_ACTIVE, STATUS_PAUSED, ScheduleRedisStore
from bot.app.schedule_jobs import create_scheduled_prompt
from bot.domain.agent.suggestions import collect_suggestions
from bot.domain.agent.tools.cancel_scheduled_prompt import execute_cancel_scheduled_prompt
from bot.domain.agent.tools.list_scheduled_prompts import execute_list_scheduled_prompts
from bot.domain.agent.tools.registry import SCHEDULED_OK_TOOLS, TOOLS
from bot.domain.agent.tools.schedule_prompt import CONFIRM_OPTIONS, execute_schedule_prompt
from bot.domain.schedule.describe import (
    describe_cron,
    describe_schedule,
    normalize_zone,
    read_back,
)
from bot.domain.schedule.policy import DEFAULT_SCHEDULE_TZ, MAX_JOBS_PER_USER
from tests.test_schedule_engine import FakeRedis

LA = "America/Los_Angeles"
GUILD = 111
CHANNEL = 222
NICK = 333
SAM = 444
NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)  # Monday, 05:00 in LA


@pytest.fixture
def redis() -> Any:
    fake = FakeRedis()
    client = SimpleNamespace(redis=fake)
    with patch("bot.app.redis.schedule_store.get_redis_client", return_value=client), \
            patch("bot.app.schedule_jobs.get_redis_client", return_value=client):
        yield fake


def user(uid: int, name: str = "Nick C") -> Any:
    return SimpleNamespace(id=uid, display_name=name, mention=f"<@{uid}>")


def make_channel(moderators: Optional[set] = None) -> Any:
    moderators = moderators or set()
    members = {NICK: user(NICK, "Nick C"), SAM: user(SAM, "Sam")}
    guild = SimpleNamespace(
        id=GUILD,
        get_channel=lambda cid: SimpleNamespace(name="general") if cid == CHANNEL else None,
        get_member=lambda uid: members.get(uid),
    )
    return SimpleNamespace(
        id=CHANNEL,
        guild=guild,
        permissions_for=lambda u: SimpleNamespace(manage_messages=u.id in moderators),
    )


async def draft(channel: Any, who: Any, cron: str = "0 9 * * 1-5", tz: Optional[str] = None) -> str:
    args = {"step": "draft", "prompt": "Summarize the last 24 hours here", "cron": cron}
    if tz:
        args["timezone"] = tz
    return await execute_schedule_prompt(args, channel, who)


# --- plain words -----------------------------------------------------------


class TestDescribe:
    @pytest.mark.parametrize("cron,words", [
        ("0 9 * * *", "daily at 9:00 AM"),
        ("30 8 * * 1-5", "weekdays at 8:30 AM"),
        ("0 10 * * 0,6", "weekends at 10:00 AM"),
        ("0 17 * * 1,3,5", "every Monday, Wednesday and Friday at 5:00 PM"),
        ("0 12 * * 7", "every Sunday at 12:00 PM"),
        ("0 9,17 * * *", "daily at 9:00 AM and 5:00 PM"),
        ("0 0 1 * *", "on the 1st of every month at 12:00 AM"),
        ("0 9 22 * *", "on the 22nd of every month at 9:00 AM"),
        ("0 * * * *", "every hour, on the hour"),
        ("15 * * * *", "every hour at 15 past"),
    ])
    def test_common_shapes(self, cron: str, words: str) -> None:
        assert describe_cron(cron) == words

    @pytest.mark.parametrize("cron", ["0 9 * 1 *", "0 */2 * * *", "0 9 1 * 1"])
    def test_anything_else_shows_the_expression(self, cron: str) -> None:
        assert describe_cron(cron) == f"on the cron schedule `{cron}`"

    def test_names_the_zone(self) -> None:
        assert describe_schedule("0 9 * * *", LA) == "daily at 9:00 AM (Pacific time)"
        assert describe_schedule("0 9 * * *", "Europe/London") == "daily at 9:00 AM (Europe/London time)"

    def test_read_back_lists_the_next_three_runs_in_local_time(self) -> None:
        assert read_back("0 9 * * 1-5", LA, NOW) == (
            "weekdays at 9:00 AM (Pacific time) "
            "(next: Mon Sep 21, 9:00 AM; Tue Sep 22, 9:00 AM; Wed Sep 23, 9:00 AM)"
        )

    def test_zone_defaults_to_pacific_and_takes_common_names(self) -> None:
        assert DEFAULT_SCHEDULE_TZ == LA
        assert normalize_zone(None, DEFAULT_SCHEDULE_TZ) == LA
        assert normalize_zone("  ", DEFAULT_SCHEDULE_TZ) == LA
        assert normalize_zone("ET", DEFAULT_SCHEDULE_TZ) == "America/New_York"
        assert normalize_zone("Europe/Paris", DEFAULT_SCHEDULE_TZ) == "Europe/Paris"


# --- schedule_prompt ---------------------------------------------------------


class TestSchedulePrompt:
    @pytest.mark.asyncio
    async def test_draft_reads_back_and_offers_confirm_buttons(self, redis: FakeRedis) -> None:
        with collect_suggestions() as box:
            result = await draft(make_channel(), user(NICK))

        assert "NOT scheduled yet" in result
        assert "weekdays at 9:00 AM (Pacific time)" in result
        assert box == CONFIRM_OPTIONS
        saved = await ScheduleRedisStore().get_draft(GUILD, CHANNEL)
        assert saved["cron"] == "0 9 * * 1-5" and saved["tz"] == LA
        assert await ScheduleRedisStore().list_jobs(GUILD) == []

    @pytest.mark.asyncio
    async def test_draft_without_buttons_asks_for_a_typed_confirm(self, redis: FakeRedis) -> None:
        result = await draft(make_channel(), user(NICK))
        assert "reply 'confirm'" in result

    @pytest.mark.asyncio
    async def test_draft_takes_a_zone_alias(self, redis: FakeRedis) -> None:
        result = await draft(make_channel(), user(NICK), tz="Eastern")
        assert "(Eastern time)" in result
        assert (await ScheduleRedisStore().get_draft(GUILD, CHANNEL))["tz"] == "America/New_York"

    @pytest.mark.asyncio
    async def test_bad_schedule_is_refused_without_a_draft(self, redis: FakeRedis) -> None:
        with collect_suggestions() as box:
            result = await draft(make_channel(), user(NICK), cron="*/15 * * * *")
        assert result.startswith("Can't schedule that") and "once an hour" in result
        assert box == []
        assert await ScheduleRedisStore().get_draft(GUILD, CHANNEL) is None

    @pytest.mark.asyncio
    async def test_draft_checks_the_callers_cap(self, redis: FakeRedis) -> None:
        for _ in range(MAX_JOBS_PER_USER):
            await create_scheduled_prompt(GUILD, CHANNEL, NICK, "x", "0 9 * * *", LA)
        result = await draft(make_channel(), user(NICK))
        assert "You already have" in result

    @pytest.mark.asyncio
    async def test_confirm_creates_the_job_as_whoever_confirms(self, redis: FakeRedis) -> None:
        channel = make_channel()
        await draft(channel, user(NICK))

        result = await execute_schedule_prompt({"step": "confirm"}, channel, user(SAM, "Sam"))

        assert result.startswith("Scheduled (id ")
        (job,) = await ScheduleRedisStore().list_jobs(GUILD)
        assert job["creator_id"] == str(SAM)
        assert job["cron"] == "0 9 * * 1-5" and job["tz"] == LA
        assert await ScheduleRedisStore().get_draft(GUILD, CHANNEL) is None

    @pytest.mark.asyncio
    async def test_confirm_with_no_draft(self, redis: FakeRedis) -> None:
        result = await execute_schedule_prompt({"step": "confirm"}, make_channel(), user(NICK))
        assert "no schedule waiting" in result
        assert await ScheduleRedisStore().list_jobs(GUILD) == []

    @pytest.mark.asyncio
    async def test_discard_drops_the_draft(self, redis: FakeRedis) -> None:
        channel = make_channel()
        await draft(channel, user(NICK))
        await execute_schedule_prompt({"step": "discard"}, channel, user(NICK))
        assert "no schedule waiting" in await execute_schedule_prompt(
            {"step": "confirm"}, channel, user(NICK)
        )

    @pytest.mark.asyncio
    async def test_needs_a_user(self, redis: FakeRedis) -> None:
        result = await execute_schedule_prompt({"step": "draft"}, make_channel(), None)
        assert "someone in a server channel" in result


# --- list and cancel -------------------------------------------------------


class TestListAndCancel:
    @pytest.mark.asyncio
    async def test_list(self, redis: FakeRedis) -> None:
        channel = make_channel()
        assert await execute_list_scheduled_prompts({}, channel) == "This server has no scheduled prompts."
        job = await create_scheduled_prompt(GUILD, CHANNEL, NICK, "Post the weekly recap", "0 17 * * 5", LA, now=NOW)

        result = await execute_list_scheduled_prompts({}, channel)

        assert job["job_id"] in result
        assert "every Friday at 5:00 PM (Pacific time) in #general, set by Nick C" in result
        assert "next Fri Sep 25, 5:00 PM" in result
        assert "Post the weekly recap" in result

    @pytest.mark.asyncio
    async def test_creator_can_cancel(self, redis: FakeRedis) -> None:
        job = await create_scheduled_prompt(GUILD, CHANNEL, NICK, "x", "0 9 * * *", LA)
        result = await execute_cancel_scheduled_prompt({"job_id": job["job_id"]}, make_channel(), user(NICK))
        assert result.startswith("Cancelled")
        assert await ScheduleRedisStore().list_jobs(GUILD) == []

    @pytest.mark.asyncio
    async def test_someone_else_cannot_unless_they_moderate(self, redis: FakeRedis) -> None:
        job = await create_scheduled_prompt(GUILD, CHANNEL, NICK, "x", "0 9 * * *", LA)

        refused = await execute_cancel_scheduled_prompt({"job_id": job["job_id"]}, make_channel(), user(SAM))
        assert "Nothing was cancelled" in refused
        assert len(await ScheduleRedisStore().list_jobs(GUILD)) == 1

        allowed = await execute_cancel_scheduled_prompt(
            {"job_id": f"`{job['job_id']}`"}, make_channel(moderators={SAM}), user(SAM)
        )
        assert allowed.startswith("Cancelled")

    @pytest.mark.asyncio
    async def test_unknown_id(self, redis: FakeRedis) -> None:
        result = await execute_cancel_scheduled_prompt({"job_id": "nope"}, make_channel(), user(NICK))
        assert "No scheduled prompt" in result


def test_scheduling_tools_are_default_on_but_not_in_scheduled_runs() -> None:
    by_key = {t.config_key: t for t in TOOLS}
    for key in ("schedule_prompt", "list_scheduled_prompts", "cancel_scheduled_prompt"):
        assert by_key[key].default_enabled
        assert key not in SCHEDULED_OK_TOOLS


# --- /schedule ---------------------------------------------------------------


def interaction(uid: int, moderator: bool = False) -> Any:
    it = MagicMock()
    it.guild = make_channel().guild
    it.user = user(uid, "Sam" if uid == SAM else "Nick C")
    it.permissions = SimpleNamespace(manage_messages=moderator)
    it.response.send_message = AsyncMock()
    return it


class TestScheduleCommand:
    @pytest.mark.asyncio
    async def test_list_is_private(self, redis: FakeRedis) -> None:
        await create_scheduled_prompt(GUILD, CHANNEL, NICK, "Daily recap", "0 9 * * *", LA)
        it = interaction(SAM)
        await ScheduleCog.list_jobs.callback(ScheduleCog(MagicMock()), it)
        kwargs = it.response.send_message.call_args.kwargs
        assert kwargs["ephemeral"] is True
        assert "Daily recap" in kwargs["embed"].description

    @pytest.mark.asyncio
    async def test_cancel_by_creator_is_announced(self, redis: FakeRedis) -> None:
        job = await create_scheduled_prompt(GUILD, CHANNEL, NICK, "Daily recap", "0 9 * * *", LA)
        it = interaction(NICK)
        await ScheduleCog.cancel.callback(ScheduleCog(MagicMock()), it, job=job["job_id"])
        text = it.response.send_message.call_args.args[0]
        assert "cancelled the scheduled prompt *Daily recap*" in text
        assert await ScheduleRedisStore().list_jobs(GUILD) == []

    @pytest.mark.asyncio
    async def test_others_are_refused(self, redis: FakeRedis) -> None:
        job = await create_scheduled_prompt(GUILD, CHANNEL, NICK, "Daily recap", "0 9 * * *", LA)
        it = interaction(SAM)
        await ScheduleCog.cancel.callback(ScheduleCog(MagicMock()), it, job=job["job_id"])
        it.response.send_message.assert_awaited_once_with(NOT_ALLOWED, ephemeral=True)
        assert len(await ScheduleRedisStore().list_jobs(GUILD)) == 1

    @pytest.mark.asyncio
    async def test_unknown_job(self, redis: FakeRedis) -> None:
        it = interaction(NICK)
        await ScheduleCog.pause.callback(ScheduleCog(MagicMock()), it, job="nope")
        it.response.send_message.assert_awaited_once_with(NOT_FOUND, ephemeral=True)

    @pytest.mark.asyncio
    async def test_moderator_can_pause_and_resume(self, redis: FakeRedis) -> None:
        job = await create_scheduled_prompt(GUILD, CHANNEL, NICK, "Daily recap", "0 9 * * *", LA)
        cog = ScheduleCog(MagicMock())

        await cog.pause.callback(cog, interaction(SAM, moderator=True), job=job["job_id"])
        paused = await ScheduleRedisStore().get_job(GUILD, job["job_id"])
        assert paused["status"] == STATUS_PAUSED and paused["paused_reason"] == "paused by Sam"

        await cog.resume.callback(cog, interaction(SAM, moderator=True), job=job["job_id"])
        assert (await ScheduleRedisStore().get_job(GUILD, job["job_id"]))["status"] == STATUS_ACTIVE

    @pytest.mark.asyncio
    async def test_autocomplete_filters_by_text(self, redis: FakeRedis) -> None:
        await create_scheduled_prompt(GUILD, CHANNEL, NICK, "Daily recap", "0 9 * * *", LA)
        await create_scheduled_prompt(GUILD, CHANNEL, SAM, "Friday trivia reminder", "0 17 * * 5", LA)
        cog = ScheduleCog(MagicMock())

        choices = await cog.job_autocomplete(interaction(NICK), "friday")

        assert [c.name for c in choices] == [
            "Friday trivia reminder — every Friday at 5:00 PM (Pacific time)"
        ]
