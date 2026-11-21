"""on_message listener that drives the channel agent.

This cog listens to every message in the guild.  The bot answers when it
is addressed; otherwise it stays out of the conversation.  When it does
answer, it fetches recent history, runs the agent service's tool-calling
loop, and sends the response back to the channel.

An explicit summon — an @mention (of the bot's user or of the managed role
Discord creates for it), a reply to the bot, or the bot's name in the
message text — always gets a response, in every channel: registered,
unregistered, or paused.  Channels with no registration use default
settings.  The one way to get unprompted replies is a registered channel
whose response mode is "always"; pausing such a channel turns those off
while summons keep working.

There used to be a "smart" mode where a classifier model guessed whether an
unaddressed message was meant for the bot.  It guessed wrong often enough to
annoy people, so it is gone: a stored "smart" mode now behaves as "strict".

Threads (including forum posts) are handled too.  A thread uses its own
registration if it has one, otherwise its parent channel's, otherwise the
unregistered defaults.  So a paused parent also silences its threads, and
cooldown, rate limit and the concurrency lock are tracked per thread.

Response modes:
- "strict" (default): Only responds when explicitly summoned.
- "always": Responds to every message. Cooldown still applies, and pausing
  the channel suspends it.

Guard rails:
- Ignores all bot messages (prevents loops).
- Per-channel cooldown (configurable, default 5 s) — unless explicitly summoned.
- Per-channel rate limit (configurable, default 10 responses / minute).
- Runs through the existing TaskQueue so agent work doesn't starve slash commands.
"""

import asyncio
import time
from collections import defaultdict
from typing import Any, Dict, Optional, Pattern

import discord
from discord.ext import commands

from bot.app.agent_runtime import (
    AGENT_CHANNEL_LOCKS,
    UNREGISTERED_AGENT_CONFIG,
    fetch_agent_history,
    get_channel_agent_config,
)
from bot.app.redis.agent_store import AgentRedisStore
from bot.app.suggested_replies import send_agent_reply
from bot.domain.agent.agent_service import run_agent
from bot.domain.agent.suggestions import collect_suggestions
from bot.domain.agent.summon import build_summon_pattern, is_summoned_by_name
from bot.app.utils.logger import get_logger

logger = get_logger()


class AgentListenerCog(commands.Cog):
    """Listens for messages in agent-registered channels."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._store: Optional[AgentRedisStore] = None

        # In-memory cooldown / rate tracking (resets on bot restart, which is fine)
        # channel_id -> last response timestamp
        self._last_response: Dict[int, float] = {}
        # channel_id -> list of response timestamps in the current minute window
        self._response_timestamps: Dict[int, list] = defaultdict(list)
        # channel_id -> asyncio.Lock to prevent concurrent agent runs per channel.
        # Shared with the /bot command so the two can't overlap in a channel.
        self._channel_locks: Dict[int, asyncio.Lock] = AGENT_CHANNEL_LOCKS
        # guild_id -> compiled pattern of the names the bot answers to there
        self._summon_patterns: Dict[int, Optional[Pattern]] = {}

    @property
    def store(self) -> AgentRedisStore:
        if self._store is None:
            self._store = AgentRedisStore()
        return self._store

    def _check_cooldown(self, channel_id: int, cooldown_seconds: int) -> bool:
        """Return True if the channel is still in cooldown."""
        last = self._last_response.get(channel_id, 0)
        return (time.time() - last) < cooldown_seconds

    def _check_rate_limit(self, channel_id: int, max_per_minute: int) -> bool:
        """Return True if the channel has hit its per-minute rate limit."""
        now = time.time()
        timestamps = self._response_timestamps[channel_id]
        # Prune old entries
        self._response_timestamps[channel_id] = [
            t for t in timestamps if now - t < 60
        ]
        return len(self._response_timestamps[channel_id]) >= max_per_minute

    def _record_response(self, channel_id: int) -> None:
        """Record that we just responded in this channel."""
        now = time.time()
        self._last_response[channel_id] = now
        self._response_timestamps[channel_id].append(now)

    def _is_reply_to_bot(self, message: discord.Message) -> bool:
        """Check if the message is a direct reply to a bot message."""
        if message.reference and message.reference.resolved:
            ref = message.reference.resolved
            if isinstance(ref, discord.Message) and ref.author.bot:
                return True
        return False

    def _summon_pattern(self, guild: discord.Guild) -> Optional[Pattern]:
        """Compiled name pattern for a guild, built once per guild."""
        if guild.id not in self._summon_patterns:
            names = []
            if self.bot.user is not None:
                names.append(self.bot.user.name)
            if guild.me is not None:
                names.append(guild.me.display_name)
            self._summon_patterns[guild.id] = build_summon_pattern(names)
        return self._summon_patterns[guild.id]

    def _mentions_bot_role(self, message: discord.Message) -> bool:
        """True if the message mentions the managed role Discord made for this bot.

        Picking the bot from the @ menu can insert ``<@&role_id>`` for its
        same-named integration role instead of a user mention.  Only that
        role counts -- other roles the bot happens to hold do not.
        """
        if self.bot.user is None:
            return False
        for role in message.role_mentions:
            tags = role.tags
            if tags is not None and tags.bot_id == self.bot.user.id:
                return True
        return False

    def _is_summoned(self, message: discord.Message) -> bool:
        """True if the message addresses the bot directly.

        A mention (of the bot or its managed role), a reply to something the
        bot said, or the bot's name in the text all count.
        """
        if self.bot.user in message.mentions:
            return True
        if self._mentions_bot_role(message):
            return True
        if self._is_reply_to_bot(message):
            return True
        return is_summoned_by_name(
            message.content, self._summon_pattern(message.guild)
        )

    @staticmethod
    def _should_respond(config: Dict, summoned: bool) -> bool:
        """Addressed directly wins everywhere; "always" mode answers the rest.

        Any other stored mode -- "strict", or the retired "smart" -- means
        summons only.
        """
        if summoned:
            return True
        return config.get("response_mode", "strict") == "always" and bool(
            config.get("enabled", False)
        )

    async def _get_channel_config(
        self, guild_id: str, channel: discord.abc.GuildChannel
    ) -> Optional[Dict[str, Any]]:
        """Registration for the channel; a thread falls back to its parent's."""
        return await get_channel_agent_config(self.store, guild_id, channel)

    @commands.Cog.listener("on_message")
    async def on_message(self, message: discord.Message) -> None:
        # 1. Ignore bots (including ourselves)
        if message.author.bot:
            return

        # 2. Only handle guild text channels and threads (incl. forum posts)
        if not isinstance(message.channel, (discord.TextChannel, discord.Thread)):
            return

        guild_id = str(message.guild.id)
        channel_id = str(message.channel.id)

        # 3. Is the bot being addressed directly? (mention, reply, or by name)
        summoned = self._is_summoned(message)

        # 4. Look up the channel's agent (fast Redis lookup)
        config = await self._get_channel_config(guild_id, message.channel)
        if config is None:
            # Unregistered channel — answer only when summoned by name/mention.
            if not summoned:
                return
            config = UNREGISTERED_AGENT_CONFIG
            logger.info({
                "event": "agent_summoned_unregistered",
                "guild": guild_id,
                "channel": channel_id,
            })
        elif not config.get("enabled", False) and not summoned:
            # Paused turns off unprompted replies; a summon still gets through.
            return

        # 5. Cooldown check (skipped when the bot is addressed directly)
        cooldown = config.get("cooldown_seconds", 5)
        if not summoned and self._check_cooldown(message.channel.id, cooldown):
            return

        # 6. Rate limit check
        max_rpm = config.get("max_responses_per_minute", 10)
        if self._check_rate_limit(message.channel.id, max_rpm):
            logger.warning(f"Agent rate limit hit in channel {channel_id}")
            return

        # 7. Addressed, or an "always" channel that isn't paused?
        if not self._should_respond(config, summoned):
            return

        # 8. Acquire per-channel lock so we don't run multiple agents concurrently
        lock = self._channel_locks[message.channel.id]
        if lock.locked():
            # Agent is already processing in this channel; skip
            return

        async with lock:
            await self._handle_agent_response(message, config)

    async def _handle_agent_response(
        self, message: discord.Message, config: Dict
    ) -> None:
        """Fetch history, run the agent, and send the response."""
        channel = message.channel
        context_window = config.get("context_window", 30)

        try:
            # Show typing indicator while processing
            async with channel.typing():
                # Fetch channel history
                history = await fetch_agent_history(channel, context_window)

                # Run the agent loop
                with collect_suggestions() as suggestions:
                    response = await run_agent(
                        channel=channel,
                        history=history,
                        agent_config=config,
                        guild_id=message.guild.id,
                        # Tools that act for a person, or check whether they're
                        # allowed to, need to know who asked.
                        user=message.author,
                    )

            await send_agent_reply(channel, message.guild.id, response, suggestions)

            # Record this response for cooldown/rate tracking
            self._record_response(channel.id)

        except Exception as e:
            logger.error(f"Agent error in channel {channel.id}: {e}", exc_info=True)
            # Don't spam the channel with errors — just log


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AgentListenerCog(bot))
