"""Silently puts plans people mention in chat onto the server's calendar.

In a server where `/calendar enable` has been run, every human message in a
text channel or thread goes through a free regex gate (bot/domain/calendar/
gate.py). The few that mention a day or a time go to the utility model with
the three previous messages for context; if it finds a concrete upcoming
plan, the event is saved and the message gets a 📅 reaction. Nothing is
posted. That reaction is the only sign anything happened.

Undo is a reaction too: the message's author, or anyone who can manage
messages, reacts ❌ and the events taken from that message are removed along
with the 📅. Deleting the message removes them as well.

Gate order matters for cost: the regex runs first because it is free, then
the per-guild config lookup (one Redis GET), then the daily budget counter,
and only then the model. The budget is the backstop for a busy server; when
it runs out the listener goes quiet until the next local day.

`on_raw_*` events rather than the cached ones: the message someone reacts to
may be hours old and long gone from the message cache.
"""

import datetime as dt
from typing import List, Optional

import discord
from discord.ext import commands

from bot.api.discord.utils import flatten_discord_message
from bot.app.redis.calendar_store import CalendarRedisStore
from bot.app.redis.serialization import channel_id_to_str, guild_id_to_str
from bot.app.utils.logger import get_logger
from bot.domain.calendar import service as svc
from bot.domain.calendar.extractor import ExtractError, LLMCall, extract_events
from bot.domain.calendar.gate import looks_like_date
from bot.domain.calendar.models import CalendarEvent, CaptureContext, ChatLine
from bot.domain.scan.extractor import openai_llm

logger = get_logger()

CAPTURE_EMOJI = "📅"
UNDO_EMOJI = "❌"
CONTEXT_MESSAGES = 3


class CalendarListenerCog(commands.Cog):
    def __init__(self, bot: commands.Bot, llm: Optional[LLMCall] = None) -> None:
        self.bot = bot
        self._store: Optional[CalendarRedisStore] = None
        self._llm: LLMCall = llm or openai_llm

    @property
    def store(self) -> CalendarRedisStore:
        if self._store is None:
            self._store = CalendarRedisStore()
        return self._store

    # --------------------------------------------------------------- capture

    @commands.Cog.listener("on_message")
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        if not isinstance(message.channel, (discord.TextChannel, discord.Thread)):
            return
        content = flatten_discord_message(message)
        if not looks_like_date(content):
            return

        guild_id = guild_id_to_str(message.guild.id)
        config = await self.store.get_config(guild_id)
        if not config.get("enabled"):
            return
        tz_name = str(config.get("timezone") or svc.DEFAULT_TIMEZONE_NAME)
        sent_at = message.created_at if message.created_at.tzinfo else message.created_at.replace(tzinfo=dt.timezone.utc)

        spent = await self.store.spend_budget(guild_id, svc.local_day(sent_at, tz_name))
        if spent > svc.DAILY_MODEL_BUDGET:
            if spent == svc.DAILY_MODEL_BUDGET + 1:
                logger.warning({"event": "calendar_budget_exhausted", "guild": guild_id})
            return

        previous = await self._previous_lines(message)
        context = CaptureContext(
            message=ChatLine(message.author.display_name, content, sent_at),
            previous=previous,
            timezone=tz_name,
        )
        try:
            extracted = await extract_events(context, self._llm)
        except ExtractError as e:
            logger.error({"event": "calendar_extract_failed", "guild": guild_id, "error": str(e)})
            return

        saved: List[CalendarEvent] = []
        for proposal in extracted:
            same_day = await self.store.get_events_on(guild_id, proposal.date, tz_name)
            if svc.is_duplicate(proposal.title, proposal.date, same_day):
                continue
            event = CalendarEvent(
                id=await self._fresh_id(guild_id),
                title=proposal.title,
                date=proposal.date,
                time=proposal.time,
                end_date=proposal.end_date,
                timezone=tz_name,
                channel_id=channel_id_to_str(message.channel.id),
                message_id=str(message.id),
                author_id=str(message.author.id),
                author_name=message.author.display_name,
                jump_url=message.jump_url,
                created_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                source="capture",
                confidence=proposal.confidence,
            )
            await self.store.add_event(guild_id, event)
            saved.append(event)

        logger.info({
            "event": "calendar_capture",
            "guild": guild_id,
            "channel": channel_id_to_str(message.channel.id),
            "proposed": len(extracted),
            "saved": [e.title for e in saved],
        })
        if not saved:
            return

        await self.store.trim_past(guild_id, svc.past_cutoff_epoch(sent_at))
        try:
            await message.add_reaction(CAPTURE_EMOJI)
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.warning({"event": "calendar_react_failed", "guild": guild_id, "error": str(e)})

    async def _previous_lines(self, message: discord.Message) -> List[ChatLine]:
        lines: List[ChatLine] = []
        try:
            async for prior in message.channel.history(limit=CONTEXT_MESSAGES, before=message):
                text = flatten_discord_message(prior)
                if not text.strip():
                    continue
                when = prior.created_at if prior.created_at.tzinfo else prior.created_at.replace(tzinfo=dt.timezone.utc)
                lines.append(ChatLine(prior.author.display_name, text, when))
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.warning({"event": "calendar_context_failed", "error": str(e)})
        lines.reverse()
        return lines

    async def _fresh_id(self, guild_id: str) -> str:
        for _ in range(5):
            candidate = svc.new_event_id()
            if not await self.store.event_id_exists(guild_id, candidate):
                return candidate
        return svc.new_event_id() + svc.new_event_id()

    # ------------------------------------------------------------------ undo

    @commands.Cog.listener("on_raw_reaction_add")
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if str(payload.emoji) != UNDO_EMOJI or payload.guild_id is None:
            return
        if self.bot.user is not None and payload.user_id == self.bot.user.id:
            return
        guild_id = guild_id_to_str(payload.guild_id)
        message_id = str(payload.message_id)
        events = await self.store.get_events_for_message(guild_id, message_id)
        if not events:
            return
        if not self._may_undo(payload, events):
            return

        removed = await self.store.delete_events_for_message(guild_id, message_id)
        logger.info({
            "event": "calendar_undo",
            "guild": guild_id,
            "by": str(payload.user_id),
            "removed": [e.title for e in removed],
        })
        await self._remove_capture_reaction(payload)

    def _may_undo(self, payload: discord.RawReactionActionEvent, events: List[CalendarEvent]) -> bool:
        user_id = str(payload.user_id)
        if any(e.author_id == user_id for e in events):
            return True
        member = payload.member
        perms = getattr(member, "guild_permissions", None)
        return bool(perms is not None and perms.manage_messages)

    async def _remove_capture_reaction(self, payload: discord.RawReactionActionEvent) -> None:
        if self.bot.user is None:
            return
        try:
            channel = self.bot.get_channel(payload.channel_id) or await self.bot.fetch_channel(payload.channel_id)
            message = await channel.fetch_message(payload.message_id)  # type: ignore[union-attr]
            await message.remove_reaction(CAPTURE_EMOJI, self.bot.user)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException, AttributeError) as e:
            logger.warning({"event": "calendar_unreact_failed", "error": str(e)})

    @commands.Cog.listener("on_raw_message_delete")
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        if payload.guild_id is None:
            return
        guild_id = guild_id_to_str(payload.guild_id)
        removed = await self.store.delete_events_for_message(guild_id, str(payload.message_id))
        if removed:
            logger.info({
                "event": "calendar_source_deleted",
                "guild": guild_id,
                "removed": [e.title for e in removed],
            })


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CalendarListenerCog(bot))
