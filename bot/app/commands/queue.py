"""
Queue Management Commands
Provides commands to monitor and manage the task queue system.
"""

import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional

from bot.app.task_queue import get_task_queue
from bot.app.utils.logger import get_logger

logger = get_logger()

class QueueCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="queue", description="Check the current task queue status")
    async def queue_status(self, interaction: discord.Interaction) -> None:
        """Display current queue status and statistics"""
        try:
            task_queue = get_task_queue()
            status = task_queue.get_queue_status()
            
            embed = discord.Embed(
                title="🔄 Task Queue Status",
                color=discord.Color.blue()
            )
            
            # Add queue statistics
            embed.add_field(
                name="📊 Queue Statistics",
                value=f"**Queued Tasks:** {status['queue_size']}\n"
                      f"**Active Tasks:** {status['active_tasks']}\n"
                      f"**Completed Tasks:** {status['completed_tasks']}\n"
                      f"**Worker Status:** {'🟢 Running' if status['worker_running'] else '🔴 Stopped'}",
                inline=False
            )
            
            # Add status message
            if status['queue_size'] == 0:
                embed.add_field(
                    name="✅ Status",
                    value="No tasks currently queued. New requests will be processed immediately.",
                    inline=False
                )
            else:
                embed.add_field(
                    name="⏳ Status",
                    value=f"There are currently {status['queue_size']} tasks waiting to be processed.",
                    inline=False
                )
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
        except Exception as e:
            logger.error(f"Error getting queue status: {str(e)}")
            await interaction.response.send_message(
                "Sorry, I couldn't retrieve the queue status. Please try again later.",
                ephemeral=True
            )

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(QueueCog(bot)) 