"""Tests for scheduled prompts: the cron rules, the store, creating, and the runner.

No Discord and no network: the store runs against a fake in-memory Redis, the
clock is passed in, and a run is either a stub or `run_scheduled_job` against
fake guild/channel objects with `run_agent` patched.
"""

import asyncio
import fnmatch
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from bot.app import schedule_jobs, schedule_runtime
from bot.app.agent_runtime import AGENT_CHANNEL_LOCKS, get_agent_channel_lock
from bot.app.redis.schedule_store import (
    DUE_KEY,
    RESULT_OK,
    RESULT_SKIPPED,
    STATUS_ACTIVE,
    STATUS_PAUSED,
    ScheduleRedisStore,
    due_member,
)
from bot.app.schedule_jobs import create_scheduled_prompt, resume_scheduled_prompt
from bot.app.schedule_runtime import (
    SCHEDULE_TASKS,
    SkipRun,
    run_scheduled_job,
    scheduled_config,
    tick,
)
from bot.domain.agent.tools.registry import SCHEDULED_OK_TOOLS
from bot.domain.schedule.cron import (
    ScheduleError,
    next_run_after,
    should_run_late,
    validate_schedule,
)
from bot.domain.schedule.policy import (
    MAX_CONSECUTIVE_FAILURES,
    MAX_JOBS_PER_GUILD,
    MAX_JOBS_PER_USER,
    MAX_PROMPT_CHARS,
)

LA = "America/Los_Angeles"
GUILD = 111
CHANNEL = 222
CREATOR = 333
NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)  # 05:00 in Los Angeles


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


# --- fakes -------------------------------------------------------------


class FakeRedis:
    """Just enough of redis.asyncio for the schedule store and redis_lock."""

    def __init__(self) -> None:
        self.strings: Dict[str, str] = {}
        self.sets: Dict[str, set] = {}
        self.zsets: Dict[str, Dict[str, float]] = {}

    async def get(self, key: str) -> Optional[str]:
        return self.strings.get(key)

    async def set(self, key: str, value: Any, nx: bool = False, ex: Optional[int] = None) -> Any:
        if nx and key in self.strings:
            return None
        self.strings[key] = str(value)
        return True

    async def delete(self, *keys: str) -> int:
        return sum(1 for k in keys if self.strings.pop(k, None) is not None)

    async def eval(self, script: str, numkeys: int, key: str, value: str) -> int:
        # redis_lock's release: delete the key if it still holds our value.
        if self.strings.get(key) == value:
            del self.strings[key]
            return 1
        return 0

    async def sadd(self, key: str, *members: str) -> int:
        bucket = self.sets.setdefault(key, set())
        added = len(set(members) - bucket)
        bucket.update(members)
        return added

    async def srem(self, key: str, *members: str) -> int:
        bucket = self.sets.get(key, set())
        removed = len(set(members) & bucket)
        bucket.difference_update(members)
        return removed

    async def smembers(self, key: str) -> set:
        return set(self.sets.get(key, set()))

    async def zadd(self, key: str, mapping: Dict[str, float]) -> int:
        bucket = self.zsets.setdefault(key, {})
        added = len(set(mapping) - set(bucket))
        bucket.update(mapping)
        return added

    async def zrem(self, key: str, *members: str) -> int:
        bucket = self.zsets.get(key, {})
        return sum(1 for m in members if bucket.pop(m, None) is not None)

    async def zrangebyscore(self, key: str, low: Any, high: float) -> List[str]:
        bucket = self.zsets.get(key, {})
        return [m for m, s in sorted(bucket.items(), key=lambda kv: kv[1]) if s <= high]

    async def scan(self, cursor: int, match: Optional[str] = None, count: Optional[int] = None) -> Any:
        keys = list(self.strings) + list(self.sets) + list(self.zsets)
        return 0, [k for k in keys if match is None or fnmatch.fnmatchcase(k, match)]


@pytest.fixture
def redis() -> Any:
    fake = FakeRedis()
    client = SimpleNamespace(redis=fake)
    with patch("bot.app.redis.schedule_store.get_redis_client", return_value=client), \
            patch("bot.app.schedule_jobs.get_redis_client", return_value=client):
        yield fake


@pytest.fixture(autouse=True)
def clear_state() -> Any:
    AGENT_CHANNEL_LOCKS.clear()
    SCHEDULE_TASKS.clear()
    yield
    AGENT_CHANNEL_LOCKS.clear()
    SCHEDULE_TASKS.clear()


async def make_job(cron: str = "0 9 * * *", creator: int = CREATOR, now: datetime = NOW) -> Dict[str, Any]:
    return await create_scheduled_prompt(GUILD, CHANNEL, creator, "Summarize today", cron, LA, now=now)


async def settle() -> None:
    """Let the runs `tick` started finish."""
    await asyncio.gather(*list(SCHEDULE_TASKS.values()))


# --- cron rules ----------------------------------------------------------


class TestNextRun:
    def test_daily_stays_at_local_time_across_fall_back(self) -> None:
        # DST ends 2026-11-01: 9am PDT is 16:00Z, 9am PST is 17:00Z.
        assert next_run_after("0 9 * * *", LA, utc(2026, 10, 31, 12)) == utc(2026, 10, 31, 16)
        assert next_run_after("0 9 * * *", LA, utc(2026, 10, 31, 16)) == utc(2026, 11, 1, 17)

    def test_daily_stays_at_local_time_across_spring_forward(self) -> None:
        # DST starts 2027-03-14: 9am PST is 17:00Z, 9am PDT is 16:00Z.
        assert next_run_after("0 9 * * *", LA, utc(2027, 3, 13, 12)) == utc(2027, 3, 13, 17)
        assert next_run_after("0 9 * * *", LA, utc(2027, 3, 13, 17)) == utc(2027, 3, 14, 16)

    def test_time_in_the_spring_forward_gap_runs_just_after_it(self) -> None:
        # 2:30am doesn't exist on 2027-03-14; it runs at 3:30 PDT (10:30Z).
        assert next_run_after("30 2 * * *", LA, utc(2027, 3, 14, 0)) == utc(2027, 3, 14, 10, 30)

    def test_time_in_the_repeated_hour_runs_once(self) -> None:
        # 1:30am happens twice on 2026-11-01 (08:30Z PDT, 09:30Z PST): run the first.
        first = next_run_after("30 1 * * *", LA, utc(2026, 11, 1, 0))
        assert first == utc(2026, 11, 1, 8, 30)
        assert next_run_after("30 1 * * *", LA, first) == utc(2026, 11, 2, 9, 30)


class TestValidate:
    @pytest.mark.parametrize("cron", ["0 * * * *", "0 9 * * *", "0 9 * * 1-5", "15 8 1 * *"])
    def test_accepts_hourly_or_slower(self, cron: str) -> None:
        validate_schedule(cron, LA, NOW)

    def test_hourly_is_fine_across_a_dst_change(self) -> None:
        validate_schedule("0 * * * *", LA, utc(2027, 3, 13, 0))
        validate_schedule("0 * * * *", LA, utc(2026, 10, 31, 0))

    @pytest.mark.parametrize("cron", ["*/30 * * * *", "0,30 9 * * *", "* * * * *"])
    def test_rejects_anything_more_often_than_hourly(self, cron: str) -> None:
        with pytest.raises(ScheduleError, match="once an hour"):
            validate_schedule(cron, LA, NOW)

    @pytest.mark.parametrize("cron", ["0 9 * * * *", "@daily", "0 9 * *"])
    def test_needs_five_fields(self, cron: str) -> None:
        with pytest.raises(ScheduleError, match="five cron fields"):
            validate_schedule(cron, LA, NOW)

    def test_rejects_nonsense(self) -> None:
        with pytest.raises(ScheduleError, match="Not a valid"):
            validate_schedule("99 9 * * *", LA, NOW)

    def test_rejects_unknown_zone(self) -> None:
        with pytest.raises(ScheduleError, match="time zone"):
            validate_schedule("0 9 * * *", "Mars/Olympus", NOW)


class TestRunLate:
    def test_hourly_has_half_an_hour_of_grace(self) -> None:
        due = utc(2026, 9, 21, 12)
        assert should_run_late("0 * * * *", LA, due, due + timedelta(minutes=29))
        assert not should_run_late("0 * * * *", LA, due, due + timedelta(minutes=31))

    def test_daily_has_twelve_hours_of_grace(self) -> None:
        due = utc(2026, 9, 21, 16)
        assert should_run_late("0 9 * * *", LA, due, due + timedelta(hours=11))
        assert not should_run_late("0 9 * * *", LA, due, due + timedelta(hours=13))

    def test_not_before_it_is_due(self) -> None:
        due = utc(2026, 9, 21, 16)
        assert not should_run_late("0 9 * * *", LA, due, due - timedelta(minutes=1))


# --- creating ------------------------------------------------------------


class TestCreate:
    @pytest.mark.asyncio
    async def test_stores_an_active_job_due_at_its_next_run(self, redis: FakeRedis) -> None:
        job = await make_job()

        assert job["status"] == STATUS_ACTIVE
        assert job["next_run"] == utc(2026, 9, 21, 16).isoformat()  # 9am PDT
        member = due_member(str(GUILD), job["job_id"])
        assert redis.zsets[DUE_KEY][member] == utc(2026, 9, 21, 16).timestamp()
        # The create lock is released.
        assert not [k for k in redis.strings if k.startswith("lock:")]

    @pytest.mark.asyncio
    async def test_per_user_cap(self, redis: FakeRedis) -> None:
        for _ in range(MAX_JOBS_PER_USER):
            await make_job()
        with pytest.raises(ScheduleError, match="You already have"):
            await make_job()
        await make_job(creator=CREATOR + 1)  # someone else still can

    @pytest.mark.asyncio
    async def test_per_server_cap(self, redis: FakeRedis) -> None:
        for i in range(MAX_JOBS_PER_GUILD):
            await make_job(creator=1000 + i)
        with pytest.raises(ScheduleError, match="This server already has"):
            await make_job(creator=5000)

    @pytest.mark.asyncio
    async def test_paused_jobs_count_and_cancelled_ones_dont(self, redis: FakeRedis) -> None:
        jobs = [await make_job() for _ in range(MAX_JOBS_PER_USER)]
        await schedule_jobs.pause_scheduled_prompt(GUILD, jobs[0]["job_id"])
        with pytest.raises(ScheduleError):
            await make_job()
        assert await schedule_jobs.cancel_scheduled_prompt(GUILD, jobs[0]["job_id"])
        await make_job()

    @pytest.mark.asyncio
    async def test_rejects_bad_prompts_and_schedules(self, redis: FakeRedis) -> None:
        with pytest.raises(ScheduleError, match="needs something"):
            await create_scheduled_prompt(GUILD, CHANNEL, CREATOR, "  ", "0 9 * * *", LA, now=NOW)
        with pytest.raises(ScheduleError, match="too long"):
            await create_scheduled_prompt(
                GUILD, CHANNEL, CREATOR, "x" * (MAX_PROMPT_CHARS + 1), "0 9 * * *", LA, now=NOW
            )
        with pytest.raises(ScheduleError, match="once an hour"):
            await create_scheduled_prompt(GUILD, CHANNEL, CREATOR, "hi", "*/5 * * * *", LA, now=NOW)
        assert DUE_KEY not in redis.zsets

    @pytest.mark.asyncio
    async def test_pause_takes_it_off_the_queue_and_resume_puts_it_back(self, redis: FakeRedis) -> None:
        job = await make_job()
        member = due_member(str(GUILD), job["job_id"])

        await schedule_jobs.pause_scheduled_prompt(GUILD, job["job_id"])
        assert member not in redis.zsets[DUE_KEY]

        later = utc(2026, 9, 25, 20)
        resumed = await resume_scheduled_prompt(GUILD, job["job_id"], now=later)
        assert resumed["status"] == STATUS_ACTIVE
        assert redis.zsets[DUE_KEY][member] == utc(2026, 9, 26, 16).timestamp()


# --- ticking -------------------------------------------------------------


class TestTick:
    @pytest.mark.asyncio
    async def test_nothing_runs_before_it_is_due(self, redis: FakeRedis) -> None:
        await make_job()
        run = AsyncMock()
        assert await tick(None, now=utc(2026, 9, 21, 15, 59), run=run) == 0
        run.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_due_job_runs_and_moves_to_its_next_run(self, redis: FakeRedis) -> None:
        job = await make_job()
        run = AsyncMock()

        assert await tick(None, now=utc(2026, 9, 21, 16, 0, 20), run=run) == 1
        await settle()

        run.assert_awaited_once()
        member = due_member(str(GUILD), job["job_id"])
        assert redis.zsets[DUE_KEY][member] == utc(2026, 9, 22, 16).timestamp()
        stored = await ScheduleRedisStore().get_job(GUILD, job["job_id"])
        assert stored["last_result"] == RESULT_OK and stored["run_count"] == 1

    @pytest.mark.asyncio
    async def test_missed_run_within_grace_runs_once_late(self, redis: FakeRedis) -> None:
        await make_job()
        run = AsyncMock()
        # Down from 8:00 to 19:00 PDT: 10 hours late, inside the 12-hour grace.
        assert await tick(None, now=utc(2026, 9, 22, 2), run=run) == 1
        await settle()
        run.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_missed_run_past_grace_is_skipped_not_replayed(self, redis: FakeRedis) -> None:
        job = await make_job()
        run = AsyncMock()
        # Three days late: one skip, and the job moves to the next future 9am.
        now = utc(2026, 9, 24, 12)
        assert await tick(None, now=now, run=run) == 0
        run.assert_not_awaited()
        stored = await ScheduleRedisStore().get_job(GUILD, job["job_id"])
        assert stored["last_result"] == RESULT_SKIPPED
        assert stored["next_run"] == utc(2026, 9, 24, 16).isoformat()
        assert await tick(None, now=now + timedelta(minutes=1), run=run) == 0

    @pytest.mark.asyncio
    async def test_a_job_claimed_elsewhere_is_left_alone(self, redis: FakeRedis) -> None:
        job = await make_job()
        run = AsyncMock()
        store = ScheduleRedisStore()
        with patch.object(ScheduleRedisStore, "claim_due", AsyncMock(return_value=False)):
            assert await tick(None, now=utc(2026, 9, 21, 16, 1), run=run) == 0
        run.assert_not_awaited()
        assert (await store.get_job(GUILD, job["job_id"]))["next_run"] == utc(2026, 9, 21, 16).isoformat()

    @pytest.mark.asyncio
    async def test_a_still_running_job_isnt_started_twice(self, redis: FakeRedis) -> None:
        job = await make_job("0 * * * *")
        release = asyncio.Event()

        async def slow(client: Any, job: Dict[str, Any]) -> None:
            await release.wait()

        # Created at 12:00, so the first hourly run is 13:00.
        assert await tick(None, now=utc(2026, 9, 21, 13, 0, 5), run=slow) == 1
        assert await tick(None, now=utc(2026, 9, 21, 14, 0, 5), run=slow) == 0
        stored = await ScheduleRedisStore().get_job(GUILD, job["job_id"])
        assert stored["last_error"] == "The previous run was still going."
        release.set()
        await settle()

    @pytest.mark.asyncio
    async def test_failures_pause_the_job_and_tell_the_creator(self, redis: FakeRedis) -> None:
        job = await make_job("0 * * * *")

        async def broken(client: Any, job: Dict[str, Any]) -> None:
            raise RuntimeError("Missing Access")

        notify = AsyncMock()
        with patch.object(schedule_runtime, "notify_paused", notify):
            for hour in range(13, 13 + MAX_CONSECUTIVE_FAILURES):
                await tick(None, now=utc(2026, 9, 21, hour, 0, 5), run=broken)
                await settle()

        stored = await ScheduleRedisStore().get_job(GUILD, job["job_id"])
        assert stored["status"] == STATUS_PAUSED
        assert stored["consecutive_failures"] == MAX_CONSECUTIVE_FAILURES
        assert "Missing Access" in stored["paused_reason"]
        assert due_member(str(GUILD), job["job_id"]) not in redis.zsets[DUE_KEY]
        notify.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_success_resets_the_failure_count(self, redis: FakeRedis) -> None:
        job = await make_job("0 * * * *")
        outcomes = iter([RuntimeError("boom"), RuntimeError("boom"), None, RuntimeError("boom")])

        async def flaky(client: Any, job: Dict[str, Any]) -> None:
            error = next(outcomes)
            if error:
                raise error

        for hour in range(13, 17):
            await tick(None, now=utc(2026, 9, 21, hour, 0, 5), run=flaky)
            await settle()

        stored = await ScheduleRedisStore().get_job(GUILD, job["job_id"])
        assert stored["status"] == STATUS_ACTIVE
        assert stored["consecutive_failures"] == 1

    @pytest.mark.asyncio
    async def test_a_skipped_run_is_not_a_failure(self, redis: FakeRedis) -> None:
        job = await make_job("0 * * * *")

        async def paused_channel(client: Any, job: Dict[str, Any]) -> None:
            raise SkipRun("The bot is paused in that channel.")

        for hour in range(13, 13 + MAX_CONSECUTIVE_FAILURES + 1):
            await tick(None, now=utc(2026, 9, 21, hour, 0, 5), run=paused_channel)
            await settle()

        stored = await ScheduleRedisStore().get_job(GUILD, job["job_id"])
        assert stored["status"] == STATUS_ACTIVE
        assert stored["last_result"] == RESULT_SKIPPED
        assert stored["consecutive_failures"] == 0


# --- one run -------------------------------------------------------------


class Typing:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: Any) -> bool:
        return False


def make_client(config: Optional[dict], member_missing: bool = False) -> Any:
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = CHANNEL
    channel.typing = MagicMock(return_value=Typing())
    creator = SimpleNamespace(id=CREATOR, display_name="Nick C")
    guild = MagicMock()
    guild.id = GUILD
    guild.get_member = MagicMock(return_value=None if member_missing else creator)
    guild.fetch_member = AsyncMock(
        side_effect=discord.NotFound(MagicMock(status=404), "gone") if member_missing else None,
        return_value=creator,
    )
    client = MagicMock()
    client.get_guild = MagicMock(return_value=guild)
    client.get_channel = MagicMock(return_value=channel)

    store = MagicMock()
    store.get_agent_config = AsyncMock(return_value=config)
    return SimpleNamespace(client=client, channel=channel, creator=creator, store=store)


JOB = {
    "job_id": "abc123abc123",
    "guild_id": str(GUILD),
    "channel_id": str(CHANNEL),
    "creator_id": str(CREATOR),
    "prompt": "Summarize the last 24 hours of this channel",
    "cron": "0 9 * * *",
    "tz": LA,
}


class TestRunScheduledJob:
    async def _run(self, env: Any, response: str = "Here's the summary.") -> Any:
        run_agent = AsyncMock(return_value=response)
        send = AsyncMock()
        with patch.object(schedule_runtime, "AgentRedisStore", lambda: env.store), \
                patch.object(schedule_runtime, "run_agent", run_agent), \
                patch.object(schedule_runtime, "send_agent_reply", send):
            await run_scheduled_job(env.client, JOB, now=utc(2026, 9, 21, 16))
        return run_agent, send

    @pytest.mark.asyncio
    async def test_runs_as_the_creator_with_scheduled_tools_and_posts(self) -> None:
        tools = ["weather", "read_channel", "create_github_issue", "scan_channel_history", "publish_page"]
        env = make_client({"enabled": True, "tools": tools})

        run_agent, send = await self._run(env)

        kwargs = run_agent.call_args.kwargs
        assert kwargs["user"] is env.creator
        assert kwargs["agent_config"]["tools"] == ["weather", "read_channel", "publish_page"]
        (message,) = kwargs["history"]
        assert message["role"] == "user" and message["name"] == "Nick_C"
        assert "Monday 2026-09-21 09:00 America/Los_Angeles" in message["content"]
        assert message["content"].endswith(JOB["prompt"])
        text = send.call_args.args[2]
        assert text.startswith("> 🗓️ **Scheduled by Nick C:** Summarize the last 24 hours")
        assert text.endswith("Here's the summary.")

    @pytest.mark.asyncio
    async def test_unregistered_channel_runs_with_the_defaults(self) -> None:
        env = make_client(None)
        run_agent, _ = await self._run(env)
        tools = run_agent.call_args.kwargs["agent_config"]["tools"]
        assert tools and set(tools) <= SCHEDULED_OK_TOOLS

    @pytest.mark.asyncio
    async def test_paused_channel_skips(self) -> None:
        env = make_client({"enabled": False, "tools": []})
        with pytest.raises(SkipRun):
            await self._run(env)

    @pytest.mark.asyncio
    async def test_creator_who_left_fails_the_run(self) -> None:
        env = make_client({"enabled": True, "tools": []}, member_missing=True)
        with pytest.raises(RuntimeError, match="no longer in the server"):
            await self._run(env)

    @pytest.mark.asyncio
    async def test_empty_reply_posts_nothing(self) -> None:
        env = make_client({"enabled": True, "tools": []})
        _, send = await self._run(env, response="  ")
        send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_waits_for_a_run_already_going_in_the_channel(self) -> None:
        env = make_client({"enabled": True, "tools": []})
        lock = get_agent_channel_lock(CHANNEL)
        await lock.acquire()
        run_agent = AsyncMock(return_value="done")
        with patch.object(schedule_runtime, "AgentRedisStore", lambda: env.store), \
                patch.object(schedule_runtime, "run_agent", run_agent), \
                patch.object(schedule_runtime, "send_agent_reply", AsyncMock()):
            task = asyncio.ensure_future(run_scheduled_job(env.client, JOB, now=NOW))
            await asyncio.sleep(0.01)
            assert not run_agent.await_count
            lock.release()
            await task
        run_agent.assert_awaited_once()


def test_outward_acting_tools_are_off_in_scheduled_runs() -> None:
    assert "create_github_issue" not in SCHEDULED_OK_TOOLS
    assert "scan_channel_history" not in SCHEDULED_OK_TOOLS
    assert {"read_channel", "publish_page", "suggest_replies"} <= SCHEDULED_OK_TOOLS


def test_scheduled_config_leaves_the_channel_config_alone() -> None:
    config = {"tools": ["weather", "create_github_issue"], "model": "m"}
    filtered = scheduled_config(config)
    assert filtered == {"tools": ["weather"], "model": "m"}
    assert config["tools"] == ["weather", "create_github_issue"]
