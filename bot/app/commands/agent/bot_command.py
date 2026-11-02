"""/bot — run the channel agent once, in any channel, without registering it.

The same agent a mention reaches: recent channel history as context, the
same tools, the same per-channel lock.  The reply quotes the prompt, since a
slash invocation leaves no visible message the way a mention does.
"""

from typing import Any, Dict, Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot.app.agent_runtime import (
    UNREGISTERED_AGENT_CONFIG,
    fetch_agent_history,
    get_agent_channel_lock,
)
from bot.app.redis.agent_store import AgentRedisStore
from bot.app.suggested_replies import send_agent_reply
from bot.app.utils.logger import get_logger
from bot.api.openai.utils import sanitize_name
from bot.domain.agent.agent_service import run_agent
from bot.domain.agent.suggestions import collect_suggestions

logger = get_logger()

BUSY_MESSAGE = "I'm already working on something in this channel. Try again in a moment."
UNSUPPORTED_CHANNEL_MESSAGE = "`/bot` only works in server text channels and threads."
QUOTE_PREVIEW_CHARS = 200

# A quoted prompt shouldn't be able to ping @everyone or a role on the bot's behalf.
REPLY_ALLOWED_MENTIONS = discord.AllowedMentions(everyone=False, roles=False, users=True)


def quote_prompt(author: str, prompt: str) -> str:
    """First line of the prompt as a Discord block quote, credited to its author."""
    lines = prompt.strip().splitlines() or [""]
    first = lines[0][:QUOTE_PREVIEW_CHARS]
    if len(lines) > 1 or len(lines[0]) > QUOTE_PREVIEW_CHARS:
        first += "…"
    return f"> **{author}:** {first}"


class BotCommandCog(commands.Cog):
    """The /bot one-shot agent command."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._store: Optional[AgentRedisStore] = None

    @property
    def store(self) -> AgentRedisStore:
        if self._store is None:
            self._store = AgentRedisStore()
        return self._store

    async def _select_config(self, guild_id: int, channel_id: int) -> Dict[str, Any]:
        """The agent config for a /bot run in this channel.

        A registered, enabled channel uses its own config (model, persona,
        tools) — /bot there should behave like mentioning the bot.  Anything
        else, including a paused channel, gets the unregistered defaults:
        pausing silences the bot's own judgement, but /bot is an explicit ask.
        """
        config = await self.store.get_agent_config(str(guild_id), str(channel_id))
        if config is not None and config.get("enabled", False):
            return config
        return UNREGISTERED_AGENT_CONFIG

    @app_commands.command(
        name="bot", description="Ask the AI agent something once, in any channel"
    )
    @app_commands.describe(prompt="What you want the bot to do or answer")
    async def ask(self, interaction: discord.Interaction, prompt: str) -> None:
        channel = interaction.channel
        if interaction.guild is None or not isinstance(
            channel, (discord.TextChannel, discord.Thread)
        ):
            await interaction.response.send_message(
                UNSUPPORTED_CHANNEL_MESSAGE, ephemeral=True
            )
            return

        lock = get_agent_channel_lock(channel.id)
        if lock.locked():
            await interaction.response.send_message(BUSY_MESSAGE, ephemeral=True)
            return

        # Taking a free lock doesn't yield, so nothing can grab it after the check.
        async with lock:
            await interaction.response.defer()
            author = interaction.user.display_name
            try:
                config = await self._select_config(interaction.guild.id, channel.id)
                history = await fetch_agent_history(
                    channel, config.get("context_window", 30)
                )
                # The slash invocation isn't in the channel history; add it last.
                history.append({
                    "role": "user",
                    "content": prompt,
                    "name": sanitize_name(author),
                })
                logger.info({
                    "event": "agent_bot_command",
                    "guild": str(interaction.guild.id),
                    "channel": str(channel.id),
                    "registered_config": config is not UNREGISTERED_AGENT_CONFIG,
                })
                # Tools that post rich output (images) send to `channel` directly.
                with collect_suggestions() as suggestions:
                    response = await run_agent(
                        channel=channel,
                        history=history,
                        agent_config=config,
                        guild_id=interaction.guild.id,
                        user=interaction.user,
                    )
            except Exception as e:
                logger.error(f"/bot failed in channel {channel.id}: {e}", exc_info=True)
                # Not ephemeral: the first followup replaces the public "thinking" message.
                await interaction.followup.send(
                    quote_prompt(author, prompt) + "\n\nSorry, I ran into an error handling that.",
                    allowed_mentions=REPLY_ALLOWED_MENTIONS,
                )
                return

            reply = quote_prompt(author, prompt)
            if response and response.strip():
                reply += "\n\n" + response

            async def send(chunk: str, view: Optional[discord.ui.View]) -> Any:
                if view is None:
                    return await interaction.followup.send(
                        chunk, allowed_mentions=REPLY_ALLOWED_MENTIONS
                    )
                return await interaction.followup.send(
                    chunk, allowed_mentions=REPLY_ALLOWED_MENTIONS, view=view
                )

            await send_agent_reply(
                channel, interaction.guild.id, reply, suggestions, send=send
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BotCommandCog(bot))
