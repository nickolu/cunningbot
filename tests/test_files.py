"""Tests for user uploads: the service, the two agent tools, the watcher, /files."""

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from bot.domain.files import file_service
from bot.domain.files.file_service import (
    DEFAULT_MAX_FILES,
    DEFAULT_TTL_DAYS,
    MAX_FILES_CAP,
    UploadLink,
    clamp_max_files,
    clamp_ttl,
    describe_file,
    expires_in_words,
    format_bytes,
    normalize_kind,
)

GUILD = 844003671334977607


@pytest.fixture(autouse=True)
def _pages_env():
    with patch.dict(os.environ, {
        "PAGES_BASE_URL": "https://pages.example",
        "PAGES_PUBLISH_TOKEN": "unit-test-token",
    }):
        yield


def _channel(**extra):
    return SimpleNamespace(
        guild=SimpleNamespace(id=GUILD, name="g"), id=42, name="general",
        send=AsyncMock(), **extra,
    )


def _user():
    return SimpleNamespace(id=325893569220968448, display_name="Nick", mention="<@325893569220968448>")


def _link(**over):
    base = dict(
        session_id="a" * 24, url="https://pages.example/u/" + "a" * 24,
        expires_at="2099-01-01T00:15:00+00:00", max_files=10,
        max_bytes=25 * 1024 * 1024, ttl_days=7, kind="any",
    )
    base.update(over)
    return UploadLink(**base)


def _file(**over):
    base = dict(
        id="f" * 24, name="receipt.pdf", content_type="application/pdf", bytes=120_000,
        note="dinner", label="trip", uploader_id="325893569220968448", uploader_name="Nick",
        created_at="2026-11-01T12:00:00.000Z", expires_at="2026-11-08T12:00:00.000Z",
        url="https://pages.example/f/" + "f" * 24,
    )
    base.update(over)
    return base


# --------------------------------------------------------------------------
# Choosing limits
# --------------------------------------------------------------------------

def test_ttl_snaps_up_to_a_lifecycle_tier():
    assert clamp_ttl(None) == DEFAULT_TTL_DAYS
    assert clamp_ttl(1) == 1
    assert clamp_ttl(3) == 7, "between tiers rounds up, never silently shortens"
    assert clamp_ttl(30) == 30
    assert clamp_ttl(400) == 30
    assert clamp_ttl("junk") == DEFAULT_TTL_DAYS


def test_max_files_is_bounded():
    assert clamp_max_files(None) == DEFAULT_MAX_FILES
    assert clamp_max_files(0) == 1
    assert clamp_max_files(999) == MAX_FILES_CAP
    assert clamp_max_files("x") == DEFAULT_MAX_FILES


def test_kind_defaults_to_any():
    assert normalize_kind("images") == "images"
    assert normalize_kind("IMAGES ") == "images"
    assert normalize_kind("pictures") == "any"
    assert normalize_kind(None) == "any"


# --------------------------------------------------------------------------
# The service passes the right thing to the client
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_open_upload_link_sends_bounded_limits_and_returns_the_link():
    create = AsyncMock(return_value={
        "session_id": "a" * 24, "url": "https://pages.example/u/" + "a" * 24,
        "expires_at": "2099-01-01T00:15:00.000Z", "max_files": 25,
        "max_bytes": 25 * 1024 * 1024, "ttl_days": 30, "kind": "images",
    })
    with patch.object(file_service.PagesClient, "create_upload_session", create):
        link = await file_service.open_upload_link(
            str(GUILD), channel_id="42", requester_id="7", requester_name="Nick",
            label="  Tahoe photos  ", kind="images", max_files=100, ttl_days=20,
        )

    kwargs = create.call_args.kwargs
    assert kwargs["max_files"] == MAX_FILES_CAP
    assert kwargs["ttl_days"] == 30
    assert kwargs["kind"] == "images"
    assert kwargs["label"] == "Tahoe photos"
    assert kwargs["guild_id"] == str(GUILD)
    assert link.url.endswith("/u/" + "a" * 24)
    assert link.kind == "images" and link.ttl_days == 30


@pytest.mark.asyncio
async def test_search_files_returns_files_and_total():
    listing = AsyncMock(return_value={"files": [_file()], "total": 3})
    with patch.object(file_service.PagesClient, "list_files", listing):
        files, total = await file_service.search_files(str(GUILD), query="receipt", limit=1)
    assert len(files) == 1 and total == 3
    assert listing.call_args.kwargs == {"query": "receipt", "limit": 1}


# --------------------------------------------------------------------------
# Formatting
# --------------------------------------------------------------------------

def test_format_bytes():
    assert format_bytes(512) == "512 B"
    assert format_bytes(120_000) == "117 KB"
    assert format_bytes(25 * 1024 * 1024) == "25.0 MB"
    assert format_bytes("x") == ""


def test_describe_file_has_what_a_person_needs_to_pick_it():
    line = describe_file(_file())
    assert "receipt.pdf" in line
    assert "Nick" in line
    assert "2026-11-01" in line
    assert "dinner" in line
    assert "[trip]" in line
    assert line.rstrip().endswith("/f/" + "f" * 24)


def test_expires_in_words():
    from datetime import datetime, timezone
    now = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    assert expires_in_words("2026-11-01T12:14:30Z", now) == "in 14 minutes"
    assert expires_in_words("2026-11-01T12:00:50Z", now) == "in about a minute"
    assert expires_in_words("2026-11-01T12:00:10Z", now) == "now"
    assert expires_in_words("garbage", now) == "in 15 minutes"


# --------------------------------------------------------------------------
# request_upload tool
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_request_upload_returns_the_link_and_starts_a_watch():
    from bot.domain.agent.tools.request_upload import execute_request_upload

    channel, user = _channel(), _user()
    with patch("bot.domain.files.file_service.open_upload_link",
               new=AsyncMock(return_value=_link())) as opened, \
         patch("bot.app.upload_watch.start_upload_watch") as watch:
        result = await execute_request_upload({"label": "Tahoe photos", "kind": "images"}, channel, user)

    assert "https://pages.example/u/" + "a" * 24 in result
    assert "do not claim" in result
    assert opened.call_args.kwargs["requester_id"] == str(user.id)
    assert opened.call_args.kwargs["channel_id"] == "42"
    assert opened.call_args.kwargs["label"] == "Tahoe photos"
    watch.assert_called_once()
    assert watch.call_args.args[2] == "a" * 24
    assert watch.call_args.kwargs["label"] == "Tahoe photos"


@pytest.mark.asyncio
async def test_request_upload_copes_without_a_user():
    from bot.domain.agent.tools.request_upload import execute_request_upload

    with patch("bot.domain.files.file_service.open_upload_link",
               new=AsyncMock(return_value=_link())) as opened, \
         patch("bot.app.upload_watch.start_upload_watch"):
        result = await execute_request_upload({}, _channel(), None)

    assert "Upload link opened" in result
    assert opened.call_args.kwargs["requester_id"] is None


@pytest.mark.asyncio
async def test_request_upload_reports_each_way_the_service_can_be_missing():
    from bot.api.pages.client import PagesNotDeployed, UploadsNotConfigured
    from bot.domain.agent.tools.request_upload import execute_request_upload

    cases = [
        (EnvironmentError("no env"), "not configured"),
        (UploadsNotConfigured("no bucket"), "storage bucket"),
        (PagesNotDeployed("no route"), "older version"),
        (RuntimeError("HTTP 502"), "Could not open"),
    ]
    for exc, expected in cases:
        with patch("bot.domain.files.file_service.open_upload_link",
                   new=AsyncMock(side_effect=exc)), \
             patch("bot.app.upload_watch.start_upload_watch") as watch:
            result = await execute_request_upload({}, _channel(), _user())
        assert expected in result, (exc, result)
        watch.assert_not_called()


@pytest.mark.asyncio
async def test_request_upload_requires_a_guild():
    from bot.domain.agent.tools.request_upload import execute_request_upload

    result = await execute_request_upload({}, SimpleNamespace(guild=None), _user())
    assert "inside a server" in result


# --------------------------------------------------------------------------
# search_files tool
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_files_tool_lists_matches_with_links():
    from bot.domain.agent.tools.search_files import execute_search_files

    with patch("bot.domain.files.file_service.search_files",
               new=AsyncMock(return_value=([_file(), _file(id="e" * 24, name="menu.jpg")], 12))):
        result = await execute_search_files({"query": "receipt"}, _channel())

    assert "12 uploaded file(s) match 'receipt'" in result
    assert "newest 2 shown" in result
    assert "receipt.pdf" in result and "menu.jpg" in result
    assert "/f/" + "f" * 24 in result


@pytest.mark.asyncio
async def test_search_files_tool_says_when_nothing_matches():
    from bot.domain.agent.tools.search_files import execute_search_files

    with patch("bot.domain.files.file_service.search_files", new=AsyncMock(return_value=([], 0))):
        with_query = await execute_search_files({"query": "unicorn"}, _channel())
        without = await execute_search_files({}, _channel())

    assert "No uploaded files match 'unicorn'" in with_query
    assert "Nothing has been uploaded" in without


@pytest.mark.asyncio
async def test_search_files_tool_degrades_when_the_service_predates_it():
    from bot.api.pages.client import PagesNotDeployed
    from bot.domain.agent.tools.search_files import execute_search_files

    with patch("bot.domain.files.file_service.search_files",
               new=AsyncMock(side_effect=PagesNotDeployed("no route"))):
        result = await execute_search_files({}, _channel())
    assert "older version" in result


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------

def test_tools_are_registered_with_the_right_flags():
    from bot.app.redis.migrations.backfill_agent_tools import DEFAULT_TOOLS_TO_ADD
    from bot.domain.agent.tools.registry import (
        CHANNEL_AWARE_TOOLS, DEFAULT_ENABLED_TOOLS, SCHEDULED_OK_TOOLS, TOOL_SCHEMAS,
        USER_AWARE_TOOLS,
    )

    assert {"request_upload", "search_files"} <= set(TOOL_SCHEMAS)
    assert {"request_upload", "search_files"} <= set(DEFAULT_ENABLED_TOOLS)
    assert {"request_upload", "search_files"} <= set(DEFAULT_TOOLS_TO_ADD)
    assert {"request_upload", "search_files"} <= CHANNEL_AWARE_TOOLS
    assert "request_upload" in USER_AWARE_TOOLS
    assert "search_files" not in USER_AWARE_TOOLS
    assert "request_upload" not in SCHEDULED_OK_TOOLS, "an unattended run has nobody to upload"
    assert "search_files" in SCHEDULED_OK_TOOLS


def test_system_prompt_and_help_mention_uploads():
    from bot.domain.agent.agent_service import AGENT_SYSTEM_PROMPT
    from bot.app.commands.help import HELP_PAGES

    assert "request_upload" in AGENT_SYSTEM_PROMPT
    assert "search_files" in AGENT_SYSTEM_PROMPT
    text = " ".join(f.value for page in HELP_PAGES for f in page.fields)
    assert "/files upload" in text
    assert "/files list" in text
    assert "/files delete" in text


# --------------------------------------------------------------------------
# The watcher
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_watcher_posts_once_the_link_is_full():
    from bot.app.upload_watch import watch_upload_session

    polls = iter([
        {"session": {"open": True}, "files": []},
        {"session": {"open": True}, "files": [_file()]},
        {"session": {"open": True}, "files": [_file(), _file(id="e" * 24, name="menu.jpg")]},
    ])
    sleep = AsyncMock()
    channel = _channel()
    with patch("bot.app.upload_watch.read_upload_session",
               new=AsyncMock(side_effect=lambda g, s: next(polls))):
        files = await watch_upload_session(
            channel, str(GUILD), "a" * 24, "2099-01-01T00:00:00Z", max_files=2,
            requester_mention="<@7>", label="Tahoe photos", sleep=sleep,
        )

    assert len(files) == 2
    assert sleep.await_count == 3
    channel.send.assert_awaited_once()
    embed = channel.send.call_args.kwargs["embed"]
    assert embed.title == "Tahoe photos"
    assert "2 files came in" in embed.description
    assert "<@7>" in embed.description
    assert "menu.jpg" in embed.description


@pytest.mark.asyncio
async def test_watcher_stops_when_the_link_closes_and_stays_quiet_if_empty():
    from bot.app.upload_watch import watch_upload_session

    channel = _channel()
    with patch("bot.app.upload_watch.read_upload_session",
               new=AsyncMock(return_value={"session": {"open": False}, "files": []})):
        files = await watch_upload_session(
            channel, str(GUILD), "a" * 24, "2099-01-01T00:00:00Z", max_files=10, sleep=AsyncMock(),
        )

    assert files == []
    channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_watcher_gives_up_at_the_deadline_even_if_polling_fails():
    from bot.app import upload_watch
    from bot.app.upload_watch import watch_upload_session

    channel = _channel()
    sleep = AsyncMock()
    # Already expired: one grace poll, then stop -- a failing poll must not loop forever.
    with patch("bot.app.upload_watch.read_upload_session",
               new=AsyncMock(side_effect=RuntimeError("down"))):
        files = await watch_upload_session(
            channel, str(GUILD), "a" * 24, "2000-01-01T00:00:00Z", max_files=10, sleep=sleep,
        )

    assert files == []
    assert sleep.await_count <= (upload_watch.GRACE_SECONDS // upload_watch.POLL_SECONDS) + 1
    channel.send.assert_not_awaited()


def test_only_one_watcher_per_session():
    from bot.app import upload_watch

    async def run():
        with patch("bot.app.upload_watch.watch_upload_session", new=AsyncMock(return_value=[])):
            first = upload_watch.start_upload_watch(_channel(), str(GUILD), "b" * 24, "2099-01-01T00:00:00Z", 1)
            second = upload_watch.start_upload_watch(_channel(), str(GUILD), "b" * 24, "2099-01-01T00:00:00Z", 1)
            assert first is not None and second is None
            await first
        assert "b" * 24 not in upload_watch._watchers

    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(run())


# --------------------------------------------------------------------------
# /files delete permissions
# --------------------------------------------------------------------------

def test_may_delete_is_the_opener_or_a_moderator():
    from bot.app.commands.files.files import may_delete

    record = _file(uploader_id="7")
    assert may_delete(record, 7, manage_messages=False)
    assert may_delete(record, 8, manage_messages=True)
    assert not may_delete(record, 8, manage_messages=False)
    assert not may_delete(_file(uploader_id=None), 7, manage_messages=False)


# --------------------------------------------------------------------------
# Client: feature detection
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_client_tells_a_missing_route_from_a_missing_session():
    """Vercel answers an unknown route with HTML; the API answers 404 as JSON."""
    from bot.api.pages.client import PagesClient, PagesNotDeployed

    class FakeResponse:
        def __init__(self, status, content_type, payload=None):
            self.status, self.content_type, self._payload = status, content_type, payload
        async def json(self): return self._payload
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    class FakeSession:
        def __init__(self, response): self._response = response
        def request(self, *a, **k): return self._response
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    client = PagesClient()
    with patch("aiohttp.ClientSession", return_value=FakeSession(FakeResponse(404, "text/html"))):
        with pytest.raises(PagesNotDeployed):
            await client.get_upload_session("a" * 24, str(GUILD))

    with patch("aiohttp.ClientSession",
               return_value=FakeSession(FakeResponse(404, "application/json", {"error": "not_found"}))):
        assert await client.get_upload_session("a" * 24, str(GUILD)) is None

    with patch("aiohttp.ClientSession",
               return_value=FakeSession(FakeResponse(503, "application/json", {"error": "storage_not_configured"}))):
        from bot.api.pages.client import UploadsNotConfigured
        with pytest.raises(UploadsNotConfigured):
            await client.create_upload_session(str(GUILD))
