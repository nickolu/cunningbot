"""
Help Command
Provides an overview of all bot commands and features.
"""

import discord
from discord import app_commands
from discord.ext import commands

from bot.app.utils.logger import get_logger

logger = get_logger()

# ---------------------------------------------------------------------------
# Help content — update this whenever commands or features change
# ---------------------------------------------------------------------------

HELP_PAGES = [
    # Page 1 — Chat & Image
    discord.Embed(
        title="CunningBot Help (1/5) — Chat & Images",
        color=discord.Color.blurple(),
    ).add_field(
        name="/chat",
        value=(
            "Chat with an LLM. Options:\n"
            "- `model` — choose from GPT-4o, GPT-4.1, o4-mini, and more (default: GPT-5.2)\n"
            "- `persona` — override the guild persona for this message\n"
            "- `private` — send response only to you\n"
            "- `context` — number of previous messages to include (default: 20)\n"
            "Requests are queued and processed in order."
        ),
        inline=False,
    ).add_field(
        name="/image",
        value=(
            "Generate or edit an image.\n"
            "- `prompt` — what to generate\n"
            "- `attachment` — existing image to edit\n"
            "- `model` — Gemini 2.5 Flash (default), Gemini 3 Pro, Nano Banana 2, ChatGPT-Image-Latest, etc.\n"
            "- `size` — auto, 1024×1024, 1536×1024, 1024×1536\n"
            "Requests are queued and processed in order."
        ),
        inline=False,
    ).add_field(
        name="/image-json",
        value=(
            "Generate an image using a structured JSON prompt (fine-grained parameter control). "
            "Supports the same models as `/image`."
        ),
        inline=False,
    ).add_field(
        name="Edit Images (context menu)",
        value=(
            "Right-click any message that contains images → **Apps → Edit Images**. "
            "Opens a modal where you enter an edit prompt and target size. "
            "Edits all images in the message using Gemini."
        ),
        inline=False,
    ).set_footer(text="Use /queue to check image/chat task queue status."),

    # Page 2 — Trivia
    discord.Embed(
        title="CunningBot Help (2/5) — Trivia",
        color=discord.Color.gold(),
    ).add_field(
        name="User commands",
        value=(
            "`/trivia answer` — submit an answer to the active trivia game in this thread\n"
            "`/answer` — shorthand for `/trivia answer`\n"
            "`/trivia status` — show the current game status\n"
            "`/trivia leaderboard` — this week's scores (optional category filter)\n"
            "`/trivia alltime` — all-time leaderboard from weekly snapshots\n"
            "`/trivia stats` — your personal stats broken down by category\n"
            "`/trivia list` — list all scheduled trivia games for this server\n"
            "**Submit Answer** (context menu) — right-click a trivia question → **Apps → Submit Answer**"
        ),
        inline=False,
    ).add_field(
        name="Admin commands",
        value=(
            "`/trivia register` — schedule recurring trivia in a channel\n"
            "`/trivia post` — post a trivia session immediately\n"
            "`/trivia enable` / `disable` / `delete` — manage registrations\n"
            "`/trivia close` — manually close an active game\n"
            "`/trivia configure_seeds` — set custom AI seed words for question generation\n"
            "`/trivia configure_method` — switch between OpenTrivia DB and AI-generated questions\n"
            "`/trivia clear_schedules` — delete schedules for a channel or whole server\n"
            "`/trivia clear_stats` / `reset` — wipe the leaderboard (requires confirmation)"
        ),
        inline=False,
    ).add_field(
        name="Scoring",
        value=(
            "Points are difficulty-weighted. Answering multiple questions in a session "
            "earns combo bonuses. Weekly scores are snapshotted and reset every Monday."
        ),
        inline=False,
    ),

    # Page 3 — News & Weather
    discord.Embed(
        title="CunningBot Help (3/5) — News & Weather",
        color=discord.Color.green(),
    ).add_field(
        name="/news — RSS feed management",
        value=(
            "**User:**\n"
            "`/news list` — list all feeds in this channel\n"
            "`/news preview` — preview the latest item from a feed\n\n"
            "**Admin:**\n"
            "`/news add` — add an RSS feed to this channel\n"
            "`/news enable` / `disable` / `remove` / `remove-all` — manage feeds\n"
            "`/news set-mode` — `summary` (AI-aggregated at scheduled times) or `direct` (immediate)\n"
            "`/news set-filter` — AI filter instructions (e.g. \"only San Diego articles\")\n"
            "`/news set-schedule` — customize summary posting times\n"
            "`/news set-limits` — article processing pipeline limits\n"
            "`/news set-window` — story deduplication time window (6–168 hours)\n"
            "`/news diversity` — feed diversity strategy (balanced / proportional / disabled)\n"
            "`/news reset` — clear seen-items history (causes articles to re-post)\n"
            "`/news summary` — generate an on-demand AI news summary"
        ),
        inline=False,
    ).add_field(
        name="/weather — Forecasts",
        value=(
            "**User:**\n"
            "`/weather forecast` — on-demand forecast for any US ZIP code (1–16 days)\n"
            "`/weather history` — historical weather data (up to 92-day range)\n"
            "`/weather list` — show the weather schedule for this channel\n\n"
            "**Admin:**\n"
            "`/weather schedule` — schedule daily weather posts to this channel\n"
            "`/weather unschedule` — remove the weather schedule from this channel\n\n"
            "Forecasts include an AI-generated TV-meteorologist intro. "
            "A **Show Details** button expands the full hourly/daily table."
        ),
        inline=False,
    ),

    # Page 4 — Polls, Dice, GIFs, Reddit, Personas
    discord.Embed(
        title="CunningBot Help (4/5) — Polls, Dice, GIFs & More",
        color=discord.Color.orange(),
    ).add_field(
        name="/poll & /poll-results",
        value=(
            "`/poll` — create an emoji-reaction poll with up to 10 options\n"
            "`/poll-results` — display the current vote counts for a poll"
        ),
        inline=False,
    ).add_field(
        name="/roll",
        value=(
            "Roll dice using standard notation: `1d20`, `4d6`, `3d6+2d4*10`, etc.\n"
            "Supports up to 100 dice with up to 1000 sides. Defaults to `1d20`."
        ),
        inline=False,
    ).add_field(
        name="/af query — Animation Factory GIFs",
        value=(
            "Search the Animation Factory GIF library. Returns an interactive preview "
            "with **Prev / Next / Send / Cancel** buttons (only visible to you). "
            "Supports `clear`, `white`, and `black` style variants."
        ),
        inline=False,
    ).add_field(
        name="/r — Reddit links",
        value="Quickly share a subreddit link: `/r python` → https://reddit.com/r/python",
        inline=False,
    ).add_field(
        name="/persona",
        value=(
            "`/persona default [persona]` — set or view the guild's default chat persona\n"
            "`/persona show` — list all available personas with descriptions\n\n"
            "Built-in personas: `discord_user`, `cat`, `helpful_assistant`, "
            "`sarcastic_jerk`, `homer_simpson`"
        ),
        inline=False,
    ),

    # Page 5 — Lunch Rotation, Bot Updates, Queue
    discord.Embed(
        title="CunningBot Help (5/5) — Lunch Rotation, Notifications & Queue",
        color=discord.Color.red(),
    ).add_field(
        name="/lunchboyz — Bi-weekly lunch rotation",
        value=(
            "`/lunchboyz setup` — configure the rotation (participants, frequency, start date, timezone)\n"
            "`/lunchboyz status` — show who's up, upcoming event, and deadline\n"
            "`/lunchboyz plan` — announce the upcoming lunch (location, date, time, notes)\n"
            "`/lunchboyz skip` — skip the current person and advance the rotation\n"
            "`/lunchboyz advance` — manually advance to the next person\n\n"
            "An hourly background task sends reminders in the configured channel."
        ),
        inline=False,
    ).add_field(
        name="/bot-updates — Restart notifications (Admin)",
        value=(
            "`/bot-updates register` — register this channel for restart/shutdown notifications\n"
            "`/bot-updates unregister` — unregister this channel\n"
            "`/bot-updates list` — list all registered notification channels\n"
            "`/bot-updates test` — send a test notification to this channel"
        ),
        inline=False,
    ).add_field(
        name="/queue",
        value=(
            "Check the current task queue status: number of queued tasks, active tasks, "
            "completed tasks, and worker state. `/chat` and `/image` requests are queued "
            "and processed in order (max 100 in queue)."
        ),
        inline=False,
    ).add_field(
        name="/help",
        value="Show this help message.",
        inline=False,
    ),
]


class HelpCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="help", description="Show all bot commands and features")
    async def help_command(self, interaction: discord.Interaction) -> None:
        """Send paginated help embeds describing every bot capability."""
        try:
            # Send first page as the interaction response, rest as follow-ups
            await interaction.response.send_message(embed=HELP_PAGES[0], ephemeral=True)
            for page in HELP_PAGES[1:]:
                await interaction.followup.send(embed=page, ephemeral=True)
        except Exception as e:
            logger.error(f"Error sending help: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Sorry, I couldn't send the help message. Please try again later.",
                    ephemeral=True,
                )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HelpCog(bot))
