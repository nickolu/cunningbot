"""The catalog of chat models the bot permits.

This used to live in five places that drifted apart: a `Literal`, an
eighteen-branch if/elif returning request kwargs, a vendor dict, and three
hand-maintained Discord choice lists (`/chat` plus `/agent` twice). Adding a
model meant finding all five; in practice `/chat` got new models and the
channel agent did not.

Everything now derives from `MODELS`. To add a model: add a row, add its id to
`PermittedModelType`, then run `python3 scripts/check_models.py` -- membership
in the account's /v1/models listing is NOT enough. The `-pro` and `-codex`
variants are listed but only serve /v1/responses, which this bot does not use;
two of them shipped in the `/chat` picker as guaranteed errors before that
check existed.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Tuple

# Which token-cap parameter a model's API accepts. The older completion models
# take `max_tokens`; everything from 4o onward rejects it and wants
# `max_completion_tokens`. Sending the wrong one is a 400.
MAX_TOKENS = "max_tokens"
MAX_COMPLETION_TOKENS = "max_completion_tokens"


@dataclass(frozen=True)
class ChatModel:
    """One selectable chat model."""

    id: str
    token_param: str
    token_limit: int
    vendor: str = "openai"
    #: Short parenthetical shown in the Discord picker, e.g. "smartest".
    hint: Optional[str] = None
    #: Kept as a valid value so stored configs keep working, but hidden from
    #: the pickers. Discord caps a command at 25 choices, so shelf space is
    #: finite and superseded models have to give it up.
    deprecated: bool = False

    @property
    def request_arguments(self) -> Dict[str, Any]:
        """The kwargs this model needs on a chat-completions call."""
        return {"model": self.id, self.token_param: self.token_limit}

    def choice_label(self, is_default: bool = False) -> str:
        hints = []
        if is_default:
            hints.append("default")
        if self.hint:
            hints.append(self.hint)
        return "%s (%s)" % (self.id, ", ".join(hints)) if hints else self.id


# Order is user-visible: it is the order of the Discord pickers. Best-first.
MODELS: Tuple[ChatModel, ...] = (
    ChatModel("gpt-5.5", MAX_COMPLETION_TOKENS, 64000),
    ChatModel("gpt-5.4", MAX_COMPLETION_TOKENS, 10000),
    ChatModel("gpt-5.4-mini", MAX_COMPLETION_TOKENS, 10000, hint="cheaper"),
    ChatModel("gpt-5.4-nano", MAX_COMPLETION_TOKENS, 10000, hint="cheapest"),
    ChatModel("gpt-5.2", MAX_COMPLETION_TOKENS, 64000),
    ChatModel("gpt-5.1", MAX_COMPLETION_TOKENS, 10000),
    ChatModel("gpt-5", MAX_COMPLETION_TOKENS, 10000),
    ChatModel("gpt-5-mini", MAX_COMPLETION_TOKENS, 10000),
    ChatModel("o3", MAX_COMPLETION_TOKENS, 10000, hint="reasoning"),
    ChatModel("o4-mini", MAX_COMPLETION_TOKENS, 10000, hint="reasoning, cheaper"),
    ChatModel("gpt-4o", MAX_COMPLETION_TOKENS, 10000),
    ChatModel("gpt-4o-mini", MAX_COMPLETION_TOKENS, 10000),
    ChatModel("gpt-4.1", MAX_TOKENS, 10000),
    ChatModel("gpt-4.1-mini", MAX_TOKENS, 10000),
    ChatModel("gpt-4.1-nano", MAX_TOKENS, 10000),
    # Superseded but still functional. Accepted so stored channel configs keep
    # working, hidden from the pickers -- Discord caps one at 25 choices.
    ChatModel("gpt-4-turbo", MAX_TOKENS, 4096, deprecated=True),
    # 8192 is the *total* context, so a full-size completion cap 400s on any
    # real prompt. Halved to leave room for one.
    ChatModel("gpt-4", MAX_TOKENS, 4096, deprecated=True),
    ChatModel("gpt-3.5-turbo", MAX_TOKENS, 4096, deprecated=True),
)

# Must list exactly the ids in MODELS. `Literal` cannot be built from a runtime
# value, so this is written by hand and pinned by
# tests/test_model_registry.py::test_literal_matches_catalog.
PermittedModelType = Literal[
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.4-nano",
    "gpt-5.2",
    "gpt-5.1",
    "gpt-5",
    "gpt-5-mini",
    "o3",
    "o4-mini",
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4.1",
    "gpt-4.1-mini",
    "gpt-4.1-nano",
    "gpt-4-turbo",
    "gpt-4",
    "gpt-3.5-turbo",
]

MODELS_BY_ID: Dict[str, ChatModel] = {m.id: m for m in MODELS}

# Kept for the existing `ChatCompletionsClient.PERMITTED_MODELS` contract.
PERMITTED_MODELS: Dict[str, str] = {m.id: m.vendor for m in MODELS}

# --- Role defaults ---------------------------------------------------------
# Named rather than sprinkled as string literals, so "what does /chat default
# to" has one answer.

#: `/chat` with no model argument.
DEFAULT_CHAT_MODEL = "gpt-5.5"
#: A newly registered channel agent. Answers on every qualifying message, so
#: this trades some capability for cost.
DEFAULT_AGENT_MODEL = "gpt-5.4-mini"
#: Background summarization (RSS digests, weather blurbs). High volume, low
#: difficulty, never user-selected.
UTILITY_MODEL = "gpt-4o-mini"


def get_model(model_id: str) -> ChatModel:
    """Look up a model, raising the same ValueError the client used to raise."""
    try:
        return MODELS_BY_ID[model_id]
    except KeyError:
        raise ValueError("Unsupported or unknown model: %s" % model_id)


def selectable_models() -> List[ChatModel]:
    """Models offered in a Discord picker: everything not deprecated."""
    return [m for m in MODELS if not m.deprecated]


def request_arguments(model_id: str) -> Dict[str, Any]:
    """Chat-completions kwargs for a model id."""
    return get_model(model_id).request_arguments
