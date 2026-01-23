"""
main.py
Entry point for the bot application.
"""

import os
import sys
import asyncio
import logging
import signal
from typing import Any, Callable
from discord.ext import commands
import discord
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("CunningBot")

# Intents
intents = discord.Intents.none()
intents.guilds = True
intents.messages = True
intents.message_content = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents)

async def load_cogs_from_dir(directory: str) -> None:
    print(f"Loading cogs from {directory}")
    base = os.path.dirname(__file__)
    path = os.path.join(base, directory)
    if not os.path.isdir(path):
        logger.warning(f"Cog directory not found: {path}")
        return
    for entry in os.listdir(path):
        entry_path = os.path.join(path, entry)
        if os.path.isdir(entry_path) and not entry.startswith("__"):
            # load python modules in command subdirectory
            for filename in os.listdir(entry_path):
                if filename.endswith(".py") and not filename.startswith("__"):
                    module_name = filename[:-3]
                    ext = f"bot.{directory.replace('/', '.')}.{entry}.{module_name}"
                    print(f'loading cog from {ext}')
                    try:
                        await bot.load_extension(ext)
                        logger.info(f"Loaded extension: {ext}")
                    except Exception as e:
                        logger.error(f"Failed to load extension {ext}: {e}")
        elif entry.endswith(".py") and not entry.startswith("__"):
            # legacy: load python modules directly in directory
            module_name = entry[:-3]
            ext = f"bot.{directory.replace('/', '.')}.{module_name}"
            print(f'loading cog from {ext}')
            try:
                await bot.load_extension(ext)
                logger.info(f"Loaded extension: {ext}")
            except Exception as e:
                logger.error(f"Failed to load extension {ext}: {e}")

@bot.event
async def on_ready() -> None:
    logger.info(f"Bot ready as {bot.user}")
    
    # Initialize task queue
    from bot.app.task_queue import get_task_queue
    task_queue = get_task_queue()
    await task_queue.start_worker()
    logger.info("Task queue initialized and worker started")

    # Register persistent trivia views
    from bot.app.commands.trivia.trivia_views import register_persistent_trivia_views
    register_persistent_trivia_views(bot)
    logger.info("Registered persistent trivia views")

    # Log local commands before sync
    local_cmds = [cmd.name for cmd in bot.tree.walk_commands()]
    logger.info(f"Local commands before sync: {local_cmds}")

    try:
        synced = await bot.tree.sync()
        logger.info(f"Synced global commands: {[cmd.name for cmd in synced]}")
        logger.info("Command tree synced globally")
        # Log all registered global app commands
        cmds = await bot.tree.fetch_commands()
        for cmd in cmds:
            logger.info(f"Registered command: {cmd.name} (type: {cmd.type})")
    except Exception as e:
        logger.error(f"Failed to sync command tree globally: {e}")

def handle_shutdown(loop: asyncio.AbstractEventLoop) -> Callable[[], asyncio.Task[Any]]:
    async def shutdown() -> None:
        logger.info("Shutting down gracefully...")
        
        # Stop task queue worker
        try:
            from bot.app.task_queue import get_task_queue
            task_queue = get_task_queue()
            await task_queue.stop_worker()
            logger.info("Task queue worker stopped")
        except Exception as e:
            logger.error(f"Error stopping task queue: {e}")
        
        await bot.close()
        for handler in logging.root.handlers:
            handler.flush()
    return lambda *args: asyncio.ensure_future(shutdown(), loop=loop)

async def main() -> None:
    await load_cogs_from_dir("app/commands")
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
