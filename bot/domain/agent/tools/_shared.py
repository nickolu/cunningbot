"""Helpers shared by more than one agent tool."""

from io import BytesIO

import discord

from bot.app.utils.logger import get_logger

logger = get_logger()


async def _send_image_and_describe(
    channel: discord.TextChannel,
    image_bytes: bytes,
    filename: str,
    summary: str,
    host: bool = False,
) -> str:
    """Post an image to the channel and tell the model how to refer to it.

    The model needs a real URL. Without one it invents things like
    ``attachment://generated_image`` -- a Discord-internal embed scheme that
    means nothing on the open web -- and any page built with it is broken from
    the moment it is published.

    Discord's own attachment URL is returned for conversational use but labelled
    temporary, since it stops resolving after about a day. When the image is
    headed for a page, ``host=True`` uploads the bytes already in hand and
    returns a permanent URL, avoiding a re-download just to re-host it.
    """
    stream = BytesIO(image_bytes)
    stream.seek(0)
    message = await channel.send(file=discord.File(fp=stream, filename=filename))

    temp_url = ""
    if message is not None and getattr(message, "attachments", None):
        temp_url = message.attachments[0].url

    if host:
        guild = getattr(channel, "guild", None)
        if guild is None:
            return f"{summary} Could not host it (not in a server). Temporary URL: {temp_url}"
        try:
            from bot.domain.pages.image_service import host_image_bytes

            hosted = await host_image_bytes(
                str(guild.id), image_bytes, content_type="image/png", filename=filename
            )
            return f"{summary} Permanent URL, safe to put on a page: {hosted}"
        except EnvironmentError:
            return f"{summary} Image hosting is not configured; temporary URL: {temp_url}"
        except RuntimeError as e:
            return f"{summary} Could not host it ({e}); temporary URL: {temp_url}"

    if not temp_url:
        return summary
    return (
        f"{summary} Temporary URL (expires in about a day -- do NOT put this on a "
        f"page; call host_image first, or regenerate with host=true): {temp_url}"
    )
