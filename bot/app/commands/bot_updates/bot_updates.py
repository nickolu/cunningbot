"""Bot updates Discord commands."""

import discord
from discord import app_commands
from discord.ext import commands

from bot.app.redis.bot_updates_store import BotUpdatesRedisStore
from bot.domain.bot_updates.notification_service import BotUpdateNotificationService
from bot.app.utils.logger import get_logger

logger = get_logger()


class BotUpdates(commands.Cog):
    """Commands for managing bot restart notifications."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    updates_group = app_commands.Group(
        name="bot-updates",
        description="Manage bot restart notifications"
    )

    @updates_group.command(
        name="register",
        description="Register this channel for bot restart notifications"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def register(self, interaction: discord.Interaction):
        """Register current channel for restart notifications."""
        store = BotUpdatesRedisStore()
        channel_id = interaction.channel_id

        is_registered = await store.is_channel_registered(channel_id)

        if is_registered:
            await interaction.response.send_message(
                "✅ This channel is already registered for bot restart notifications.",
                ephemeral=True
            )
            return

        await store.register_channel(channel_id)

        await interaction.response.send_message(
            "✅ **Channel registered!**\n"
            "This channel will now receive notifications when the bot restarts or shuts down.",
            ephemeral=True
        )

        logger.info(
            f"Channel {channel_id} registered for bot updates by {interaction.user.name}"
        )

    @updates_group.command(
        name="unregister",
        description="Unregister this channel from bot restart notifications"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def unregister(self, interaction: discord.Interaction):
        """Unregister current channel from restart notifications."""
        store = BotUpdatesRedisStore()
        channel_id = interaction.channel_id

        was_registered = await store.unregister_channel(channel_id)

        if not was_registered:
            await interaction.response.send_message(
                "❌ This channel is not registered for bot restart notifications.",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            "✅ **Channel unregistered!**\n"
            "This channel will no longer receive bot restart notifications.",
            ephemeral=True
        )

        logger.info(
            f"Channel {channel_id} unregistered from bot updates by {interaction.user.name}"
        )

    @updates_group.command(
        name="list",
        description="Show all channels registered for bot restart notifications"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def list_channels(self, interaction: discord.Interaction):
        """List all registered channels."""
        store = BotUpdatesRedisStore()
        channel_ids = await store.get_all_registered_channels()

        if not channel_ids:
            await interaction.response.send_message(
                "📋 No channels are currently registered for bot restart notifications.",
                ephemeral=True
            )
            return

        # Build list of channels with guild context
        channel_lines = []
        for channel_id in sorted(channel_ids):
            try:
                channel = self.bot.get_channel(channel_id)

                if channel is None:
                    # Try fetching if not in cache
                    try:
                        channel = await self.bot.fetch_channel(channel_id)
                    except (discord.NotFound, discord.Forbidden):
                        channel_lines.append(
                            f"• <#{channel_id}> (Channel ID: {channel_id}) - ⚠️ Inaccessible"
                        )
                        continue

                if isinstance(channel, discord.TextChannel):
                    guild_name = channel.guild.name
                    channel_lines.append(
                        f"• <#{channel_id}> in **{guild_name}**"
                    )
                else:
                    channel_lines.append(
                        f"• <#{channel_id}> (Channel ID: {channel_id})"
                    )

            except Exception as e:
                logger.error(f"Error fetching channel {channel_id}: {e}")
                channel_lines.append(
                    f"• Channel ID: {channel_id} - ⚠️ Error"
                )

        channels_text = "\n".join(channel_lines)

        await interaction.response.send_message(
            f"📋 **Registered Channels ({len(channel_ids)}):**\n{channels_text}",
            ephemeral=True
        )

    @updates_group.command(
        name="test",
        description="Send a test notification to this channel"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def test(self, interaction: discord.Interaction):
        """Send a test notification to the current channel."""
        channel = interaction.channel

        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "❌ This command can only be used in text channels.",
                ephemeral=True
            )
            return

        # Defer response since we're about to send a message
        await interaction.response.defer(ephemeral=True)

        notification_service = BotUpdateNotificationService()
        bot_name = self.bot.user.name if self.bot.user else "Bot"
        success = await notification_service.send_test_notification(
            channel, bot_name
        )

        if success:
            await interaction.followup.send(
                "✅ Test notification sent to this channel!",
                ephemeral=True
            )
            logger.info(
                f"Test notification sent to channel {channel.id} by {interaction.user.name}"
            )
        else:
            await interaction.followup.send(
                "❌ Failed to send test notification. Check bot permissions.",
                ephemeral=True
            )

    @register.error
    @unregister.error
    @list_channels.error
    @test.error
    async def bot_updates_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        """Handle errors for bot-updates commands."""
        if isinstance(error, app_commands.errors.MissingPermissions):
            await interaction.response.send_message(
                "❌ You need administrator permissions to use this command.",
                ephemeral=True
            )
        else:
            logger.error(f"Error in bot-updates command: {error}")
            await interaction.response.send_message(
                "❌ An error occurred while processing the command.",
                ephemeral=True
            )


async def setup(bot: commands.Bot):
    """Load the BotUpdates cog."""
    await bot.add_cog(BotUpdates(bot))
