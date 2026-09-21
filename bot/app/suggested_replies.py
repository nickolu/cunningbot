"""Suggested-reply buttons: posting them under an agent reply, and clicks.

The agent offers options with the `suggest_replies` tool; every agent entry
point opens `collect_suggestions()` around its run and hands the result to
`send_agent_reply`, which puts the buttons on the reply's last message.

How a set of buttons behaves:

* **Anyone in the channel can click.** It's a group chat.
* **One click ends the set.** The first click claims it in Redis
  (`SuggestionRedisStore.claim`); the buttons turn grey with the choice
  highlighted, and later clicks are told the suggestions have expired.
* **The next agent reply expires them**, with or without buttons of its own:
  the record is replaced and the old message's buttons are removed.
* **A click is posted as a visible message** ("**Nick** chose: *...*") before
  the agent runs. The agent builds context from channel history, and a bare
  interaction never appears there, so without it later replies couldn't see
  what was chosen. For the run itself the choice is given as the clicker's
  own message.
* **A click waits for the channel's lock** rather than being dropped the way a
  mention is while the agent is busy. Someone pressed a button; they get an
  answer.

The buttons are a `DynamicItem`, matched by the custom_id pattern that
`register_suggestion_buttons` installs at startup, so clicks on messages sent
before a restart still arrive without re-attaching views per message. Whether a
set is still live is decided by the nonce in Redis, not by the view.

Lives outside bot/app/commands/ for the same reason as agent_runtime.py: an
extension module is re-executed on load, and the listener, /bot, and the click
handler must share one copy of this.
"""

import re
import uuid
from typing import Any, Awaitable, Callable, List, Optional

import discord

from bot.api.openai.utils import sanitize_name
from bot.app.agent_runtime import (
    UNREGISTERED_AGENT_CONFIG,
    fetch_agent_history,
    get_agent_channel_lock,
    get_channel_agent_config,
)
from bot.app.redis.agent_store import AgentRedisStore
from bot.app.redis.suggestion_store import SuggestionRedisStore
from bot.app.utils.logger import get_logger
from bot.domain.agent.agent_service import run_agent
from bot.domain.agent.suggestions import collect_suggestions
from bot.utils import split_message

logger = get_logger()

EXPIRED_MESSAGE = "Those suggestions have expired. Mention me to keep going."
PAUSED_MESSAGE = "I'm paused in this channel."
ERROR_MESSAGE = "Sorry, I ran into an error handling that."

# The choice is echoed into the channel; it must not ping anyone.
NO_MENTIONS = discord.AllowedMentions.none()

# Sends one chunk of a reply, with the buttons on the last one. Returns the
# sent message, whose id is needed to take the buttons off later.
SendChunk = Callable[[str, Optional[discord.ui.View]], Awaitable[Any]]


def new_nonce() -> str:
    return uuid.uuid4().hex[:12]


class SuggestionButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"suggest:(?P<nonce>[0-9a-f]{12}):(?P<index>[0-9])",
):
    """One suggested reply. The custom_id carries the set's nonce and position."""

    def __init__(self, nonce: str, index: int, label: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.secondary,
                custom_id=f"suggest:{nonce}:{index}",
            )
        )
        self.nonce = nonce
        self.index = index

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: discord.ui.Button, match: "re.Match[str]"
    ) -> "SuggestionButton":
        return cls(match["nonce"], int(match["index"]), item.label or "")

    async def callback(self, interaction: discord.Interaction) -> None:
        await handle_click(interaction, self.nonce, self.index)


def suggestions_view(nonce: str, options: List[str]) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for index, option in enumerate(options):
        view.add_item(SuggestionButton(nonce, index, option))
    return view


def chosen_view(nonce: str, options: List[str], chosen: int) -> discord.ui.View:
    """The set after a click: every button disabled, the choice highlighted."""
    view = discord.ui.View(timeout=None)
    for index, option in enumerate(options):
        view.add_item(discord.ui.Button(
            label=option,
            style=discord.ButtonStyle.primary if index == chosen else discord.ButtonStyle.secondary,
            custom_id=f"suggest-done:{nonce}:{index}",
            disabled=True,
        ))
    # Nothing on it can be clicked; a finished view isn't kept in the view store.
    view.stop()
    return view


def register_suggestion_buttons(client: discord.Client) -> None:
    """Route clicks on any suggestion button, including ones sent before a restart."""
    client.add_dynamic_items(SuggestionButton)


async def _remove_buttons(channel: Any, message_id: Optional[int]) -> None:
    if not message_id:
        return
    try:
        await channel.get_partial_message(message_id).edit(view=None)
    except discord.HTTPException as e:
        # Deleted, or too old to edit. The Redis record is what makes them dead.
        logger.info({"event": "suggestions_remove_failed", "message_id": message_id, "error": str(e)})


async def send_agent_reply(
    channel: Any,
    guild_id: Any,
    text: Optional[str],
    suggestions: List[str],
    send: Optional[SendChunk] = None,
) -> None:
    """Post an agent reply, with any suggested replies as buttons on its end.

    Posting a reply expires the channel's previous buttons, whether or not
    this one has buttons of its own. An empty reply posts nothing and leaves
    the previous buttons alone. Buttons are best effort: if Redis fails, the
    reply is still posted, without them.
    """
    chunks = split_message(text) if text and text.strip() else []
    if not chunks:
        return

    if send is None:
        async def send(chunk: str, view: Optional[discord.ui.View]) -> Any:
            if view is None:
                return await channel.send(chunk)
            return await channel.send(chunk, view=view)

    store: Optional[SuggestionRedisStore] = None
    previous = None
    try:
        store = SuggestionRedisStore()
        previous = await store.get(guild_id, channel.id)
    except Exception as e:
        logger.error(f"Suggested replies unavailable in channel {channel.id}: {e}")
        store = None

    nonce = new_nonce() if suggestions and store is not None else None
    last = None
    for i, chunk in enumerate(chunks):
        is_last = i == len(chunks) - 1
        view = suggestions_view(nonce, suggestions) if nonce and is_last else None
        last = await send(chunk, view)

    if store is None:
        return
    try:
        if nonce and last is not None:
            await store.save(guild_id, channel.id, nonce, last.id, suggestions)
        elif previous is not None:
            await store.clear(guild_id, channel.id)
    except Exception as e:
        logger.error(f"Failed to record suggested replies in channel {channel.id}: {e}")
    if previous is not None:
        await _remove_buttons(channel, previous.get("message_id"))


async def handle_click(interaction: discord.Interaction, nonce: str, index: int) -> None:
    """A suggestion was clicked: claim the set, echo the choice, run the agent."""
    channel = interaction.channel
    guild = interaction.guild
    if guild is None or not isinstance(channel, (discord.TextChannel, discord.Thread)):
        return

    config = await get_channel_agent_config(AgentRedisStore(), guild.id, channel)
    if config is None:
        config = UNREGISTERED_AGENT_CONFIG
    elif not config.get("enabled", False):
        # Paused silences the bot even when summoned; a button is no different.
        await interaction.response.send_message(PAUSED_MESSAGE, ephemeral=True)
        return

    options = await SuggestionRedisStore().claim(guild.id, channel.id, nonce)
    if options is None or not 0 <= index < len(options):
        # Someone else got there first, or a newer reply replaced these.
        await interaction.response.send_message(EXPIRED_MESSAGE, ephemeral=True)
        return

    choice = options[index]
    user = interaction.user
    name = user.display_name
    # Acknowledge inside Discord's 3 seconds by greying the buttons out.
    await interaction.response.edit_message(view=chosen_view(nonce, options, index))
    echo = await channel.send(
        f"**{discord.utils.escape_markdown(name)}** chose: "
        f"*{discord.utils.escape_markdown(choice)}*",
        allowed_mentions=NO_MENTIONS,
    )
    logger.info({
        "event": "suggestion_clicked",
        "guild": str(guild.id),
        "channel": str(channel.id),
        "index": index,
    })

    async with get_agent_channel_lock(channel.id):
        try:
            async with channel.typing():
                history = await fetch_agent_history(
                    channel, config.get("context_window", 30), exclude_ids={echo.id}
                )
                # The echo is the bot's own message; for this run the choice is
                # what the clicker said.
                history.append({"role": "user", "content": choice, "name": sanitize_name(name)})
                with collect_suggestions() as suggestions:
                    response = await run_agent(
                        channel=channel,
                        history=history,
                        agent_config=config,
                        guild_id=guild.id,
                        user=user,
                    )
            await send_agent_reply(channel, guild.id, response, suggestions)
        except Exception as e:
            logger.error(f"Suggestion click failed in channel {channel.id}: {e}", exc_info=True)
            await channel.send(ERROR_MESSAGE)
