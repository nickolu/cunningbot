"""Tests for the channel-history scan engine: store, loop, and runner.

No network and no Discord: the store runs against a fake in-memory Redis, the
channel is a list of fake messages, and the "model" is a regex that reads the
prompt the extractor built. Everything a scan does — paging, dedup, the cursor,
cancelling, resuming after a restart — is exercised through the real code path
(`start_scan` -> `run_scan` -> `ScanRedisStore`).
"""

import asyncio
import fnmatch
import json
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest

from bot.app import scan_runtime
from bot.app.redis.scan_store import FINISHED_JOB_TTL_SECONDS, ScanRedisStore
from bot.app.scan_runtime import ScanAlreadyRunning, start_scan, task_key
from bot.domain.scan.extractor import (
    ExtractError, build_prompt, normalize_key, parse_reply,
)
from bot.domain.scan.models import ScanMessage, ScanPage
from bot.domain.scan.scan_service import MAX_CONSECUTIVE_FAILURES, run_scan

GUILD = 111111111111111111
CHANNEL = 222222222222222222
REQUESTER = 333333333333333333


# --- fakes -------------------------------------------------------------


class FakePipeline:
    """Queues commands and replays them against the fake on execute()."""

    def __init__(self, redis: "FakeRedis") -> None:
        self.redis = redis
        self.queued: List[Any] = []

    def hsetnx(self, key, field, value):
        self.queued.append(("hsetnx", (key, field, value)))
        return self

    async def execute(self):
        results = []
        for name, args in self.queued:
            results.append(await getattr(self.redis, name)(*args))
        self.queued = []
        return results

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeRedis:
    """Just enough of redis.asyncio for the scan store."""

    def __init__(self) -> None:
        self.strings: Dict[str, str] = {}
        self.hashes: Dict[str, Dict[str, str]] = {}
        self.sets: Dict[str, set] = {}
        self.expiries: Dict[str, int] = {}

    async def get(self, key):
        return self.strings.get(key)

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.strings:
            return None
        self.strings[key] = str(value)
        if ex:
            self.expiries[key] = ex
        return True

    async def delete(self, *keys):
        removed = 0
        for key in keys:
            for store in (self.strings, self.hashes, self.sets):
                if key in store:
                    del store[key]
                    removed += 1
        return removed

    async def expire(self, key, seconds):
        exists = key in self.strings or key in self.hashes or key in self.sets
        if exists:
            self.expiries[key] = seconds
        return exists

    async def hsetnx(self, key, field, value):
        bucket = self.hashes.setdefault(key, {})
        if field in bucket:
            return 0
        bucket[field] = value
        return 1

    async def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    async def hlen(self, key):
        return len(self.hashes.get(key, {}))

    async def sadd(self, key, *members):
        bucket = self.sets.setdefault(key, set())
        added = sum(1 for m in members if m not in bucket)
        bucket.update(members)
        return added

    async def srem(self, key, *members):
        bucket = self.sets.get(key, set())
        removed = sum(1 for m in members if m in bucket)
        bucket.difference_update(members)
        return removed

    async def smembers(self, key):
        return set(self.sets.get(key, set()))

    async def scan(self, cursor, match=None, count=None):
        keys = list(self.strings) + list(self.hashes) + list(self.sets)
        return 0, [k for k in keys if match is None or fnmatch.fnmatchcase(k, match)]

    def pipeline(self, transaction=False):
        return FakePipeline(self)


def make_store(redis: FakeRedis) -> ScanRedisStore:
    with patch(
        "bot.app.redis.scan_store.get_redis_client",
        return_value=SimpleNamespace(redis=redis),
    ):
        return ScanRedisStore()


class FakeMessage:
    def __init__(self, msg_id, content, author="alice", bot=False, attachments=(), embeds=()):
        self.id = msg_id
        self.content = content
        self.author = SimpleNamespace(display_name=author, id=999, bot=bot)
        self.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=msg_id)
        self.attachments = [SimpleNamespace(url=u, filename=u.rsplit("/", 1)[-1]) for u in attachments]
        self.embeds = list(embeds)
        self.jump_url = f"https://discord.com/channels/{GUILD}/{CHANNEL}/{msg_id}"


class FakeChannel:
    """A channel whose history can be paged, and paused to fake a restart."""

    def __init__(self, messages: List[FakeMessage], pause_after: Optional[int] = None):
        self.id = CHANNEL
        self.guild = SimpleNamespace(id=GUILD)
        self.messages = sorted(messages, key=lambda m: m.id, reverse=True)
        self.calls = 0
        self.pause_after = pause_after
        self.paused = asyncio.Event()
        self.release = asyncio.Event()

    def history(self, limit=100, oldest_first=False, before=None, after=None):
        async def gen():
            self.calls += 1
            if self.pause_after is not None and self.calls > self.pause_after:
                self.paused.set()
                await self.release.wait()
            selected = self.messages
            if before is not None:
                selected = [m for m in selected if m.id < before.id]
            if after is not None:
                selected = [m for m in selected if m.id > after.id]
            for message in selected[:limit]:
                yield message

        return gen()


ITEM_LINE = re.compile(r"^(\d+)\. \[[^\]]*\] [^:]*: (.*)$")


class FakeLLM:
    """Reads the prompt the extractor built and 'finds' every KEY: marker.

    It answers in the model's own JSON shape, and upper-cases the key so the
    tests prove the normalizing happens in code rather than in the model.
    """

    def __init__(self, fail_on_calls=()):
        self.calls = 0
        self.prompts: List[str] = []
        self.fail_on_calls = set(fail_on_calls)
        # An async hook called with the call number, for tests that need
        # something to happen partway through a scan.
        self.hook = None

    async def __call__(self, system: str, prompt: str) -> str:
        self.calls += 1
        self.prompts.append(prompt)
        if self.hook is not None:
            await self.hook(self.calls)
        if self.calls in self.fail_on_calls:
            return "I'm afraid I can't do that."
        items = []
        for line in prompt.splitlines():
            match = ITEM_LINE.match(line)
            if not match or "KEY:" not in match.group(2):
                continue
            name = match.group(2).split("KEY:", 1)[1].strip()
            items.append({
                "index": int(match.group(1)),
                "key": name.upper(),
                "text": "someone mentioned " + name,
            })
        return json.dumps({"items": items})


def messages_with(names: List[str], start_id: int = 1) -> List[FakeMessage]:
    return [
        FakeMessage(start_id + i, f"we went there last week, KEY: {name}")
        for i, name in enumerate(names)
    ]


async def run_to_completion(job: Dict[str, Any]) -> None:
    task = scan_runtime.SCAN_TASKS[task_key(job["guild_id"], job["job_id"])]
    await asyncio.wait_for(task, timeout=5)


# --- the extractor -----------------------------------------------------


def test_normalize_key_lowercases_and_collapses():
    assert normalize_key("  Joe's   PIZZA  ") == "joe's pizza"
    assert normalize_key("Tacos!") == "tacos"
    assert normalize_key("   ") == ""


def test_build_prompt_lists_the_page_and_its_attachments():
    page = [
        ScanMessage(1, "moop", "9", "2026-01-01 10:00", "look", ("https://cdn/a.png",), "j1"),
    ]
    prompt = build_prompt("collect images moop posted", page)
    assert "collect images moop posted" in prompt
    assert "1. [2026-01-01 10:00] moop: look" in prompt
    assert "[attachment: https://cdn/a.png]" in prompt


def test_parse_reply_links_items_to_their_message():
    page = [
        ScanMessage(1, "a", "1", "t", "x", ("https://cdn/a.png",), "jump-1"),
        ScanMessage(2, "b", "2", "t", "y", (), "jump-2"),
    ]
    items = parse_reply(
        json.dumps({"items": [
            {"index": 2, "key": "Joe's Pizza", "text": "loved it"},
            {"index": 99, "key": "ghost", "text": "not on this page"},
            {"index": 1, "key": "  ", "text": "no key"},
            {"index": 1, "key": "Snapshot", "text": "a photo"},
        ]}),
        page,
    )
    assert [i.key for i in items] == ["joe's pizza", "snapshot"]
    assert items[0].source_url == "jump-2"
    assert items[1].image_urls == ("https://cdn/a.png",)


def test_parse_reply_reads_a_fenced_reply_but_rejects_garbage():
    page = [ScanMessage(1, "a", "1", "t", "x", (), "jump-1")]
    fenced = '```json\n{"items": [{"index": 1, "key": "tacos", "text": "tacos"}]}\n```'
    assert [i.key for i in parse_reply(fenced, page)] == ["tacos"]
    with pytest.raises(ExtractError):
        parse_reply("sorry, no.", page)
    with pytest.raises(ExtractError):
        parse_reply('{"result": []}', page)


# --- the store ---------------------------------------------------------


@pytest.mark.asyncio
async def test_one_scan_per_channel_at_a_time():
    store = make_store(FakeRedis())
    first = await store.create_job(GUILD, CHANNEL, REQUESTER, "find restaurants")
    assert first is not None
    assert await store.create_job(GUILD, CHANNEL, REQUESTER, "find anything") is None
    # A different channel in the same guild is unaffected.
    assert await store.create_job(GUILD, CHANNEL + 1, REQUESTER, "find anything") is not None
    # Finishing the first one frees its channel.
    await store.finish(GUILD, first["job_id"], "done")
    assert await store.create_job(GUILD, CHANNEL, REQUESTER, "again") is not None


@pytest.mark.asyncio
async def test_finishing_expires_the_job_and_leaves_the_resume_set():
    redis = FakeRedis()
    store = make_store(redis)
    job = await store.create_job(GUILD, CHANNEL, REQUESTER, "find restaurants")
    await store.add_results(GUILD, job["job_id"], [{"key": "tacos", "text": "tacos"}])
    assert await store.list_running_jobs() == [(str(GUILD), job["job_id"])]

    await store.finish(GUILD, job["job_id"], "failed", error="boom")
    record = await store.get_job(GUILD, job["job_id"])
    assert record["status"] == "failed"
    assert record["last_error"] == "boom"
    assert record["finished_at"]
    assert await store.list_running_jobs() == []
    assert redis.expiries[store._job_key(str(GUILD), job["job_id"])] == FINISHED_JOB_TTL_SECONDS
    assert redis.expiries[store._results_key(str(GUILD), job["job_id"])] == FINISHED_JOB_TTL_SECONDS


@pytest.mark.asyncio
async def test_add_results_dedups_on_the_key_and_counts_only_the_new():
    store = make_store(FakeRedis())
    job = await store.create_job(GUILD, CHANNEL, REQUESTER, "find restaurants")
    job_id = job["job_id"]
    assert await store.add_results(GUILD, job_id, [
        {"key": "tacos", "text": "first sighting", "source_url": "jump-1"},
        {"key": "pizza", "text": "pizza", "source_url": "jump-2"},
    ]) == 2
    assert await store.add_results(GUILD, job_id, [
        {"key": "tacos", "text": "second sighting", "source_url": "jump-9"},
        {"key": "ramen", "text": "ramen", "source_url": "jump-3"},
    ]) == 1
    results = {i["key"]: i for i in await store.get_results(GUILD, job_id)}
    assert set(results) == {"tacos", "pizza", "ramen"}
    # First sighting wins, so the source link stays on the newest message.
    assert results["tacos"]["source_url"] == "jump-1"


@pytest.mark.asyncio
async def test_a_stale_channel_pointer_does_not_block_a_new_scan():
    redis = FakeRedis()
    store = make_store(redis)
    job = await store.create_job(GUILD, CHANNEL, REQUESTER, "find restaurants")
    # The job record is evicted but the pointer survives.
    await redis.delete(store._job_key(str(GUILD), job["job_id"]))
    assert await store.get_active_job_for_channel(GUILD, CHANNEL) is None
    assert await store.create_job(GUILD, CHANNEL, REQUESTER, "again") is not None


# --- the loop, end to end through the runner ---------------------------


@pytest.mark.asyncio
async def test_a_scan_pages_to_exhaustion_and_advances_the_cursor():
    store = make_store(FakeRedis())
    channel = FakeChannel(messages_with(["tacos", "pizza", "ramen", "sushi", "curry"]))
    llm = FakeLLM()

    with patch.object(scan_runtime, "PAGE_SIZE", 2):
        job = await start_scan(channel, REQUESTER, "find restaurants", store=store, llm=llm)
        await run_to_completion(job)

    record = await store.get_job(GUILD, job["job_id"])
    assert record["status"] == "done"
    assert record["scanned"] == 5
    assert record["pages"] == 3
    assert record["matched"] == 5
    # The cursor ends on the oldest message, and every page was a real request.
    assert record["cursor"] == 1
    assert llm.calls == 3
    assert {i["key"] for i in await store.get_results(GUILD, job["job_id"])} == {
        "tacos", "pizza", "ramen", "sushi", "curry"
    }


@pytest.mark.asyncio
async def test_the_cursor_moves_backwards_one_page_at_a_time():
    store = make_store(FakeRedis())
    channel = FakeChannel(messages_with(["a", "b", "c", "d"]))
    cursors = []

    class Watcher(FakeLLM):
        async def __call__(self, system, prompt):
            record = await store.get_job(GUILD, job["job_id"])
            cursors.append(record["cursor"])
            return await super().__call__(system, prompt)

    with patch.object(scan_runtime, "PAGE_SIZE", 2):
        job = await start_scan(channel, REQUESTER, "find restaurants", store=store, llm=Watcher())
        await run_to_completion(job)

    # Page 1 covers ids 4,3 (saved cursor still None at extract time), page 2
    # covers 2,1 and starts from the cursor page 1 left behind.
    assert cursors == [None, 3]
    assert (await store.get_job(GUILD, job["job_id"]))["cursor"] == 1


@pytest.mark.asyncio
async def test_the_same_item_on_two_pages_is_counted_once():
    store = make_store(FakeRedis())
    channel = FakeChannel(messages_with(["tacos", "pizza", "TACOS", "tacos  "]))
    llm = FakeLLM()

    with patch.object(scan_runtime, "PAGE_SIZE", 2):
        job = await start_scan(channel, REQUESTER, "find restaurants", store=store, llm=llm)
        await run_to_completion(job)

    record = await store.get_job(GUILD, job["job_id"])
    assert record["scanned"] == 4
    assert record["matched"] == 2
    assert {i["key"] for i in await store.get_results(GUILD, job["job_id"])} == {"tacos", "pizza"}


@pytest.mark.asyncio
async def test_the_accumulated_results_are_never_sent_back_to_the_model():
    store = make_store(FakeRedis())
    channel = FakeChannel(messages_with(["tacos", "pizza", "ramen", "sushi"]))
    llm = FakeLLM()

    with patch.object(scan_runtime, "PAGE_SIZE", 2):
        job = await start_scan(channel, REQUESTER, "find restaurants", store=store, llm=llm)
        await run_to_completion(job)

    # Page 2's prompt knows nothing about what page 1 found: that is the whole
    # point of the design, and it is what keeps a long scan affordable.
    assert "sushi" in llm.prompts[0] and "ramen" in llm.prompts[0]
    assert "sushi" not in llm.prompts[1] and "ramen" not in llm.prompts[1]
    assert len(llm.prompts[1]) < len(llm.prompts[0]) + 200


@pytest.mark.asyncio
async def test_a_page_the_model_cannot_read_does_not_stop_the_scan():
    store = make_store(FakeRedis())
    channel = FakeChannel(messages_with(["tacos", "pizza", "ramen", "sushi"]))
    llm = FakeLLM(fail_on_calls=[1])

    with patch.object(scan_runtime, "PAGE_SIZE", 2):
        job = await start_scan(channel, REQUESTER, "find restaurants", store=store, llm=llm)
        await run_to_completion(job)

    record = await store.get_job(GUILD, job["job_id"])
    assert record["status"] == "done"
    assert record["failed_pages"] == 1
    assert record["scanned"] == 4
    # The unreadable page's items are lost, the rest of the scan is not.
    assert {i["key"] for i in await store.get_results(GUILD, job["job_id"])} == {"tacos", "pizza"}


@pytest.mark.asyncio
async def test_enough_failures_in_a_row_fail_the_job():
    store = make_store(FakeRedis())
    names = ["r%d" % i for i in range(MAX_CONSECUTIVE_FAILURES + 3)]
    channel = FakeChannel(messages_with(names))
    llm = FakeLLM(fail_on_calls=range(1, MAX_CONSECUTIVE_FAILURES + 1))

    with patch.object(scan_runtime, "PAGE_SIZE", 1):
        job = await start_scan(channel, REQUESTER, "find restaurants", store=store, llm=llm)
        await run_to_completion(job)

    record = await store.get_job(GUILD, job["job_id"])
    assert record["status"] == "failed"
    assert record["failed_pages"] == MAX_CONSECUTIVE_FAILURES
    assert "could not be read" in record["last_error"]
    # It stopped at the threshold instead of burning through the channel.
    assert llm.calls == MAX_CONSECUTIVE_FAILURES
    assert await store.get_active_job_for_channel(GUILD, CHANNEL) is None


@pytest.mark.asyncio
async def test_a_failure_streak_is_reset_by_one_good_page():
    store = make_store(FakeRedis())
    names = ["r%d" % i for i in range(12)]
    channel = FakeChannel(messages_with(names))
    # Fail four, succeed once, fail four more: never five in a row.
    llm = FakeLLM(fail_on_calls=[1, 2, 3, 4, 6, 7, 8, 9])

    with patch.object(scan_runtime, "PAGE_SIZE", 1):
        job = await start_scan(channel, REQUESTER, "find restaurants", store=store, llm=llm)
        await run_to_completion(job)

    record = await store.get_job(GUILD, job["job_id"])
    assert record["status"] == "done"
    assert record["failed_pages"] == 8
    assert record["scanned"] == 12


@pytest.mark.asyncio
async def test_a_cancel_stops_the_scan_between_pages_and_keeps_what_it_found():
    store = make_store(FakeRedis())
    channel = FakeChannel(messages_with(["tacos", "pizza", "ramen", "sushi", "curry", "pho"]))
    llm = FakeLLM()
    holder: Dict[str, Any] = {}

    async def cancel_after_first_page(call_number):
        if call_number == 1:
            await store.request_cancel(GUILD, holder["job"]["job_id"])

    llm.hook = cancel_after_first_page

    with patch.object(scan_runtime, "PAGE_SIZE", 2):
        job = await start_scan(channel, REQUESTER, "find restaurants", store=store, llm=llm)
        holder["job"] = job
        await run_to_completion(job)

    record = await store.get_job(GUILD, job["job_id"])
    assert record["status"] == "cancelled"
    assert llm.calls == 1                      # the cancel landed before page 2
    assert record["scanned"] == 2
    # History is read newest first, so page 1 is the two most recent messages.
    assert {i["key"] for i in await store.get_results(GUILD, job["job_id"])} == {"pho", "curry"}
    # The channel is free again and the flag is gone with the job.
    assert await store.get_active_job_for_channel(GUILD, CHANNEL) is None
    assert await store.is_cancel_requested(GUILD, job["job_id"]) is False


@pytest.mark.asyncio
async def test_a_cancel_from_another_process_is_honoured_before_the_first_page():
    store = make_store(FakeRedis())
    channel = FakeChannel(messages_with(["tacos"]))
    llm = FakeLLM()
    job = await store.create_job(GUILD, CHANNEL, REQUESTER, "find restaurants")
    await store.request_cancel(GUILD, job["job_id"])

    await scan_runtime._execute(job, channel, store, llm)

    record = await store.get_job(GUILD, job["job_id"])
    assert record["status"] == "cancelled"
    assert channel.calls == 0
    assert llm.calls == 0


@pytest.mark.asyncio
async def test_starting_a_second_scan_in_a_channel_is_refused():
    store = make_store(FakeRedis())
    channel = FakeChannel(messages_with(["tacos"]), pause_after=0)
    llm = FakeLLM()

    job = await start_scan(channel, REQUESTER, "find restaurants", store=store, llm=llm)
    await asyncio.wait_for(channel.paused.wait(), timeout=5)

    with pytest.raises(ScanAlreadyRunning) as raised:
        await start_scan(channel, REQUESTER, "find anything else", store=store, llm=llm)
    assert raised.value.job["job_id"] == job["job_id"]

    channel.release.set()
    await run_to_completion(job)
    # Once it is done, the channel takes a new scan.
    assert await start_scan(channel, REQUESTER, "find anything else", store=store, llm=llm)


@pytest.mark.asyncio
async def test_a_scan_resumes_from_its_cursor_after_a_restart():
    redis = FakeRedis()
    store = make_store(redis)
    channel = FakeChannel(
        messages_with(["tacos", "pizza", "ramen", "sushi", "curry", "pho"]),
        pause_after=1,
    )
    llm = FakeLLM()

    with patch.object(scan_runtime, "PAGE_SIZE", 2):
        job = await start_scan(channel, REQUESTER, "find restaurants", store=store, llm=llm)
        task = scan_runtime.SCAN_TASKS[task_key(job["guild_id"], job["job_id"])]
        await asyncio.wait_for(channel.paused.wait(), timeout=5)

        # The process goes down mid-scan.
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        interrupted = await store.get_job(GUILD, job["job_id"])
        assert interrupted["status"] == "running"      # still ours to finish
        assert interrupted["cursor"] == 5              # page 1 covered ids 6 and 5
        assert interrupted["scanned"] == 2
        assert await store.list_running_jobs() == [(str(GUILD), job["job_id"])]

        # A fresh process: a new store on the same Redis, a new channel object.
        channel.release.set()
        channel.pause_after = None
        fresh_store = make_store(redis)
        client = SimpleNamespace()
        async def _resolve(_client, _channel_id):
            return channel

        with patch.object(scan_runtime, "resolve_channel", _resolve):
            resumed = await scan_runtime.resume_running_jobs(client, fresh_store, llm)
        assert resumed == 1
        await run_to_completion(job)

    record = await fresh_store.get_job(GUILD, job["job_id"])
    assert record["status"] == "done"
    # Two pages before the restart's page, four after: nothing re-read, nothing
    # skipped, and the results from before the restart are still there.
    assert record["scanned"] == 6
    assert record["matched"] == 6
    assert {i["key"] for i in await fresh_store.get_results(GUILD, job["job_id"])} == {
        "tacos", "pizza", "ramen", "sushi", "curry", "pho"
    }


@pytest.mark.asyncio
async def test_resume_drops_jobs_that_are_no_longer_running():
    redis = FakeRedis()
    store = make_store(redis)
    job = await store.create_job(GUILD, CHANNEL, REQUESTER, "find restaurants")
    await redis.delete(store._job_key(str(GUILD), job["job_id"]))

    resumed = await scan_runtime.resume_running_jobs(SimpleNamespace(), store)

    assert resumed == 0
    assert await store.list_running_jobs() == []


@pytest.mark.asyncio
async def test_resume_fails_a_job_whose_channel_is_gone():
    store = make_store(FakeRedis())
    job = await store.create_job(GUILD, CHANNEL, REQUESTER, "find restaurants")

    async def _missing(_client, _channel_id):
        raise ValueError("Unknown Channel")

    with patch.object(scan_runtime, "resolve_channel", _missing):
        assert await scan_runtime.resume_running_jobs(SimpleNamespace(), store) == 0

    record = await store.get_job(GUILD, job["job_id"])
    assert record["status"] == "failed"
    assert "Channel unreachable" in record["last_error"]


@pytest.mark.asyncio
async def test_a_crashing_scan_is_recorded_as_failed():
    store = make_store(FakeRedis())

    class ExplodingChannel(FakeChannel):
        def history(self, **kwargs):
            raise RuntimeError("gateway is on fire")

    job = await start_scan(
        ExplodingChannel(messages_with(["tacos"])), REQUESTER, "find restaurants",
        store=store, llm=FakeLLM(),
    )
    await run_to_completion(job)

    record = await store.get_job(GUILD, job["job_id"])
    assert record["status"] == "failed"
    assert "gateway is on fire" in record["last_error"]
    assert await store.get_active_job_for_channel(GUILD, CHANNEL) is None


@pytest.mark.asyncio
async def test_the_finish_callback_gets_the_finished_record():
    store = make_store(FakeRedis())
    channel = FakeChannel(messages_with(["tacos"]))
    seen: List[Any] = []

    async def on_finish(record, summary):
        seen.append((record["status"], summary.matched))

    job = await start_scan(
        channel, REQUESTER, "find restaurants", store=store, llm=FakeLLM(),
        on_finish=on_finish,
    )
    await run_to_completion(job)
    assert seen == [("done", 1)]


# --- the page fetcher --------------------------------------------------


@pytest.mark.asyncio
async def test_the_page_fetcher_skips_bots_but_still_advances_the_cursor():
    channel = FakeChannel([
        FakeMessage(3, "beep", author="CunningBot", bot=True),
        FakeMessage(2, "boop", author="CunningBot", bot=True),
        FakeMessage(1, "we went there, KEY: tacos"),
    ])
    fetch = scan_runtime.page_fetcher(channel, page_size=2)

    page = await fetch(None)
    assert page.messages == []       # nothing for the model to read...
    assert page.read == 2            # ...but the page was not empty
    assert page.oldest_id == 2
    assert page.exhausted is False

    page = await fetch(page.oldest_id)
    assert [m.id for m in page.messages] == [1]
    assert await fetch(1) == ScanPage(messages=[], oldest_id=None, read=0)


@pytest.mark.asyncio
async def test_the_page_fetcher_keeps_attachments_jump_urls_and_embed_text():
    channel = FakeChannel([
        FakeMessage(2, "", attachments=("https://cdn/a.png", "https://cdn/b.png")),
        FakeMessage(1, "", embeds=[SimpleNamespace(title="Big news", description="a story")]),
    ])
    page = await scan_runtime.page_fetcher(channel)(None)

    assert page.messages[0].attachment_urls == ("https://cdn/a.png", "https://cdn/b.png")
    assert page.messages[0].jump_url.endswith("/2")
    assert "Big news a story" in page.messages[1].content


@pytest.mark.asyncio
async def test_the_page_fetcher_honours_the_after_bound():
    channel = FakeChannel(messages_with(["a", "b", "c", "d"]))
    fetch = scan_runtime.page_fetcher(channel, after_id=2, page_size=2)

    page = await fetch(None)
    assert [m.id for m in page.messages] == [4, 3]
    # The scan stops at the bound instead of running to the start of the channel.
    assert (await fetch(page.oldest_id)).exhausted is True


# --- the loop on its own -----------------------------------------------


@pytest.mark.asyncio
async def test_run_scan_leaves_an_unfinished_job_running_when_max_pages_stops_it():
    pages = [
        ScanPage(messages=[ScanMessage(2, "a", "1", "t", "x", (), "j2")], oldest_id=2, read=1),
        ScanPage(messages=[ScanMessage(1, "a", "1", "t", "y", (), "j1")], oldest_id=1, read=1),
    ]

    async def fetch(cursor):
        return pages.pop(0) if pages else ScanPage()

    async def extract(messages):
        return []

    async def save_results(items):
        return 0

    saved: List[Any] = []

    async def save_progress(progress):
        saved.append(progress.cursor)

    summary = await run_scan({}, fetch, extract, save_results, save_progress, max_pages=1)

    assert summary.status == "running"
    assert summary.cursor == 2
    assert saved[-1] == 2
