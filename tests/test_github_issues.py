"""Tests for the create_github_issue tool and the service behind it."""

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from bot.api.github.client import GitHubClient, GitHubConfigError
from bot.domain.agent.tools.create_github_issue import execute_create_github_issue


class FakeChannel:
    def __init__(self, guild_id=42, guild_name="Test Server", name="general"):
        self.guild = SimpleNamespace(id=guild_id, name=guild_name)
        self.name = name
        self.jump_url = "https://discord.com/channels/%s/1" % guild_id


class FakeStore:
    """Stands in for GitHubRedisStore, which connects to Redis in __init__."""

    allow = True

    def __init__(self):
        self.claimed = 0

    async def claim_issue_slot(self, guild_id):
        self.claimed += 1
        return FakeStore.allow


@pytest.fixture
def store(monkeypatch):
    FakeStore.allow = True
    monkeypatch.setattr("bot.domain.github.issue_service.GitHubRedisStore", FakeStore)
    return FakeStore


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    monkeypatch.setenv("GITHUB_ISSUE_REPO", "owner/repo")


# --- client configuration -------------------------------------------------

def test_client_requires_a_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_ISSUE_REPO", "owner/repo")
    with pytest.raises(GitHubConfigError):
        GitHubClient()


@pytest.mark.parametrize("repo", ["", "not-a-repo", "owner/repo/extra", "/repo", "owner/"])
def test_client_rejects_a_malformed_repo(monkeypatch, repo):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
    monkeypatch.setenv("GITHUB_ISSUE_REPO", repo)
    with pytest.raises(GitHubConfigError):
        GitHubClient()


def test_repo_comes_from_env_not_from_arguments(configured):
    """The model never gets to choose where an issue lands."""
    from bot.domain.agent.tools.create_github_issue import SCHEMA

    assert GitHubClient().repo == "owner/repo"
    accepted = set(SCHEMA["function"]["parameters"]["properties"])
    assert accepted == {"title", "body"}, "the tool must not take a repo argument"


# --- executor -------------------------------------------------------------

@pytest.mark.asyncio
async def test_reports_when_not_configured(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    result = await execute_create_github_issue(
        {"title": "A real title here", "body": "Details."}, FakeChannel()
    )
    assert "not available" in result
    assert "GITHUB_TOKEN" in result


@pytest.mark.asyncio
async def test_rejects_a_thin_title(configured):
    result = await execute_create_github_issue({"title": "bug", "body": "Details."}, FakeChannel())
    assert "did not file" in result.lower()


@pytest.mark.asyncio
async def test_rejects_an_empty_body(configured):
    result = await execute_create_github_issue(
        {"title": "Something went wrong here", "body": "  "}, FakeChannel()
    )
    assert "did not file" in result.lower()


@pytest.mark.asyncio
async def test_refuses_outside_a_guild(configured):
    channel = FakeChannel()
    channel.guild = None
    result = await execute_create_github_issue(
        {"title": "A real title here", "body": "Details."}, channel
    )
    assert "inside a server" in result


@pytest.mark.asyncio
async def test_files_and_returns_the_url(configured, store):
    with patch("bot.api.github.client.GitHubClient.create_issue",
               new=AsyncMock(return_value={"html_url": "https://github.com/owner/repo/issues/7",
                                           "number": 7})) as create:
        result = await execute_create_github_issue(
            {"title": "read_channel cannot paginate", "body": "It caps at 50."}, FakeChannel()
        )

    assert "https://github.com/owner/repo/issues/7" in result
    body = create.await_args.kwargs["body"]
    assert "It caps at 50." in body
    assert "#general" in body and "Test Server" in body, "provenance footer missing"


@pytest.mark.asyncio
async def test_rate_limit_blocks_and_explains(configured, store):
    store.allow = False
    with patch("bot.api.github.client.GitHubClient.create_issue", new=AsyncMock()) as create:
        result = await execute_create_github_issue(
            {"title": "A real title here", "body": "Details."}, FakeChannel()
        )

    assert "past hour" in result
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_github_refusal_becomes_a_sentence(configured, store):
    with patch("bot.api.github.client.GitHubClient.create_issue",
               new=AsyncMock(side_effect=RuntimeError("Issues are disabled for owner/repo."))):
        result = await execute_create_github_issue(
            {"title": "A real title here", "body": "Details."}, FakeChannel()
        )

    assert result.startswith("Could not file the issue:")
    assert "disabled" in result


# --- registration ---------------------------------------------------------

def test_tool_is_registered_but_opt_in():
    from bot.domain.agent.tools.registry import (
        CHANNEL_AWARE_TOOLS,
        DEFAULT_ENABLED_TOOLS,
        TOOL_SCHEMAS,
    )

    assert "create_github_issue" in TOOL_SCHEMAS
    assert "create_github_issue" in CHANNEL_AWARE_TOOLS
    assert "create_github_issue" not in DEFAULT_ENABLED_TOOLS, (
        "writes to a public repo; must not be on by default"
    )


def test_error_messages_never_leak_the_token(configured):
    client = GitHubClient()
    for status in (401, 403, 404, 410, 422, 500):
        assert "ghp_test" not in client._describe_error(status, {"message": "nope"})


# --- reaching it from Discord --------------------------------------------

@pytest.mark.asyncio
async def test_agent_tool_command_enables_an_opt_in_tool():
    """`/agent tool` is the only way an opt-in tool can be switched on.

    `/agent configure` has no tools option, and re-registering a channel gives
    it the defaults -- which deliberately exclude this one.
    """
    from unittest.mock import MagicMock

    from bot.app.commands.agent.agent import AgentCog

    cog = AgentCog(MagicMock())
    cog._store = MagicMock()
    cog.store.get_agent_config = AsyncMock(return_value={"tools": ["dice"]})
    cog.store.update_agent_config = AsyncMock(return_value=True)

    interaction = MagicMock()
    interaction.guild_id = 42
    interaction.channel_id = 7
    interaction.response.send_message = AsyncMock()

    await cog.tool.callback(cog, interaction, "create_github_issue", "enable")

    cog.store.update_agent_config.assert_awaited_once()
    updates = cog.store.update_agent_config.await_args[0][2]
    assert updates["tools"] == ["dice", "create_github_issue"]


@pytest.mark.asyncio
async def test_agent_tool_command_disables_and_is_idempotent():
    from unittest.mock import MagicMock

    from bot.app.commands.agent.agent import AgentCog

    cog = AgentCog(MagicMock())
    cog._store = MagicMock()
    cog.store.get_agent_config = AsyncMock(return_value={"tools": ["dice"]})
    cog.store.update_agent_config = AsyncMock(return_value=True)

    interaction = MagicMock()
    interaction.guild_id = 42
    interaction.channel_id = 7
    interaction.response.send_message = AsyncMock()

    await cog.tool.callback(cog, interaction, "dice", "disable")
    assert cog.store.update_agent_config.await_args[0][2]["tools"] == []

    cog.store.update_agent_config.reset_mock()
    await cog.tool.callback(cog, interaction, "web_search", "disable")
    cog.store.update_agent_config.assert_not_awaited()
