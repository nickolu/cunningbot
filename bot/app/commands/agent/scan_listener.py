"""Stopping a running channel scan: a stop word, or a 🛑 on the status message.

A scan can run for hours, so the person who started it needs a way to call it
off that does not involve remembering a job id. Two ways, both restricted to the
requester so a channel can't stop someone else's scan:

* saying "stop" (or "stop scan", "nvm", "cancel") in the channel being scanned;
* reacting 🛑 to the scan's status message.

Both go through `cancel_channel_scan`, which sets a flag in Redis rather than
killing the task: the scan finishes the page it is on, saves what it found, and
reports as `cancelled` through the usual finish callback. That also means a
cancel lands on a scan being run by a process that has restarted since.

`on_raw_reaction_add` rather than `on_reaction_add`: the status message is
frequently older than the bot's message cache by the time anyone reacts to it.

This cog also hands the bot to `bot/app/scan_access.py` when it loads, which is
how the owner check reaches `bot.is_owner` from inside an agent tool executor.
"""

from typing import Any, Optional

import discord
from discord.ext import commands

from bot.app.redis.scan_store import ScanRedisStore
from bot.app.scan_access import set_scan_client
from bot.app.scan_runtime import cancel_channel_scan
from bot.app.scan_ux import job_for_status_message
from bot.app.utils.logger import get_logger
from bot.domain.scan.report import short_instruction

logger = get_logger()

STOP_EMOJI = "🛑"

# Matched against the whole message, stripped and lowercased: a scan should not
# be cancelled by the word "stop" appearing in the middle of a sentence.
STOP_WORDS = {
    "stop",
    "stop scan",
    "stop the scan",
    "stop scanning",
    "nvm",
    "nevermind",
    "never mind",
    "cancel",
    "cancel scan",
    "cancel the scan",
}

ACK = "🛑 Stopping the scan for {what} — I'll post what it found so far."


def is_stop_word(content: str) -> bool:
    return " ".join(str(content or "").split()).strip(" .!").lower() in STOP_WORDS


class ScanListenerCog(commands.Cog):
    """Lets the person who started a scan stop it."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._store: Optional[ScanRedisStore] = None

    @property
    def store(self) -> ScanRedisStore:
        if self._store is None:
            self._store = ScanRedisStore()
        return self._store

    async def _cancel(self, channel: Any, job: dict) -> None:
        await cancel_channel_scan(job.get("guild_id"), job.get("channel_id"), self.store)
        logger.info({
            "event": "scan_cancel_requested",
            "job": job.get("job_id"),
            "channel": str(job.get("channel_id")),
        })
        try:
            await channel.send(ACK.format(what=short_instruction(job.get("instruction") or "")))
        except Exception as e:
            logger.warning(f"scan {job.get('job_id')}: could not acknowledge the stop: {e}")

    @commands.Cog.listener("on_message")
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        if not isinstance(message.channel, (discord.TextChannel, discord.Thread)):
            return
        # Cheap check first: almost no message is a stop word, and every one
        # that isn't should cost nothing.
        if not is_stop_word(message.content):
            return

        job = await self.store.get_active_job_for_channel(
            message.guild.id, message.channel.id
        )
        if job is None:
            return
        if str(job.get("requester_id")) != str(message.author.id):
            return  # someone else's scan: not theirs to stop

        await self._cancel(message.channel, job)

    @commands.Cog.listener("on_raw_reaction_add")
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if str(payload.emoji) != STOP_EMOJI:
            return
        if self.bot.user is not None and payload.user_id == self.bot.user.id:
            return

        located = job_for_status_message(payload.message_id)
        if located is None:
            return  # not a scan status message
        guild_id, job_id = located

        job = await self.store.get_job(guild_id, job_id)
        if job is None or job.get("status") != "running":
            return
        if str(job.get("requester_id")) != str(payload.user_id):
            return

        try:
            channel = self.bot.get_channel(payload.channel_id) or await self.bot.fetch_channel(
                payload.channel_id
            )
        except Exception as e:
            logger.warning(f"scan {job_id}: could not reach the channel to acknowledge: {e}")
            return

        await self._cancel(channel, job)


async def setup(bot: commands.Bot) -> None:
    # The owner check inside the agent tool has no other way to reach the client.
    set_scan_client(bot)
    await bot.add_cog(ScanListenerCog(bot))
