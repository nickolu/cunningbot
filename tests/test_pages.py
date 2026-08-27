"""Tests for published pages: guild isolation, rendering, and the agent tool."""

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from bot.domain.pages.page_ids import PageIdError, derive_page_id, slugify
from bot.domain.pages.page_renderer import render_page


@pytest.fixture(autouse=True)
def _id_secret():
    with patch.dict(os.environ, {"PAGES_ID_SECRET": "unit-test-secret"}):
        yield


# --------------------------------------------------------------------------
# Guild isolation — the whole reason page ids are derived rather than composed
# --------------------------------------------------------------------------

def test_same_slug_in_different_guilds_yields_different_ids():
    a = derive_page_id("111111111111111111", "restaurants")
    b = derive_page_id("222222222222222222", "restaurants")
    assert a != b


def test_page_id_is_stable_for_a_guild():
    first = derive_page_id("111111111111111111", "restaurants")
    second = derive_page_id("111111111111111111", "Restaurants")
    assert first == second, "slug normalization must not change the URL"


def test_page_id_is_not_derivable_from_the_guild_id_alone():
    """A guild id is public, so it must not appear in the page id."""
    guild_id = "123456789012345678"
    page_id = derive_page_id(guild_id, "restaurants")
    assert guild_id not in page_id


def test_page_id_changes_with_the_secret():
    with_a = derive_page_id("111", "restaurants")
    with patch.dict(os.environ, {"PAGES_ID_SECRET": "a-different-secret"}):
        with_b = derive_page_id("111", "restaurants")
    assert with_a != with_b


def test_missing_secret_raises():
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(PageIdError):
            derive_page_id("111", "restaurants")


def test_omitted_slug_produces_a_unique_page_each_time():
    assert derive_page_id("111") != derive_page_id("111")


def test_slugify_strips_unsafe_characters():
    assert slugify("Restaurants to Visit!") == "restaurants-to-visit"
    assert slugify("  ../etc/passwd  ") == "etc-passwd"


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def test_raw_html_in_markdown_is_escaped_not_rendered():
    out = render_page("T", "<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>")
    body = out.split("<h1>T</h1>")[1].split("</main>")[0]
    # The payload may still appear as text; what matters is that no live tag does.
    assert "<script" not in body
    assert "<img" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
    assert "&lt;img src=x onerror=alert(1)&gt;" in body


def test_title_is_escaped():
    out = render_page("<script>x</script>", "hi")
    assert "<title>&lt;script&gt;x&lt;/script&gt;</title>" in out


def test_markdown_features_render():
    out = render_page("T", "# H\n\n- one\n- two\n\n| a | b |\n|---|---|\n| 1 | 2 |")
    assert "<li>one</li>" in out
    assert "table-wrap" in out, "wide tables must scroll in their own container"


def test_page_is_self_contained():
    out = render_page("T", "body")
    assert out.startswith("<!doctype html>")
    assert "<style>" in out
    assert "http://" not in out and "src=" not in out, "no external assets"


# --------------------------------------------------------------------------
# Agent tool
# --------------------------------------------------------------------------

def _channel(guild_id=987654321, name="Test Server"):
    return SimpleNamespace(guild=SimpleNamespace(id=guild_id, name=name))


@pytest.mark.asyncio
async def test_publish_page_tool_returns_the_url():
    from bot.domain.agent.agent_tools import execute_publish_page

    with patch(
        "bot.domain.pages.page_service.publish_page",
        new=AsyncMock(return_value="https://example.vercel.app/p/restaurants-abc123"),
    ) as pub:
        result = await execute_publish_page(
            {"title": "Restaurants", "markdown": "- Yummy Noodles", "slug": "restaurants"},
            _channel(),
        )

    assert "https://example.vercel.app/p/restaurants-abc123" in result
    assert pub.await_args.kwargs["guild_id"] == "987654321"
    assert pub.await_args.kwargs["slug"] == "restaurants"


@pytest.mark.asyncio
async def test_publish_page_tool_reports_missing_config_without_raising():
    from bot.domain.agent.agent_tools import execute_publish_page

    with patch(
        "bot.domain.pages.page_service.publish_page",
        new=AsyncMock(side_effect=EnvironmentError("nope")),
    ):
        result = await execute_publish_page(
            {"title": "T", "markdown": "body"}, _channel()
        )

    assert "not available" in result.lower()


@pytest.mark.asyncio
async def test_publish_page_tool_rejects_empty_content():
    from bot.domain.agent.agent_tools import execute_publish_page

    result = await execute_publish_page({"title": "T", "markdown": "  "}, _channel())
    assert "no page content" in result.lower()


@pytest.mark.asyncio
async def test_publish_page_tool_requires_a_guild():
    from bot.domain.agent.agent_tools import execute_publish_page

    result = await execute_publish_page(
        {"title": "T", "markdown": "body"}, SimpleNamespace(guild=None)
    )
    assert "server" in result.lower()


# --------------------------------------------------------------------------
# Registration — all five places the skill warns about
# --------------------------------------------------------------------------

def test_tool_is_registered_everywhere():
    from bot.app.redis.agent_store import DEFAULT_AGENT_CONFIG
    from bot.domain.agent.agent_service import AGENT_SYSTEM_PROMPT
    from bot.domain.agent.agent_tools import (
        CHANNEL_AWARE_TOOLS, TOOL_EXECUTORS, TOOL_SCHEMAS,
    )

    assert "publish_page" in TOOL_SCHEMAS
    assert TOOL_SCHEMAS["publish_page"]["function"]["name"] == "publish_page"
    assert "publish_page" in TOOL_EXECUTORS
    assert "publish_page" in CHANNEL_AWARE_TOOLS
    assert "publish_page" in DEFAULT_AGENT_CONFIG["tools"]
    assert "publish_page" in AGENT_SYSTEM_PROMPT


# --------------------------------------------------------------------------
# Page memory: finding a page again, and reading it back before replacing it
# --------------------------------------------------------------------------

from bot.domain.pages.page_ids import looks_like_page_id, slug_from_page_id  # noqa: E402


def test_page_id_and_slug_round_trip():
    page_id = derive_page_id("111111111111111111", "Bot Fails Wishlist")
    assert looks_like_page_id(page_id)
    assert slug_from_page_id(page_id) == "bot-fails-wishlist"


def test_a_bare_slug_is_not_mistaken_for_a_page_id():
    """Deriving an id from something already an id yields a different page."""
    assert not looks_like_page_id("restaurants")
    assert not looks_like_page_id("restaurants-notahexdigest")


@pytest.mark.asyncio
async def test_publish_defaults_to_a_slug_from_the_title():
    """The orphaning bug: no slug used to mean a random, unfindable URL."""
    from bot.domain.pages import page_service

    with patch.object(page_service, "PagesClient") as client_cls:
        client_cls.return_value.publish = AsyncMock(return_value={"url": "https://x/p/y"})
        await page_service.publish_page(
            guild_id="111", title="Bot Fails Wishlist", markdown="- one"
        )
        first = client_cls.return_value.publish.await_args.kwargs["page_id"]

        client_cls.return_value.publish = AsyncMock(return_value={"url": "https://x/p/y"})
        await page_service.publish_page(
            guild_id="111", title="Bot Fails Wishlist", markdown="- one\n- two"
        )
        second = client_cls.return_value.publish.await_args.kwargs["page_id"]

    assert first == second, "same title must republish in place, not fork a page"
    assert slug_from_page_id(first) == "bot-fails-wishlist"


@pytest.mark.asyncio
async def test_one_off_still_gets_a_unique_url():
    from bot.domain.pages import page_service

    ids = []
    for _ in range(2):
        with patch.object(page_service, "PagesClient") as client_cls:
            client_cls.return_value.publish = AsyncMock(return_value={"url": "https://x/p/y"})
            await page_service.publish_page(
                guild_id="111", title="Reasoning trace", markdown="...", one_off=True
            )
            ids.append(client_cls.return_value.publish.await_args.kwargs["page_id"])

    assert ids[0] != ids[1]


@pytest.mark.asyncio
async def test_slugged_pages_outlive_snapshots():
    from bot.domain.pages import page_service

    with patch.object(page_service, "PagesClient") as client_cls:
        client_cls.return_value.publish = AsyncMock(return_value={"url": "https://x/p/y"})
        await page_service.publish_page(guild_id="111", title="A List", markdown="-")
        stable = client_cls.return_value.publish.await_args.kwargs["ttl_days"]

        await page_service.publish_page(
            guild_id="111", title="A Trace", markdown="-", one_off=True
        )
        snapshot = client_cls.return_value.publish.await_args.kwargs["ttl_days"]

    assert stable == page_service.STABLE_TTL_DAYS
    assert snapshot == page_service.SNAPSHOT_TTL_DAYS
    assert stable > snapshot


@pytest.mark.asyncio
async def test_markdown_is_stored_so_it_can_be_read_back():
    from bot.domain.pages import page_service

    with patch.object(page_service, "PagesClient") as client_cls:
        client_cls.return_value.publish = AsyncMock(return_value={"url": "https://x/p/y"})
        await page_service.publish_page(guild_id="111", title="A List", markdown="- one")
        assert client_cls.return_value.publish.await_args.kwargs["markdown"] == "- one"


@pytest.mark.asyncio
async def test_read_page_accepts_a_url_as_well_as_a_slug():
    from bot.domain.pages import page_service

    page_id = derive_page_id("111", "wishlist")
    with patch.object(page_service, "PagesClient") as client_cls:
        client_cls.return_value.fetch_source = AsyncMock(
            return_value={"title": "Wishlist", "markdown": "- one", "updated_at": "2026-08-27"}
        )
        client_cls.return_value.page_url = lambda pid: f"https://x/p/{pid}"

        by_slug = await page_service.read_page("111", "wishlist")
        by_url = await page_service.read_page("111", f"https://x/p/{page_id}")

    assert by_slug["markdown"] == "- one"
    assert by_url["slug"] == by_slug["slug"] == "wishlist"


@pytest.mark.asyncio
async def test_read_page_warns_rather_than_pretending_a_page_is_empty():
    """A page from before source storage must not read as blank."""
    from bot.domain.agent.tools.read_page import execute_read_page

    channel = SimpleNamespace(guild=SimpleNamespace(id=111, name="g"))
    with patch("bot.domain.pages.page_service.read_page",
               new=AsyncMock(return_value={
                   "slug": "old", "title": "Old Page", "markdown": None,
                   "url": "https://x/p/old-aaaaaaaaaaaaaaaa", "updated_at": None,
               })):
        result = await execute_read_page({"page": "old"}, channel)

    assert "before pages kept their source" in result
    assert "replace" in result


@pytest.mark.asyncio
async def test_tools_degrade_when_the_service_predates_them():
    """web/ deploys separately, so the bot can be ahead of it."""
    from bot.api.pages.client import PagesNotDeployed
    from bot.domain.agent.tools.list_pages import execute_list_pages

    channel = SimpleNamespace(guild=SimpleNamespace(id=111, name="g"))
    with patch("bot.domain.pages.page_service.list_pages",
               new=AsyncMock(side_effect=PagesNotDeployed("no route"))):
        result = await execute_list_pages({}, channel)

    assert "older version" in result
    assert "Could not" not in result


def test_new_page_tools_are_registered_and_on_by_default():
    from bot.domain.agent.tools.registry import CHANNEL_AWARE_TOOLS, DEFAULT_ENABLED_TOOLS

    for key in ("list_pages", "read_page"):
        assert key in DEFAULT_ENABLED_TOOLS
        assert key in CHANNEL_AWARE_TOOLS


def test_backfill_ships_the_new_tools_to_existing_channels():
    """Default-on tools do not reach already-registered channels on their own."""
    from bot.app.redis.migrations.backfill_agent_tools import DEFAULT_TOOLS_TO_ADD

    assert set(DEFAULT_TOOLS_TO_ADD) == {"list_pages", "read_page"}
