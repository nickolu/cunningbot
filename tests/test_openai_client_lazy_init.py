"""Regression tests for lazy OpenAI client construction.

bot/api/openai/chat_completions_client.py used to build AsyncOpenAI() at module
import time. Any module that imported it transitively — including
breaking_news_service, which the RSS collector imports purely for its keyword
matcher — therefore raised OpenAIError inside a container without
OPENAI_API_KEY. In the collector that import sits in the per-feed loop, so every
feed with new items was abandoned before its articles were queued or marked
seen, silently stopping all RSS summaries.
"""
import subprocess
import sys
import textwrap


def _run_without_api_key(source: str) -> subprocess.CompletedProcess:
    """Run source in a subprocess with OPENAI_API_KEY stripped from the env."""
    program = textwrap.dedent(
        """
        import os
        os.environ.pop("OPENAI_API_KEY", None)
        # Stop python-dotenv from putting the key back from a local .env
        os.environ["OPENAI_API_KEY"] = ""
        """
    ) + textwrap.dedent(source)
    return subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
    )


def test_client_module_imports_without_api_key() -> None:
    result = _run_without_api_key(
        """
        import bot.api.openai.chat_completions_client  # noqa: F401
        print("OK")
        """
    )
    assert "OK" in result.stdout, result.stderr


def test_breaking_news_service_imports_without_api_key() -> None:
    """The exact import that broke the RSS collector in production."""
    result = _run_without_api_key(
        """
        from bot.domain.news.breaking_news_service import matches_breaking_news_topics
        entry = {"title": "Fed raises rates", "description": ""}
        assert matches_breaking_news_topics(entry, ["fed"]) == "fed"
        print("OK")
        """
    )
    assert "OK" in result.stdout, result.stderr


def test_rss_feed_poster_imports_without_api_key() -> None:
    result = _run_without_api_key(
        """
        import bot.app.tasks.rss_feed_poster  # noqa: F401
        print("OK")
        """
    )
    assert "OK" in result.stdout, result.stderr
