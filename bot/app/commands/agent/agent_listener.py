"""on_message listener that drives the channel agent.

This cog listens to every message in the guild and checks whether the
channel has a registered (and enabled) agent.  If so, it fetches recent
history, runs the agent service's tool-calling loop, and sends the
response back to the channel.

Guard rails:
- Ignores all bot messages (prevents loops).
- Per-channel cooldown (configurable, default 5 s) — unless the bot is @mentioned.
- Per-channel rate limit (configurable, default 10 responses / minute).
- Runs through the existing TaskQueue so agent work doesn't starve slash commands.
"""

import asyncio
import time
from collections import defaultdict
from typing import Dict, Optional

import discord
from discord.ext import commands

from bot.app.redis.agent_store import AgentRedisStore
from bot.domain.agent.agent_service import run_agent
from bot.api.discord.utils import flatten_discord_message
from bot.api.openai.utils import sanitize_name
from bot.utils import split_message
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
        # channel_id -> asyncio.Lock to prevent concurrent agent runs per channel
        self._channel_locks: Dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

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

    @commands.Cog.listener("on_message")
    async def on_message(self, message: discord.Message) -> None:
        # 1. Ignore bots (including ourselves)
        if message.author.bot:
            return

        # 2. Only handle guild text channels
        if not isinstance(message.channel, discord.TextChannel):
            return

        guild_id = str(message.guild.id)
        channel_id = str(message.channel.id)

        # 3. Check if this channel has an active agent (fast Redis lookup)
        config = await self.store.get_agent_config(guild_id, channel_id)
        if config is None or not config.get("enabled", False):
            return

        # 4. Check if bot is @mentioned (bypasses cooldown)
        bot_mentioned = self.bot.user in message.mentions

        # 5. Cooldown check (skip if bot was mentioned)
        cooldown = config.get("cooldown_seconds", 5)
        if not bot_mentioned and self._check_cooldown(message.channel.id, cooldown):
            return

        # 6. Rate limit check
        max_rpm = config.get("max_responses_per_minute", 10)
        if self._check_rate_limit(message.channel.id, max_rpm):
            logger.warning(f"Agent rate limit hit in channel {channel_id}")
            return

        # 7. Acquire per-channel lock so we don't run multiple agents concurrently
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
                history = []
                async for msg in channel.history(limit=context_window, oldest_first=False):
                    content = flatten_discord_message(msg)
                    author_name = sanitize_name(msg.author.display_name)
                    if msg.author.bot:
                        history.append({
                            "role": "assistant",
                            "content": content,
                            "name": author_name,
                        })
                    else:
                        history.append({
                            "role": "user",
                            "content": content,
                            "name": author_name,
                        })

                history.reverse()  # Chronological order

                # Run the agent loop
                response = await run_agent(
                    channel=channel,
                    history=history,
                    agent_config=config,
                    guild_id=message.guild.id,
                )

            if response and response.strip():
                chunks = split_message(response)
                for chunk in chunks:
                    await channel.send(chunk)

            # Record this response for cooldown/rate tracking
            self._record_response(channel.id)

        except Exception as e:
            logger.error(f"Agent error in channel {channel.id}: {e}", exc_info=True)
            # Don't spam the channel with errors — just log


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AgentListenerCog(bot))
