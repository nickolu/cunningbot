# Adding a slash command

## Where it goes

`bot/app/commands/<feature>/<name>.py`, plus an empty `__init__.py` in the
directory. `bot/main.py` scans `bot/app/commands/` and loads every `.py` inside
a subdirectory as an extension — no registration list to edit. The file **must**
define `async def setup(bot: commands.Bot) -> None`.

A failure to import or a missing `setup` is logged and skipped; the bot starts
anyway. Always check startup logs after adding a cog.

## Shape

```python
"""One-line description of the feature."""

import discord
from discord import app_commands
from discord.ext import commands

from bot.app.redis.serialization import guild_id_to_str
from bot.app.utils.logger import get_logger
from bot.domain.<feature>.<feature>_service import do_the_thing

logger = get_logger()


class FeatureCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # Grouped commands (/feature sub) — a class attribute, not decorated
    feature = app_commands.Group(name="feature", description="...")

    @feature.command(name="show", description="Show the thing")
    @app_commands.describe(target="What to show")
    async def show(self, interaction: discord.Interaction, target: str) -> None:
        await interaction.response.defer()          # ephemeral=True for private
        try:
            result = await do_the_thing(guild_id_to_str(interaction.guild_id), target)
        except Exception as e:
            logger.error(f"/feature show failed: {e}")
            await interaction.followup.send(f"Couldn't do that: {e}", ephemeral=True)
            return

        embed = discord.Embed(title="…", description=result, color=0x3498DB)
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(FeatureCog(bot))
```

A single ungrouped command uses `@app_commands.command(...)` directly on the
method instead of a `Group`.

## Rules that bite

- **Always `defer()` first** if anything downstream touches the network. Discord
  kills the interaction after 3 seconds. After deferring, respond only via
  `interaction.followup.send`.
- **25 options max per command**, and 25 choices max per option. `/image-json`
  is at the ceiling — new parameters there need the `custom_*` key/value escape
  hatch, not a new option.
- **`ephemeral=True`** for anything configuration-shaped, noisy, or user-specific.
- **Admin-gated actions** use `@app_commands.checks.has_permissions(administrator=True)`
  — see `/weather` setup for the pattern.
- **Long or expensive LLM work** should go through `bot/app/task_queue.py` the
  way `/chat` and `/image` do, so it doesn't block the gateway.
- **Commands sync on startup.** A renamed or newly grouped command can take a
  few minutes to appear in the Discord client.

## Then

1. Add it to `HELP_PAGES` in `bot/app/commands/help.py` — mandatory.
2. Add an integration test alongside `tests/test_*_command_integration.py`; those
   mock the interaction and assert on what was sent.
3. Update the command table in `README.md` if it's a headline feature.
