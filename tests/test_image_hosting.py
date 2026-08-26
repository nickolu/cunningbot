"""Tests for stable image hosting."""

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from bot.domain.pages.page_ids import derive_guild_prefix, derive_page_id
from bot.domain.pages.image_service import MAX_IMAGE_BYTES, host_image_bytes


@pytest.fixture(autouse=True)
def _id_secret():
    with patch.dict(os.environ, {"PAGES_ID_SECRET": "unit-test-secret"}):
        yield


# --- Guild isolation of the storage prefix ---------------------------------

def test_prefix_differs_per_guild():
    assert derive_guild_prefix("111") != derive_guild_prefix("222")


def test_prefix_is_stable_and_well_formed():
    p = derive_guild_prefix("111")
    assert p == derive_guild_prefix("111")
    assert len(p) == 16 and all(c in "0123456789abcdef" for c in p)


def test_prefix_does_not_leak_the_guild_id():
    guild_id = "123456789012345678"
    assert guild_id not in derive_guild_prefix(guild_id)


def test_image_prefix_does_not_collide_with_a_page_id():
    """Image and page namespaces are derived from the same secret; keep them apart."""
    assert derive_guild_prefix("111") != derive_page_id("111", "images")


# --- Upload guards ----------------------------------------------------------

@pytest.mark.asyncio
async def test_rejects_oversized_image_without_calling_the_service():
    with patch("bot.api.pages.client.PagesClient") as client:
        with pytest.raises(RuntimeError, match="too large"):
            await host_image_bytes("111", b"x" * (MAX_IMAGE_BYTES + 1))
    client.assert_not_called()


@pytest.mark.asyncio
async def test_rejects_unsupported_content_type():
    with pytest.raises(RuntimeError, match="Unsupported image type"):
        await host_image_bytes("111", b"data", content_type="image/tiff")


@pytest.mark.asyncio
async def test_rejects_empty_image():
    with pytest.raises(RuntimeError, match="No image data"):
        await host_image_bytes("111", b"")


@pytest.mark.asyncio
async def test_upload_passes_the_derived_prefix_not_the_guild_id():
    fake = AsyncMock(return_value={"url": "https://x.blob.vercel-storage.com/img/a/b.png"})
    with patch("bot.domain.pages.image_service.PagesClient") as cls:
        cls.return_value = SimpleNamespace(upload_image=fake)
        url = await host_image_bytes("111", b"pngdata", filename="art")

    assert url.startswith("https://")
    kwargs = fake.await_args.kwargs
    assert kwargs["guild_prefix"] == derive_guild_prefix("111")
    assert kwargs["guild_id"] == "111"


# --- Agent tool -------------------------------------------------------------

def _channel(guild_id=987654321):
    return SimpleNamespace(guild=SimpleNamespace(id=guild_id, name="Test"))


@pytest.mark.asyncio
async def test_host_image_tool_returns_the_url():
    from bot.domain.agent.agent_tools import execute_host_image

    with patch(
        "bot.domain.pages.image_service.host_image_from_url",
        new=AsyncMock(return_value="https://x.blob.vercel-storage.com/img/a/b.png"),
    ):
        result = await execute_host_image({"image_url": "https://cdn.discordapp.com/x.png"}, _channel())

    assert "https://x.blob.vercel-storage.com/img/a/b.png" in result


@pytest.mark.asyncio
async def test_host_image_tool_reports_expired_link_gracefully():
    from bot.domain.agent.agent_tools import execute_host_image

    with patch(
        "bot.domain.pages.image_service.host_image_from_url",
        new=AsyncMock(side_effect=RuntimeError("That image link has expired or is no longer available.")),
    ):
        result = await execute_host_image({"image_url": "https://cdn.discordapp.com/old.png"}, _channel())

    assert "expired" in result.lower()
    assert not result.startswith("Traceback")


@pytest.mark.asyncio
async def test_host_image_tool_requires_a_url():
    from bot.domain.agent.agent_tools import execute_host_image
    assert "no image url" in (await execute_host_image({}, _channel())).lower()


def test_tool_registered_everywhere():
    from bot.app.redis.agent_store import DEFAULT_AGENT_CONFIG
    from bot.domain.agent.agent_service import AGENT_SYSTEM_PROMPT
    from bot.domain.agent.agent_tools import (
        CHANNEL_AWARE_TOOLS, TOOL_EXECUTORS, TOOL_SCHEMAS,
    )

    assert "host_image" in TOOL_SCHEMAS
    assert "host_image" in TOOL_EXECUTORS
    assert "host_image" in CHANNEL_AWARE_TOOLS
    assert "host_image" in DEFAULT_AGENT_CONFIG["tools"]
    assert "host_image" in AGENT_SYSTEM_PROMPT


# --- Publishing guard: bad image refs never reach a page --------------------

@pytest.mark.parametrize(
    "markdown,expected",
    [
        ("![art](attachment://generated_image)", "attachment"),
        ("![art](https://cdn.discordapp.com/attachments/1/2/x.png)", "discord"),
        ("![art](https://media.discordapp.net/attachments/1/2/x.png)", "discord"),
    ],
)
@pytest.mark.asyncio
async def test_publish_page_refuses_unpublishable_images(markdown, expected):
    from bot.domain.agent.agent_tools import execute_publish_page

    result = await execute_publish_page(
        {"title": "T", "markdown": markdown}, _channel()
    )
    assert "won't work on the web" in result
    assert expected in result.lower()
    assert "host_image" in result


@pytest.mark.asyncio
async def test_publish_page_allows_hosted_urls():
    from bot.domain.agent.agent_tools import execute_publish_page

    with patch(
        "bot.domain.pages.page_service.publish_page",
        new=AsyncMock(return_value="https://cunningbot-pages.vercel.app/p/x"),
    ):
        result = await execute_publish_page(
            {"title": "T", "markdown": "![art](https://abc.public.blob.vercel-storage.com/img/a/b.png)"},
            _channel(),
        )
    assert "cunningbot-pages.vercel.app/p/x" in result


@pytest.mark.asyncio
async def test_generate_image_returns_a_usable_url_not_an_attachment_scheme():
    """The original bug: no URL in the result, so the model invented one."""
    from bot.domain.agent.agent_tools import _send_image_and_describe

    class Msg:
        attachments = [SimpleNamespace(url="https://cdn.discordapp.com/a/b.png")]

    class Chan:
        guild = SimpleNamespace(id=1, name="g")
        async def send(self, *a, **kw):
            return Msg()

    out = await _send_image_and_describe(Chan(), b"png", "f.png", "Image generated.")
    assert "https://cdn.discordapp.com/a/b.png" in out
    assert "attachment://" not in out
    assert "do NOT put this on a page" in out


@pytest.mark.asyncio
async def test_generate_image_with_host_returns_permanent_url():
    from bot.domain.agent.agent_tools import _send_image_and_describe

    class Msg:
        attachments = [SimpleNamespace(url="https://cdn.discordapp.com/a/b.png")]

    class Chan:
        guild = SimpleNamespace(id=1, name="g")
        async def send(self, *a, **kw):
            return Msg()

    with patch(
        "bot.domain.pages.image_service.host_image_bytes",
        new=AsyncMock(return_value="https://abc.public.blob.vercel-storage.com/img/a/b.png"),
    ):
        out = await _send_image_and_describe(Chan(), b"png", "f.png", "Image generated.", host=True)

    assert "blob.vercel-storage.com" in out
    assert "safe to put on a page" in out
