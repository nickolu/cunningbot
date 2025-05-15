"""
main.py
Entry point for the bot application.
"""

import os
import sys
import asyncio
import logging
import signal
from discord.ext import commands
import discord
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("manchatbot")

# Intents
intents = discord.Intents.none()
intents.guilds = True
intents.messages = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

async def load_cogs_from_dir(directory: str):
    base = os.path.dirname(__file__)
    path = os.path.join(base, directory)
    if not os.path.isdir(path):
        logger.warning(f"Cog directory not found: {path}")
        return
    for filename in os.listdir(path):
        if filename.endswith(".py") and not filename.startswith("__"):
            ext = f"bot.{directory}.{filename[:-3]}"
            try:
                await bot.load_extension(ext)
                logger.info(f"Loaded extension: {ext}")
            except Exception as e:
                logger.error(f"Failed to load extension {ext}: {e}")

@bot.event
async def on_ready():
    logger.info(f"Bot ready as {bot.user} (ID: {bot.user.id})")
    guild_id = os.getenv("GUILD_ID")
    if guild_id:
        try:
            guild = discord.Object(id=int(guild_id))
            await bot.tree.sync(guild=guild)
            logger.info(f"Command tree synced to guild {guild_id}")
        except Exception as e:
            logger.error(f"Failed to sync command tree to guild {guild_id}: {e}")
    else:
        try:
            await bot.tree.sync()
            logger.info("Command tree synced globally")
        except Exception as e:
            logger.error(f"Failed to sync command tree globally: {e}")

def handle_shutdown(loop):
    async def shutdown():
        logger.info("Shutting down gracefully...")
        await bot.close()
        for handler in logging.root.handlers:
            handler.flush()
    return lambda *_: asyncio.ensure_future(shutdown(), loop=loop)

async def main():
    await load_cogs_from_dir("commands")
    await load_cogs_from_dir("listeners")
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.error("DISCORD_TOKEN environment variable not set.")
        sys.exit(1)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_shutdown(loop))
        except NotImplementedError:
            # Windows compatibility
            pass
    try:
        await bot.start(token)
    except Exception as e:
        logger.error(f"Bot exited with error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
