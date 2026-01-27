"""Discord UI views and modals for trivia game interactions."""

import discord
from discord.ext import commands
from bot.app.utils.logger import get_logger
from bot.app.commands.trivia.trivia_submission_handler import submit_trivia_answer

logger = get_logger()


class TriviaAnswerModal(discord.ui.Modal, title="Submit Trivia Answer"):
    """Modal dialog with text input for submitting trivia answers."""

    answer = discord.ui.TextInput(
        label="Your Answer",
        placeholder="Type your answer here...",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500
    )

    def __init__(self, game_id: str, guild_id: str, bot: commands.Bot):
        super().__init__()
        self.game_id = game_id
        self.guild_id = guild_id
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction):
        """Handle modal submission."""
        # CRITICAL: Defer IMMEDIATELY to prevent "This interaction failed"
        # Modal submissions have a strict 3-second window
        await interaction.response.defer(ephemeral=True)

        # Now process the submission (validation can take several seconds)
        await submit_trivia_answer(
            self.bot, interaction, self.answer.value, self.guild_id, self.game_id
        )


class TriviaQuestionView(discord.ui.View):
    """Persistent view with Submit Answer button for trivia questions."""

    def __init__(self, game_id: str, guild_id: str, bot: commands.Bot):
        super().__init__(timeout=None)  # Persistent view
        self.game_id = game_id
        self.guild_id = guild_id
        self.bot = bot

        # Create button with custom_id that includes game_id for persistence
        button = discord.ui.Button(
            label="Submit Answer",
            style=discord.ButtonStyle.primary,
            custom_id=f"trivia_answer:{game_id}",
            emoji="📝"
        )
        button.callback = self.answer_button_callback
        self.add_item(button)

    async def answer_button_callback(self, interaction: discord.Interaction):
        """Handle button click - shows the answer modal."""
        try:
            # Parse game_id from the button's custom_id (for persistence)
            button = [item for item in self.children if isinstance(item, discord.ui.Button)][0]
            game_id = button.custom_id.split(":")[-1]

            # Show modal
            modal = TriviaAnswerModal(game_id, str(interaction.guild_id), self.bot)
            await interaction.response.send_modal(modal)
        except Exception as e:
            logger.error(f"Error in trivia button callback: {e}", exc_info=True)
            # Try to send error message to user if we haven't responded yet
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "❌ An error occurred. Please try again or use `/answer` instead.",
                        ephemeral=True
                    )
            except Exception:
                # If we can't respond, log and move on
                logger.error("Could not send error message to user")


async def register_persistent_trivia_views(bot: commands.Bot):
    """
    Register views for active games on bot startup.

    This ensures buttons continue to work after bot restarts.
    """
    from bot.app.redis.trivia_store import TriviaRedisStore
    from bot.app.redis.client import get_redis_client

    redis_client = get_redis_client()
    store = TriviaRedisStore()

    registered_count = 0

    try:
        # Find all guild IDs with active trivia games by scanning Redis keys
        pattern = "trivia:*:games:active"
        cursor = 0
        guild_ids = set()

        # Use SCAN to find all matching keys without blocking Redis
        while True:
            cursor, keys = await redis_client.redis.scan(cursor, match=pattern, count=100)
            for key in keys:
                # Extract guild_id from key format: trivia:{guild_id}:games:active
                parts = key.split(":")
                if len(parts) >= 3:
                    guild_id = parts[1]
                    guild_ids.add(guild_id)

            if cursor == 0:
                break

        # Register views for all active games in each guild
        for guild_id_str in guild_ids:
            try:
                active_games = await store.get_active_games(guild_id_str)

                for game_id in active_games.keys():
                    view = TriviaQuestionView(game_id, guild_id_str, bot)
                    bot.add_view(view)
                    registered_count += 1
            except Exception as e:
                logger.error(f"Failed to register views for guild {guild_id_str}: {e}")

        logger.info(f"Registered {registered_count} persistent trivia views from Redis")

    except Exception as e:
        logger.error(f"Failed to register persistent trivia views: {e}")
        logger.info("Registered 0 persistent trivia views")
