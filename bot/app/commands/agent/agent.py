"""Slash commands for registering / managing channel agents.

/agent register  — activate an always-on agent in the current channel
/agent unregister — remove the agent
/agent status    — show current config
/agent configure — update settings
/agent pause     — temporarily disable
/agent resume    — re-enable
"""

import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional

from bot.app.redis.agent_store import AgentRedisStore, DEFAULT_AGENT_CONFIG
from bot.domain.agent.agent_tools import TOOL_SCHEMAS
from bot.app.utils.logger import get_logger

logger = get_logger()

# Build the list of available tool keys for the choices decorator
AVAILABLE_TOOLS = list(TOOL_SCHEMAS.keys())


class AgentCog(commands.Cog):
    """Manages channel agent registrations."""

    agent_group = app_commands.Group(
        name="agent", description="Manage the always-on AI agent in this channel"
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.store = AgentRedisStore()

    # ------------------------------------------------------------------
    # /agent register
    # ------------------------------------------------------------------
    @agent_group.command(
        name="register",
        description="Activate an always-on AI agent in this channel",
    )
    @app_commands.describe(
        persona="Agent personality (optional, uses server default if omitted)",
        model="LLM model to use (default: gpt-4o)",
        context_window="Number of previous messages the agent sees (default: 30)",
        cooldown="Seconds between automatic responses (default: 5)",
    )
    @app_commands.choices(
        persona=[
            app_commands.Choice(name="A discord user", value="discord_user"),
            app_commands.Choice(name="Cat", value="cat"),
            app_commands.Choice(name="Helpful Assistant", value="helpful_assistant"),
            app_commands.Choice(name="Sarcastic Jerk", value="sarcastic_jerk"),
            app_commands.Choice(name="Homer Simpson", value="homer_simpson"),
        ]
    )
    @app_commands.choices(
        model=[
            app_commands.Choice(name="gpt-4o (default, balanced)", value="gpt-4o"),
            app_commands.Choice(name="gpt-4o-mini (cheaper)", value="gpt-4o-mini"),
            app_commands.Choice(name="gpt-4.1 (newer)", value="gpt-4.1"),
            app_commands.Choice(name="gpt-4.1-mini (newer, cheaper)", value="gpt-4.1-mini"),
            app_commands.Choice(name="gpt-5.2 (smartest, expensive)", value="gpt-5.2"),
        ]
    )
    async def register(
        self,
        interaction: discord.Interaction,
        persona: Optional[str] = None,
        model: Optional[str] = None,
        context_window: Optional[int] = None,
        cooldown: Optional[int] = None,
    ) -> None:
        guild_id = str(interaction.guild_id)
        channel_id = str(interaction.channel_id)

        # Check if already registered
        existing = await self.store.get_agent_config(guild_id, channel_id)
        if existing and existing.get("enabled"):
            await interaction.response.send_message(
                "An agent is already active in this channel. "
                "Use `/agent configure` to change settings or `/agent unregister` to remove it.",
                ephemeral=True,
            )
            return

        config = {
            "enabled": True,
            "persona": persona,
            "model": model or DEFAULT_AGENT_CONFIG["model"],
            "tools": AVAILABLE_TOOLS,  # All tools enabled by default
            "context_window": context_window or DEFAULT_AGENT_CONFIG["context_window"],
            "cooldown_seconds": cooldown or DEFAULT_AGENT_CONFIG["cooldown_seconds"],
            "max_responses_per_minute": DEFAULT_AGENT_CONFIG["max_responses_per_minute"],
        }

        await self.store.register_agent(
            guild_id, channel_id, config, str(interaction.user.id)
        )

        tools_text = ", ".join(AVAILABLE_TOOLS)
        persona_text = persona or "server default"
        embed = discord.Embed(
            title="Agent Activated",
            description=f"An AI agent is now active in this channel.",
            color=0x00CC66,
        )
        embed.add_field(name="Model", value=config["model"], inline=True)
        embed.add_field(name="Persona", value=persona_text, inline=True)
        embed.add_field(name="Context Window", value=str(config["context_window"]), inline=True)
        embed.add_field(name="Cooldown", value=f"{config['cooldown_seconds']}s", inline=True)
        embed.add_field(name="Tools", value=tools_text, inline=False)
        embed.set_footer(text="The agent will respond to all messages in this channel. Use /agent unregister to remove.")

        await interaction.response.send_message(embed=embed)

    # ------------------------------------------------------------------
    # /agent unregister
    # ------------------------------------------------------------------
    @agent_group.command(
        name="unregister",
        description="Remove the AI agent from this channel",
    )
    async def unregister(self, interaction: discord.Interaction) -> None:
        guild_id = str(interaction.guild_id)
        channel_id = str(interaction.channel_id)

        removed = await self.store.unregister_agent(guild_id, channel_id)
        if removed:
            await interaction.response.send_message("Agent removed from this channel.")
        else:
            await interaction.response.send_message(
                "No agent is registered in this channel.", ephemeral=True
            )

    # ------------------------------------------------------------------
    # /agent status
    # ------------------------------------------------------------------
    @agent_group.command(
        name="status",
        description="Show the current agent configuration for this channel",
    )
    async def status(self, interaction: discord.Interaction) -> None:
        guild_id = str(interaction.guild_id)
        channel_id = str(interaction.channel_id)

        config = await self.store.get_agent_config(guild_id, channel_id)
        if config is None:
            await interaction.response.send_message(
                "No agent registered in this channel. Use `/agent register` to set one up.",
                ephemeral=True,
            )
            return

        enabled = config.get("enabled", False)
        status_text = "Active" if enabled else "Paused"
        color = 0x00CC66 if enabled else 0xFFAA00

        embed = discord.Embed(
            title=f"Agent Status: {status_text}",
            color=color,
        )
        embed.add_field(name="Model", value=config.get("model", "?"), inline=True)
        embed.add_field(name="Persona", value=config.get("persona") or "server default", inline=True)
        embed.add_field(name="Context Window", value=str(config.get("context_window", 30)), inline=True)
        embed.add_field(name="Cooldown", value=f"{config.get('cooldown_seconds', 5)}s", inline=True)
        embed.add_field(name="Rate Limit", value=f"{config.get('max_responses_per_minute', 10)}/min", inline=True)
        embed.add_field(name="Tools", value=", ".join(config.get("tools", [])), inline=False)
        embed.set_footer(text=f"Registered by user {config.get('registered_by', '?')}")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # /agent configure
    # ------------------------------------------------------------------
    @agent_group.command(
        name="configure",
        description="Update agent settings for this channel",
    )
    @app_commands.describe(
        persona="Change the agent's personality",
        model="Change the LLM model",
        context_window="Number of previous messages the agent sees",
        cooldown="Seconds between automatic responses",
        max_per_minute="Maximum responses per minute",
    )
    @app_commands.choices(
        persona=[
            app_commands.Choice(name="A discord user", value="discord_user"),
            app_commands.Choice(name="Cat", value="cat"),
            app_commands.Choice(name="Helpful Assistant", value="helpful_assistant"),
            app_commands.Choice(name="Sarcastic Jerk", value="sarcastic_jerk"),
            app_commands.Choice(name="Homer Simpson", value="homer_simpson"),
        ]
    )
    @app_commands.choices(
        model=[
            app_commands.Choice(name="gpt-4o (default, balanced)", value="gpt-4o"),
            app_commands.Choice(name="gpt-4o-mini (cheaper)", value="gpt-4o-mini"),
            app_commands.Choice(name="gpt-4.1 (newer)", value="gpt-4.1"),
            app_commands.Choice(name="gpt-4.1-mini (newer, cheaper)", value="gpt-4.1-mini"),
            app_commands.Choice(name="gpt-5.2 (smartest, expensive)", value="gpt-5.2"),
        ]
    )
    async def configure(
        self,
        interaction: discord.Interaction,
        persona: Optional[str] = None,
        model: Optional[str] = None,
        context_window: Optional[int] = None,
        cooldown: Optional[int] = None,
        max_per_minute: Optional[int] = None,
    ) -> None:
        guild_id = str(interaction.guild_id)
        channel_id = str(interaction.channel_id)

        updates = {}
        if persona is not None:
            updates["persona"] = persona
        if model is not None:
            updates["model"] = model
        if context_window is not None:
            updates["context_window"] = max(1, min(context_window, 100))
        if cooldown is not None:
            updates["cooldown_seconds"] = max(0, min(cooldown, 300))
        if max_per_minute is not None:
            updates["max_responses_per_minute"] = max(1, min(max_per_minute, 60))

        if not updates:
            await interaction.response.send_message(
                "No changes specified. Provide at least one option to update.",
                ephemeral=True,
            )
            return

        success = await self.store.update_agent_config(guild_id, channel_id, updates)
        if not success:
            await interaction.response.send_message(
                "No agent registered in this channel. Use `/agent register` first.",
                ephemeral=True,
            )
            return

        changes = ", ".join(f"**{k}** = `{v}`" for k, v in updates.items())
        await interaction.response.send_message(f"Agent updated: {changes}")

    # ------------------------------------------------------------------
    # /agent pause & /agent resume
    # ------------------------------------------------------------------
    @agent_group.command(
        name="pause",
        description="Temporarily pause the agent without removing its configuration",
    )
    async def pause(self, interaction: discord.Interaction) -> None:
        guild_id = str(interaction.guild_id)
        channel_id = str(interaction.channel_id)

        success = await self.store.update_agent_config(
            guild_id, channel_id, {"enabled": False}
        )
        if success:
            await interaction.response.send_message("Agent paused. Use `/agent resume` to reactivate.")
        else:
            await interaction.response.send_message(
                "No agent registered in this channel.", ephemeral=True
            )

    @agent_group.command(
        name="resume",
        description="Resume a paused agent",
    )
    async def resume(self, interaction: discord.Interaction) -> None:
        guild_id = str(interaction.guild_id)
        channel_id = str(interaction.channel_id)

        success = await self.store.update_agent_config(
            guild_id, channel_id, {"enabled": True}
        )
        if success:
            await interaction.response.send_message("Agent resumed and listening.")
        else:
            await interaction.response.send_message(
                "No agent registered in this channel.", ephemeral=True
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AgentCog(bot))
