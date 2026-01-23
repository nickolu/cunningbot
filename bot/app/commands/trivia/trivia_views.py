"""Discord UI views and modals for trivia game interactions."""

import discord
from discord.ext import commands
from bot.app.utils.logger import get_logger

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
        from .trivia_submission_handler import submit_trivia_answer
        await submit_trivia_answer(
            self.bot, interaction, self.answer.value, self.guild_id
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
        # Parse game_id from the button's custom_id (for persistence)
        button = [item for item in self.children if isinstance(item, discord.ui.Button)][0]
        game_id = button.custom_id.split(":")[-1]

        # Show modal
        modal = TriviaAnswerModal(game_id, str(interaction.guild_id), self.bot)
        await interaction.response.send_modal(modal)


def register_persistent_trivia_views(bot: commands.Bot):
    """
    Register views for active games on bot startup.

    This ensures buttons continue to work after bot restarts.
    """
    from bot.app.app_state import get_all_guild_states

    all_guild_states = get_all_guild_states()

    registered_count = 0
    for guild_id_str, guild_state in all_guild_states.items():
        if guild_id_str == "global":
            continue

        if not isinstance(guild_state, dict):
            continue

        active_games = guild_state.get("active_trivia_games", {})

        for game_id in active_games.keys():
            view = TriviaQuestionView(game_id, guild_id_str, bot)
            bot.add_view(view)
            registered_count += 1

    logger.info(f"Registered {registered_count} persistent trivia views")
