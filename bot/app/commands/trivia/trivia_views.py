"""Discord UI views and modals for trivia game interactions."""

import discord
from discord.ext import commands
from bot.app.utils.logger import get_logger
from bot.app.commands.trivia.trivia_submission_handler import submit_trivia_answer

logger = get_logger()


class TriviaAnswerModal(discord.ui.Modal, title="Submit Trivia Answer"):
    """Modal dialog with text input for submitting trivia answers."""

    def __init__(self, game_id: str, guild_id: str, bot: commands.Bot, question: str = None):
        super().__init__()
        self.game_id = game_id
        self.guild_id = guild_id
        self.bot = bot

        # Create the answer input with the question as placeholder if provided
        placeholder = question if question else "Type your answer here..."
        # Discord has a 100 character limit for placeholders
        if len(placeholder) > 100:
            placeholder = placeholder[:97] + "..."

        self.answer = discord.ui.TextInput(
            label="Your Answer",
            placeholder=placeholder,
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=500
        )
        self.add_item(self.answer)

    async def on_submit(self, interaction: discord.Interaction):
        """Handle modal submission."""
        # CRITICAL: Defer IMMEDIATELY to prevent "This interaction failed"
        # Modal submissions have a strict 3-second window
        await interaction.response.defer(ephemeral=True)

        # Now process the submission (validation can take several seconds)
        await submit_trivia_answer(
            self.bot, interaction, self.answer.value, self.guild_id, self.game_id
        )
