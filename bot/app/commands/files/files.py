"""/files — send the bot files, and find them again, without going through the agent.

/files upload  — open an upload link (only you see it; share it if you like)
/files list    — what has been uploaded for this server, optionally filtered
/files delete  — remove one file: for whoever opened its link, or anyone who
                 can manage messages

Uploading is a web page, not a Discord attachment: Discord's attachment links
expire, and nothing in Discord lets a user hand the bot a file for keeps. The
link stays open fifteen minutes; when it closes (or fills up) the bot posts
what came through it in this channel.
"""

from typing import Any, Dict, List, Optional

import discord
from discord import app_commands
from discord.ext import commands

from bot.api.pages.client import PagesNotDeployed, UploadsNotConfigured
from bot.app.upload_watch import start_upload_watch
from bot.app.utils.logger import get_logger
from bot.domain.files.file_service import (
    KIND_ANY,
    KIND_IMAGES,
    MAX_FILES_CAP,
    delete_file,
    describe_file,
    expires_in_words,
    format_bytes,
    open_upload_link,
    search_files,
)

logger = get_logger()

NOT_CONFIGURED = "Uploads aren't available: the pages service isn't configured for this bot."
NOT_SET_UP = (
    "Uploads aren't set up yet: the page service has no storage bucket. "
    "The bot's owner needs to do a one-time setup (see web/README.md)."
)
OLD_SERVICE = "The page service is running an older version without uploads. It needs a redeploy."
NOT_FOUND = "No uploaded file with that id in this server. `/files list` shows them."
NOT_ALLOWED = "Only the person who opened that upload link, or someone who can manage messages here, can delete it."
LIST_LIMIT = 15


def may_delete(record: Dict[str, Any], user_id: int, manage_messages: bool) -> bool:
    """Whoever opened the link a file came through may remove it, or a moderator."""
    if manage_messages:
        return True
    uploader = record.get("uploader_id")
    return bool(uploader) and str(uploader) == str(user_id)


class FilesCog(commands.Cog):
    files = app_commands.Group(
        name="files", description="Send the bot files and find them again", guild_only=True
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def file_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        if interaction.guild is None:
            return []
        try:
            found, _ = await search_files(str(interaction.guild.id), query=current, limit=25)
        except (EnvironmentError, RuntimeError):
            return []
        choices = []
        for record in found:
            name = record.get("name") or "file"
            who = record.get("uploader_name")
            when = (record.get("created_at") or "")[:10]
            label = " · ".join(p for p in (name, who, when) if p)
            choices.append(app_commands.Choice(name=label[:100], value=str(record.get("id"))))
        return choices[:25]

    @files.command(name="upload", description="Open a link where files can be uploaded to the bot")
    @app_commands.describe(
        label="What the files are for, e.g. 'Tahoe trip photos'",
        kind="Accept only images, or any file",
        max_files=f"How many files the link accepts (1-{MAX_FILES_CAP}, default 10)",
        keep="How long to keep the files",
    )
    @app_commands.choices(
        kind=[
            app_commands.Choice(name="Any file", value=KIND_ANY),
            app_commands.Choice(name="Images only", value=KIND_IMAGES),
        ],
        keep=[
            app_commands.Choice(name="1 day", value=1),
            app_commands.Choice(name="7 days", value=7),
            app_commands.Choice(name="30 days", value=30),
        ],
    )
    async def upload(
        self,
        interaction: discord.Interaction,
        label: Optional[str] = None,
        kind: Optional[app_commands.Choice[str]] = None,
        max_files: Optional[app_commands.Range[int, 1, MAX_FILES_CAP]] = None,
        keep: Optional[app_commands.Choice[int]] = None,
    ) -> None:
        guild = interaction.guild
        channel = interaction.channel
        if guild is None or channel is None:
            await interaction.response.send_message("Use this inside a server channel.", ephemeral=True)
            return

        try:
            link = await open_upload_link(
                guild_id=str(guild.id),
                channel_id=str(channel.id),
                channel_name=getattr(channel, "name", None),
                requester_id=str(interaction.user.id),
                requester_name=interaction.user.display_name,
                label=label,
                kind=kind.value if kind else None,
                max_files=max_files,
                ttl_days=keep.value if keep else None,
            )
        except EnvironmentError:
            await interaction.response.send_message(NOT_CONFIGURED, ephemeral=True)
            return
        except UploadsNotConfigured:
            await interaction.response.send_message(NOT_SET_UP, ephemeral=True)
            return
        except PagesNotDeployed:
            await interaction.response.send_message(OLD_SERVICE, ephemeral=True)
            return
        except RuntimeError as e:
            await interaction.response.send_message(f"Couldn't open an upload link: {e}", ephemeral=True)
            return

        start_upload_watch(
            channel,
            str(guild.id),
            link.session_id,
            link.expires_at,
            link.max_files,
            requester_mention=interaction.user.mention,
            label=(label or "").strip() or None,
        )

        what = "images" if link.kind == KIND_IMAGES else "files"
        days = f"{link.ttl_days} day{'s' if link.ttl_days != 1 else ''}"
        embed = discord.Embed(
            title=label or "Upload link",
            description=(
                f"**{link.url}**\n\n"
                f"Takes up to {link.max_files} {what} of {format_bytes(link.max_bytes)} each, "
                f"kept for {days}. Closes {expires_in_words(link.expires_at)}.\n\n"
                "Only you can see this. Share the link with whoever should upload; "
                "when it closes, I'll post what came through it here."
            ),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @files.command(name="list", description="Files uploaded to the bot for this server")
    @app_commands.describe(query="Words to match in the name, note, label, or uploader (optional)")
    async def list_files(self, interaction: discord.Interaction, query: Optional[str] = None) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Use this inside a server.", ephemeral=True)
            return
        try:
            found, total = await search_files(
                str(interaction.guild.id), query=(query or "").strip(), limit=LIST_LIMIT
            )
        except EnvironmentError:
            await interaction.response.send_message(NOT_CONFIGURED, ephemeral=True)
            return
        except PagesNotDeployed:
            await interaction.response.send_message(OLD_SERVICE, ephemeral=True)
            return
        except RuntimeError as e:
            await interaction.response.send_message(f"Couldn't list files: {e}", ephemeral=True)
            return

        if not found:
            msg = (
                f"No uploaded files match “{query}”." if query
                else "Nothing has been uploaded for this server yet. `/files upload` opens a link."
            )
            await interaction.response.send_message(msg, ephemeral=True)
            return

        title = f"Uploaded files matching “{query}”" if query else "Uploaded files"
        lines = [describe_file(f) for f in found]
        if total > len(found):
            lines.append(f"…and {total - len(found)} more. Narrow it with a query.")
        embed = discord.Embed(
            title=title[:256],
            description="\n".join(lines)[:4000],
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @files.command(name="delete", description="Remove an uploaded file")
    @app_commands.describe(file="The file (start typing to search)")
    async def delete(self, interaction: discord.Interaction, file: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Use this inside a server.", ephemeral=True)
            return
        guild_id = str(interaction.guild.id)
        file_id = file.strip().lower()

        try:
            found, _ = await search_files(guild_id, query="", limit=500)
            record = next((f for f in found if str(f.get("id")) == file_id), None)
            if record is None:
                await interaction.response.send_message(NOT_FOUND, ephemeral=True)
                return
            if not may_delete(record, interaction.user.id, interaction.permissions.manage_messages):
                await interaction.response.send_message(NOT_ALLOWED, ephemeral=True)
                return
            deleted = await delete_file(guild_id, file_id)
        except EnvironmentError:
            await interaction.response.send_message(NOT_CONFIGURED, ephemeral=True)
            return
        except PagesNotDeployed:
            await interaction.response.send_message(OLD_SERVICE, ephemeral=True)
            return
        except RuntimeError as e:
            await interaction.response.send_message(f"Couldn't delete that file: {e}", ephemeral=True)
            return

        if not deleted:
            await interaction.response.send_message(NOT_FOUND, ephemeral=True)
            return
        name = record.get("name") or "the file"
        await interaction.response.send_message(
            f"🗑️ Deleted **{discord.utils.escape_markdown(name)}**. Its link stops working now.",
            ephemeral=True,
        )

    delete.autocomplete("file")(file_autocomplete)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(FilesCog(bot))
