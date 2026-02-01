"""Business logic for bot restart notifications.

This module handles creating and sending restart notifications to registered channels.
"""

import logging
from datetime import datetime
from typing import Dict, Optional

import discord

from bot.app.redis.bot_updates_store import BotUpdatesRedisStore

logger = logging.getLogger("BotUpdateNotificationService")


class BotUpdateNotificationService:
    """Service for creating and sending bot restart notifications."""

    def __init__(self):
        self.store = BotUpdatesRedisStore()

    def create_restart_embed(
        self, bot_name: str, timestamp: datetime, reason: Optional[str] = None
    ) -> discord.Embed:
        """Create a restart notification embed.

        Args:
            bot_name: Name of the bot
            timestamp: Timestamp when bot came online
            reason: Optional restart reason (for future enhancement)

        Returns:
            Discord embed with restart information
        """
        embed = discord.Embed(
            title="🟢 Bot Restart Notification",
            description=f"**{bot_name}** has restarted and is now online.",
            color=discord.Color.green(),
            timestamp=timestamp,
        )

        embed.add_field(
            name="Status", value="✅ Online and ready", inline=True
        )

        if reason:
            embed.add_field(
                name="Reason", value=reason, inline=True
            )

        embed.set_footer(text="Bot restart notification")

        return embed

    def create_shutdown_embed(
        self, bot_name: str, timestamp: datetime, reason: Optional[str] = None
    ) -> discord.Embed:
        """Create a shutdown notification embed.

        Args:
            bot_name: Name of the bot
            timestamp: Timestamp when shutdown initiated
            reason: Optional shutdown reason

        Returns:
            Discord embed with shutdown information
        """
        embed = discord.Embed(
            title="🔴 Bot Shutdown Notification",
            description=f"**{bot_name}** is shutting down for maintenance.",
            color=discord.Color.red(),
            timestamp=timestamp,
        )

        embed.add_field(
            name="Status", value="⚠️ Going offline", inline=True
        )

        if reason:
            embed.add_field(
                name="Reason", value=reason, inline=True
            )

        embed.add_field(
            name="Expected",
            value="Bot will be back online shortly",
            inline=False
        )

        embed.set_footer(text="Bot shutdown notification")

        return embed

    def create_test_embed(self, bot_name: str) -> discord.Embed:
        """Create a test notification embed.

        Args:
            bot_name: Name of the bot

        Returns:
            Discord embed for test notifications
        """
        embed = discord.Embed(
            title="🔵 Test Notification",
            description=f"This is a test notification from **{bot_name}**.",
            color=discord.Color.blue(),
            timestamp=datetime.now(),
        )

        embed.add_field(
            name="Status",
            value="✅ This channel will receive restart notifications",
            inline=False,
        )

        embed.set_footer(text="Test notification")

        return embed

    async def send_restart_notifications(
        self, bot: discord.Client, reason: Optional[str] = None
    ) -> Dict[str, int]:
        """Send restart notifications to all registered channels.

        Args:
            bot: Discord bot client
            reason: Optional restart reason

        Returns:
            Dictionary with 'success', 'failed', and 'total' counts
        """
        channel_ids = await self.store.get_all_registered_channels()
        total = len(channel_ids)

        if total == 0:
            logger.info("No channels registered for restart notifications")
            return {"success": 0, "failed": 0, "total": 0}

        logger.info(f"Sending restart notifications to {total} channels")

        bot_name = bot.user.name if bot.user else "Bot"
        timestamp = datetime.now()
        embed = self.create_restart_embed(bot_name, timestamp, reason)

        success_count = 0
        failed_count = 0

        for channel_id in channel_ids:
            try:
                channel = bot.get_channel(channel_id)

                if channel is None:
                    # Try fetching if not in cache
                    try:
                        channel = await bot.fetch_channel(channel_id)
                    except discord.NotFound:
                        logger.warning(
                            f"Channel {channel_id} not found (deleted?)"
                        )
                        failed_count += 1
                        continue
                    except discord.Forbidden:
                        logger.warning(
                            f"No access to channel {channel_id} (permissions?)"
                        )
                        failed_count += 1
                        continue

                # Check if we can send messages
                if isinstance(channel, discord.TextChannel):
                    if not channel.permissions_for(channel.guild.me).send_messages:
                        logger.warning(
                            f"No permission to send messages in channel {channel_id}"
                        )
                        failed_count += 1
                        continue

                await channel.send(embed=embed)
                logger.info(f"Sent restart notification to channel {channel_id}")
                success_count += 1

            except discord.Forbidden:
                logger.warning(
                    f"Forbidden: Cannot send message to channel {channel_id}"
                )
                failed_count += 1
            except discord.HTTPException as e:
                logger.error(
                    f"HTTP error sending to channel {channel_id}: {e}"
                )
                failed_count += 1
            except Exception as e:
                logger.error(
                    f"Unexpected error sending to channel {channel_id}: {e}"
                )
                failed_count += 1

        return {"success": success_count, "failed": failed_count, "total": total}

    async def send_shutdown_notifications(
        self, bot: discord.Client, reason: Optional[str] = None
    ) -> Dict[str, int]:
        """Send shutdown notifications to all registered channels.

        Args:
            bot: Discord bot client
            reason: Optional shutdown reason

        Returns:
            Dictionary with 'success', 'failed', and 'total' counts
        """
        channel_ids = await self.store.get_all_registered_channels()
        total = len(channel_ids)

        if total == 0:
            logger.info("No channels registered for shutdown notifications")
            return {"success": 0, "failed": 0, "total": 0}

        logger.info(f"Sending shutdown notifications to {total} channels")

        bot_name = bot.user.name if bot.user else "Bot"
        timestamp = datetime.now()
        embed = self.create_shutdown_embed(bot_name, timestamp, reason)

        success_count = 0
        failed_count = 0

        for channel_id in channel_ids:
            try:
                channel = bot.get_channel(channel_id)

                if channel is None:
                    # Try fetching if not in cache
                    try:
                        channel = await bot.fetch_channel(channel_id)
                    except discord.NotFound:
                        logger.warning(
                            f"Channel {channel_id} not found (deleted?)"
                        )
                        failed_count += 1
                        continue
                    except discord.Forbidden:
                        logger.warning(
                            f"No access to channel {channel_id} (permissions?)"
                        )
                        failed_count += 1
                        continue

                # Check if we can send messages
                if isinstance(channel, discord.TextChannel):
                    if not channel.permissions_for(channel.guild.me).send_messages:
                        logger.warning(
                            f"No permission to send messages in channel {channel_id}"
                        )
                        failed_count += 1
                        continue

                await channel.send(embed=embed)
                logger.info(f"Sent shutdown notification to channel {channel_id}")
                success_count += 1

            except discord.Forbidden:
                logger.warning(
                    f"Forbidden: Cannot send message to channel {channel_id}"
                )
                failed_count += 1
            except discord.HTTPException as e:
                logger.error(
                    f"HTTP error sending to channel {channel_id}: {e}"
                )
                failed_count += 1
            except Exception as e:
                logger.error(
                    f"Unexpected error sending to channel {channel_id}: {e}"
                )
                failed_count += 1

        return {"success": success_count, "failed": failed_count, "total": total}

    async def send_test_notification(
        self, channel: discord.TextChannel, bot_name: str
    ) -> bool:
        """Send a test notification to a specific channel.

        Args:
            channel: Discord text channel
            bot_name: Name of the bot

        Returns:
            True if sent successfully, False otherwise
        """
        try:
            embed = self.create_test_embed(bot_name)
            await channel.send(embed=embed)
            logger.info(f"Sent test notification to channel {channel.id}")
            return True
        except discord.Forbidden:
            logger.warning(f"Forbidden: Cannot send test to channel {channel.id}")
            return False
        except discord.HTTPException as e:
            logger.error(f"HTTP error sending test to channel {channel.id}: {e}")
            return False
        except Exception as e:
            logger.error(
                f"Unexpected error sending test to channel {channel.id}: {e}"
            )
            return False
