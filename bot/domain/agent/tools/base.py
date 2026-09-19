"""The descriptor every agent tool module exports.

One `AgentTool` per module is the whole registration contract. `registry.py`
collects them and derives everything the rest of the bot used to hand-maintain
in parallel lists: the schema map, the executor map, the channel-aware set, and
the default enabled-tool list.
"""

from dataclasses import dataclass
from typing import Callable, Coroutine


@dataclass(frozen=True)
class AgentTool:
    """A single tool the channel agent can call.

    Two names matter and they are not the same thing:

    - ``config_key`` is what a channel's stored config lists ("image"), and what
      ``/agent`` shows in its tool picker.
    - the function name inside the schema is what the model emits in a tool call
      ("generate_image"), and what the executor is dispatched by.

    They used to live in separate dicts with nothing tying them together. Here
    the schema is the single source of the function name.

    ``channel_aware`` and ``user_aware`` say what the executor is given besides
    the model's arguments, in this order::

        execute(arguments)                    # neither
        execute(arguments, channel)           # channel_aware
        execute(arguments, channel, user)     # both
        execute(arguments, user)              # user_aware only

    ``user`` is the Discord user whose message caused the run, which a tool
    needs when it acts on someone's behalf or has to decide whether they are
    allowed to (``scan_channel_history``). It is None when the caller could not
    identify one, so a tool that requires it has to handle that.
    """

    config_key: str
    schema: dict
    executor: Callable[..., Coroutine]
    channel_aware: bool = False
    user_aware: bool = False
    default_enabled: bool = True

    @property
    def function_name(self) -> str:
        return str(self.schema["function"]["name"])
