"""on_message listener that drives the channel agent.

This cog listens to every message in the guild and checks whether the
channel has a registered (and enabled) agent.  If so, it applies smart
triggering logic to decide whether to respond, then fetches recent
history, runs the agent service's tool-calling loop, and sends the
response back to the channel.

An explicit summon — an @mention, a reply to the bot, or the bot's name
in the message text — always gets a response, whatever the response mode,
and skips the classifier.  Channels with no registration are not ignored
outright: a summon wakes the agent there with default settings.  A
*paused* agent stays silent either way; pausing is a deliberate opt-out.

Response modes:
- "smart" (default): Uses hard gates + LLM intent classifier to decide
  when to respond. Only responds when confident the bot is being addressed.
- "strict": Only responds when explicitly summoned.
- "always": Responds to every message (original behavior). Cooldown still applies.

Guard rails:
- Ignores all bot messages (prevents loops).
- Per-channel cooldown (configurable, default 5 s) — unless explicitly summoned.
- Per-channel rate limit (configurable, default 10 responses / minute).
- Runs through the existing TaskQueue so agent work doesn't starve slash commands.
"""

import asyncio
import time
from collections import defaultdict
from typing import Dict, Optional, Pattern

import discord
from discord.ext import commands

from bot.app.redis.agent_store import DEFAULT_AGENT_CONFIG, AgentRedisStore
from bot.domain.agent.agent_service import run_agent
from bot.domain.agent.agent_tools import TOOL_SCHEMAS
from bot.domain.agent.intent_classifier import Intent, classify_intent
from bot.domain.agent.summon import build_summon_pattern, is_summoned_by_name
from bot.api.discord.utils import flatten_discord_message
from bot.api.openai.utils import sanitize_name
from bot.utils import split_message
from bot.app.utils.logger import get_logger

logger = get_logger()

ASK_CLARIFY_RESPONSE = "Did you want me to do something, or just chatting?"

# Config used when the bot is summoned in a channel with no registration.
# Strict mode: an unregistered channel only ever gets a reply when asked
# directly, never off the intent classifier's judgement.
UNREGISTERED_AGENT_CONFIG = {
    **DEFAULT_AGENT_CONFIG,
    "tools": list(TOOL_SCHEMAS.keys()),
    "response_mode": "strict",
}


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

    def _is_summoned(self, message: discord.Message) -> bool:
        """True if the message addresses the bot directly.

        A mention, a reply to something the bot said, or the bot's name in
        the text all count.
        """
        if self.bot.user in message.mentions:
            return True
        if self._is_reply_to_bot(message):
            return True
        return is_summoned_by_name(
            message.content, self._summon_pattern(message.guild)
        )

    async def _should_respond(
        self, message: discord.Message, config: Dict, summoned: bool
    ) -> bool:
        """Apply smart triggering logic based on response_mode.

        Returns True if the bot should respond, False otherwise.
        For ASK_CLARIFY results, sends a short clarification and returns False.
        """
        response_mode = config.get("response_mode", "smart")

        # Being addressed directly wins in every mode.
        if summoned:
            return True

        # "always" mode — original behavior, respond to everything
        if response_mode == "always":
            return True

        # "strict" mode — only an explicit summon triggers
        if response_mode == "strict":
            return False

        # --- "smart" mode: run LLM intent classifier ---
        content = flatten_discord_message(message)
        if not content or not content.strip():
            return False

        # Build minimal history for classifier context
        recent_history = []
        async for msg in message.channel.history(limit=8, oldest_first=False):
            if msg.id == message.id:
                continue
            author_name = sanitize_name(msg.author.display_name)
            recent_history.append({
                "role": "assistant" if msg.author.bot else "user",
                "content": flatten_discord_message(msg)[:200],
                "name": author_name,
            })
        recent_history.reverse()

        bot_name = self.bot.user.display_name if self.bot.user else "Bot"

        intent = await classify_intent(
            latest_message=content,
            recent_history=recent_history,
            bot_name=bot_name,
        )

        logger.info({
            "event": "intent_classification",
            "channel": message.channel.id,
            "intent": intent.value,
            "message_preview": content[:80],
        })

        if intent == Intent.RESPOND:
            return True

        if intent == Intent.ASK_CLARIFY:
            await message.channel.send(ASK_CLARIFY_RESPONSE)
            self._record_response(message.channel.id)
            return False

        return False

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

        # 3. Is the bot being addressed directly? (mention, reply, or by name)
        summoned = self._is_summoned(message)

        # 4. Look up the channel's agent (fast Redis lookup)
        config = await self.store.get_agent_config(guild_id, channel_id)
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
        elif not config.get("enabled", False):
            # Paused is a deliberate opt-out; stay quiet even when summoned.
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

        # 7. Smart triggering — decide whether we should respond
        should_respond = await self._should_respond(message, config, summoned)
        if not should_respond:
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
                history = []
                async for msg in channel.history(limit=context_window, oldest_first=False):
                    content = flatten_discord_message(msg)
                    for att in msg.attachments:
                        if att.content_type and att.content_type.startswith("image/"):
                            content += f"\n[Image: {att.filename} | {att.url}]"
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
