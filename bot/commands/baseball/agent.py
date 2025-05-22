import discord
from discord import app_commands
from discord.ext import commands

from bot.agents.baseball_agent import BaseballAgent
from bot.api.discord.utils import format_response_with_interaction_user_message

class BaseballAgentCog(commands.Cog):
    """
    Agent command group for API-sports endpoints.
    """
    agent_group = app_commands.Group(name="baseball", description="Baseball API commands")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @agent_group.command(name="agent", description="Baseball API commands")
    @app_commands.describe(
        prompt="The prompt to send to the agent."
    )
    async def agent(self, interaction: discord.Interaction, prompt: str) -> None:
        await interaction.response.defer()
        agent = BaseballAgent()
        result = await agent.run(prompt)
        await interaction.followup.send(format_response_with_interaction_user_message(result, interaction, prompt))

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BaseballAgentCog(bot))
