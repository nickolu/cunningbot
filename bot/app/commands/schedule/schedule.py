"""/schedule — see and manage scheduled prompts without going through the agent.

/schedule list    — this server's scheduled prompts (only you see it)
/schedule cancel  — stop one for good
/schedule pause   — stop it running for now
/schedule resume  — start a paused one again, from its next run

Creating one goes through the agent (`schedule_prompt`), which turns "every
weekday at 9am" into a schedule and reads it back before anything is saved.

Cancel, pause, and resume are for the job's creator or anyone who can manage
messages in the channel the command is used in -- the same rule as the
`cancel_scheduled_prompt` tool. Changes are posted publicly, so a channel can
see who stopped its daily summary.
"""

from typing import List, Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot.app.redis.schedule_store import STATUS_ACTIVE, STATUS_PAUSED, ScheduleRedisStore
from bot.app.schedule_jobs import (
    cancel_scheduled_prompt,
    may_manage,
    pause_scheduled_prompt,
    resume_scheduled_prompt,
)
from bot.app.utils.logger import get_logger
from bot.domain.agent.tools.list_scheduled_prompts import job_lines
from bot.domain.schedule.describe import describe_schedule, preview

logger = get_logger()

NO_JOBS = "This server has no scheduled prompts. Ask the bot to set one up, e.g. \"post a summary of this channel every day at 9am\"."
NOT_FOUND = "No scheduled prompt with that id in this server. `/schedule list` shows them."
NOT_ALLOWED = "Only the person who set it up, or someone who can manage messages here, can change it."
QUIET = discord.AllowedMentions.none()


class ScheduleCog(commands.Cog):
    schedule = app_commands.Group(
        name="schedule", description="See and manage scheduled prompts", guild_only=True
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def job_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        if interaction.guild is None:
            return []
        jobs = await ScheduleRedisStore().list_jobs(interaction.guild.id)
        current = current.lower()
        choices = []
        for job in jobs:
            label = f"{preview(job['prompt'], 50)} — {describe_schedule(job['cron'], job['tz'])}"
            if current and current not in label.lower() and current not in job["job_id"]:
                continue
            choices.append(app_commands.Choice(name=label[:100], value=job["job_id"]))
        return choices[:25]

    async def _job_you_may_change(
        self, interaction: discord.Interaction, job_id: str
    ) -> Optional[dict]:
        """The job, or None after telling the user why they can't have it."""
        job = await ScheduleRedisStore().get_job(interaction.guild.id, job_id.strip())
        if job is None:
            await interaction.response.send_message(NOT_FOUND, ephemeral=True)
            return None
        if not may_manage(job, interaction.user.id, interaction.permissions.manage_messages):
            await interaction.response.send_message(NOT_ALLOWED, ephemeral=True)
            return None
        return job

    def _what(self, job: dict) -> str:
        return (
            f"*{discord.utils.escape_markdown(preview(job['prompt'], 80))}* "
            f"({describe_schedule(job['cron'], job['tz'])})"
        )

    @schedule.command(name="list", description="This server's scheduled prompts")
    async def list_jobs(self, interaction: discord.Interaction) -> None:
        jobs = await ScheduleRedisStore().list_jobs(interaction.guild.id)
        if not jobs:
            await interaction.response.send_message(NO_JOBS, ephemeral=True)
            return
        embed = discord.Embed(
            title=f"🗓️ Scheduled prompts ({len(jobs)})",
            description=job_lines(interaction.guild, jobs)[:4000],
            color=0x5865F2,
        )
        embed.set_footer(text="Stop one with /schedule cancel, or pause it with /schedule pause.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @schedule.command(name="cancel", description="Stop a scheduled prompt for good")
    @app_commands.describe(job="The scheduled prompt (start typing to search)")
    async def cancel(self, interaction: discord.Interaction, job: str) -> None:
        record = await self._job_you_may_change(interaction, job)
        if record is None:
            return
        await cancel_scheduled_prompt(interaction.guild.id, record["job_id"])
        await interaction.response.send_message(
            f"🗓️ {interaction.user.mention} cancelled the scheduled prompt {self._what(record)}.",
            allowed_mentions=QUIET,
        )

    @schedule.command(name="pause", description="Stop a scheduled prompt running for now")
    @app_commands.describe(job="The scheduled prompt (start typing to search)")
    async def pause(self, interaction: discord.Interaction, job: str) -> None:
        record = await self._job_you_may_change(interaction, job)
        if record is None:
            return
        if record.get("status") == STATUS_PAUSED:
            await interaction.response.send_message("That one is already paused.", ephemeral=True)
            return
        await pause_scheduled_prompt(
            interaction.guild.id, record["job_id"], reason=f"paused by {interaction.user.display_name}"
        )
        await interaction.response.send_message(
            f"🗓️ {interaction.user.mention} paused the scheduled prompt {self._what(record)}. "
            f"`/schedule resume` starts it again.",
            allowed_mentions=QUIET,
        )

    @schedule.command(name="resume", description="Start a paused scheduled prompt again")
    @app_commands.describe(job="The scheduled prompt (start typing to search)")
    async def resume(self, interaction: discord.Interaction, job: str) -> None:
        record = await self._job_you_may_change(interaction, job)
        if record is None:
            return
        if record.get("status") == STATUS_ACTIVE:
            await interaction.response.send_message("That one isn't paused.", ephemeral=True)
            return
        await resume_scheduled_prompt(interaction.guild.id, record["job_id"])
        await interaction.response.send_message(
            f"🗓️ {interaction.user.mention} resumed the scheduled prompt {self._what(record)}.",
            allowed_mentions=QUIET,
        )

    cancel.autocomplete("job")(job_autocomplete)
    pause.autocomplete("job")(job_autocomplete)
    resume.autocomplete("job")(job_autocomplete)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ScheduleCog(bot))
