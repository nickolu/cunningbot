"""Regression tests for the RSS summary poster's cleanup paths.

2420d5f moved the RSS system to Redis but left rss_summary_poster calling the
pending_news.py JSON helpers. pending_news.json has been `{}` since 2026-01-23,
so cleanup read an empty dict, logged "Cleared 0 pending articles" and never
touched the real Redis lists. A channel deleted in Discord therefore kept its
pending articles forever — one feed reached 907,737 entries (792 MB) before
this was caught.
"""
from unittest.mock import AsyncMock, patch

import pytest

# rss_summary_poster calls load_dotenv() at import time. pytest imports every
# test module during collection, so importing it plainly would load the
# developer's real .env into os.environ for the whole session — which changes
# how unrelated tests behave (it masks three image-command failures and cuts
# 45s of retry backoff). Neutralise it for the duration of the import.
with patch("dotenv.load_dotenv"):
    from bot.app.tasks.rss_summary_poster import _cleanup_inaccessible_channel


@pytest.mark.asyncio
async def test_cleanup_clears_pending_from_redis() -> None:
    store = AsyncMock()
    store.clear_pending.return_value = 7

    await _cleanup_inaccessible_channel(store, 844003671334977607, 1461503896219291861, "Channel deleted")

    store.clear_pending.assert_awaited_once_with("844003671334977607", 1461503896219291861)


@pytest.mark.asyncio
async def test_cleanup_swallows_store_errors() -> None:
    """A failing cleanup must not abort the run for other channels."""
    store = AsyncMock()
    store.clear_pending.side_effect = RuntimeError("redis down")

    await _cleanup_inaccessible_channel(store, 1, 2, "Channel deleted")

    store.clear_pending.assert_awaited_once()


def test_poster_no_longer_uses_the_json_pending_helpers() -> None:
    """The JSON module is migration-only; the poster must go through Redis."""
    from pathlib import Path

    source = Path("bot/app/tasks/rss_summary_poster.py").read_text(encoding="utf-8")
    assert "from bot.app.pending_news import" not in source
    assert "import bot.app.pending_news" not in source
