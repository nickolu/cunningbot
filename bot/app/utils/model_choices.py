"""Discord model pickers, built from the model catalog.

`/chat` and `/agent` used to carry hand-written `app_commands.Choice` lists --
three of them, which disagreed with each other and with what the API actually
permitted. They are generated from `bot.domain.llm.models` now, so a model added
to the catalog shows up everywhere it should.

Lives under `app/` rather than `domain/` because `app_commands` is discord.py.
"""

from typing import List

from discord import app_commands

from bot.domain.llm.models import selectable_models

# Discord rejects a command option with more than 25 choices.
MAX_CHOICES = 25


def model_choices(default: str) -> List[app_commands.Choice]:
    """Choices for a model option, marking `default` as the default."""
    models = selectable_models()
    if len(models) > MAX_CHOICES:
        raise RuntimeError(
            "%d selectable models exceeds Discord's limit of %d; deprecate some"
            % (len(models), MAX_CHOICES)
        )
    return [
        app_commands.Choice(name=m.choice_label(is_default=m.id == default), value=m.id)
        for m in models
    ]
