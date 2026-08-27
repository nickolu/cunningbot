"""Contract tests for the chat model catalog.

The catalog replaced five hand-maintained lists. These tests pin the couplings
that can still silently rot -- chiefly `PermittedModelType`, which has to be
written by hand because `Literal` cannot be built from a runtime value.
"""

import pytest

from bot.domain.llm.models import (
    DEFAULT_AGENT_MODEL,
    DEFAULT_CHAT_MODEL,
    MAX_COMPLETION_TOKENS,
    MAX_TOKENS,
    MODELS,
    MODELS_BY_ID,
    PERMITTED_MODELS,
    PermittedModelType,
    UTILITY_MODEL,
    get_model,
    request_arguments,
    selectable_models,
)

ROLE_DEFAULTS = [DEFAULT_CHAT_MODEL, DEFAULT_AGENT_MODEL, UTILITY_MODEL]


def test_literal_matches_catalog():
    """The one thing that cannot be derived, so it has to be checked."""
    assert set(PermittedModelType.__args__) == {m.id for m in MODELS}


def test_ids_are_unique():
    assert len(MODELS_BY_ID) == len(MODELS)


@pytest.mark.parametrize("model", MODELS, ids=lambda m: m.id)
def test_every_model_declares_a_valid_token_param(model):
    assert model.token_param in (MAX_TOKENS, MAX_COMPLETION_TOKENS)
    assert model.token_limit > 0
    assert request_arguments(model.id) == {
        "model": model.id,
        model.token_param: model.token_limit,
    }


@pytest.mark.parametrize("model_id", ROLE_DEFAULTS)
def test_role_defaults_are_real_and_offered(model_id):
    """A default pointing at a deprecated or missing model is a startup bug."""
    model = get_model(model_id)
    assert not model.deprecated, "%s is a role default but hidden from pickers" % model_id


def test_unknown_model_raises():
    with pytest.raises(ValueError):
        get_model("gpt-nope")


def test_permitted_models_covers_the_catalog():
    assert set(PERMITTED_MODELS) == {m.id for m in MODELS}
    assert set(PERMITTED_MODELS.values()) == {"openai"}


def test_deprecated_models_stay_valid_but_hidden():
    """Stored channel configs may still name one; they must not break."""
    deprecated = [m for m in MODELS if m.deprecated]
    assert deprecated, "expected some superseded models"
    for model in deprecated:
        assert model.id in PERMITTED_MODELS
        assert model not in selectable_models()


def test_pickers_fit_discord_and_mark_one_default():
    from bot.app.utils.model_choices import MAX_CHOICES, model_choices

    choices = model_choices(DEFAULT_CHAT_MODEL)
    assert len(choices) == len(selectable_models()) <= MAX_CHOICES
    defaults = [c for c in choices if "(default" in c.name]
    assert len(defaults) == 1
    assert defaults[0].value == DEFAULT_CHAT_MODEL


def test_client_reexports_stay_wired():
    """chat.py and chat_service.py import these names from the client."""
    from bot.api.openai.chat_completions_client import (
        ChatCompletionsClient,
        transform_arguments_for_model,
    )

    assert transform_arguments_for_model("gpt-4o") == request_arguments("gpt-4o")
    assert ChatCompletionsClient.PERMITTED_MODELS == PERMITTED_MODELS
