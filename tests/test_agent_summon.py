"""Tests for name-based summoning of the channel agent."""

import pytest

from bot.domain.agent.summon import build_summon_pattern, is_summoned_by_name


@pytest.fixture
def pattern():
    return build_summon_pattern(["CunningBot"])


@pytest.mark.parametrize(
    "content",
    [
        "cunningbot what's the weather in 92101",
        "CunningBot, roll 2d6",
        "hey cunning bot can you make a picture",
        "ASK CUNNINGBOT ABOUT IT",
        "cunningbot's take on this?",
        "so... cunningbot?",
    ],
)
def test_summoned_by_name(pattern, content):
    assert is_summoned_by_name(content, pattern)


@pytest.mark.parametrize(
    "content",
    [
        "that bot is broken again",
        "the agent should really do this",
        "robots are taking over",
        "uncunningbot is not a word",
        "cunningbotany is a field",
        "",
    ],
)
def test_not_summoned(pattern, content):
    assert not is_summoned_by_name(content, pattern)


def test_guild_nickname_is_matched():
    pattern = build_summon_pattern(["CunningBot", "Dadbot"])
    assert is_summoned_by_name("dadbot help me out", pattern)


def test_no_names_yields_no_pattern_but_aliases_still_match():
    # Aliases are always included, so a pattern exists even with no names.
    pattern = build_summon_pattern([])
    assert is_summoned_by_name("cunningbot hello", pattern)
    assert not is_summoned_by_name("hello", pattern)


def test_missing_pattern_never_summons():
    assert not is_summoned_by_name("cunningbot hello", None)
