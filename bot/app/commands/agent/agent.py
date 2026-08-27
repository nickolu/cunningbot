"""Slash commands for registering / managing channel agents.

/agent register  — activate an always-on agent in the current channel
/agent unregister — remove the agent
/agent status    — show current config
/agent configure — update settings
/agent pause     — temporarily disable
/agent resume    — re-enable

Registration only governs whether the agent joins in on its own. The bot
answers in any channel when addressed by name, @mention, or reply — see
bot/app/commands/agent/agent_listener.py.
"""

import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional

from bot.app.redis.agent_store import AgentRedisStore, DEFAULT_AGENT_CONFIG
from bot.app.utils.model_choices import model_choices
from bot.domain.llm.models import DEFAULT_AGENT_MODEL
from bot.domain.agent.tools.registry import TOOL_SCHEMAS
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
        self._store: Optional[AgentRedisStore] = None

    @property
    def store(self) -> AgentRedisStore:
        if self._store is None:
            self._store = AgentRedisStore()
        return self._store

    # ------------------------------------------------------------------
    # /agent register
    # ------------------------------------------------------------------
    @agent_group.command(
        name="register",
        description="Activate an always-on AI agent in this channel",
    )
    @app_commands.describe(
        persona="Personality/system prompt for the agent (free text, optional)",
        model="LLM model to use (default: %s)" % DEFAULT_AGENT_MODEL,
        context_window="Number of previous messages the agent sees (default: 30)",
        cooldown="Seconds between automatic responses (default: 5)",
    )
    @app_commands.choices(model=model_choices(DEFAULT_AGENT_MODEL))
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
        embed.add_field(name="Response Mode", value="smart", inline=True)
        embed.set_footer(text="The agent uses smart triggering — it responds when addressed. Use /agent configure to change.")

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
            await interaction.response.send_message(
                "Agent removed from this channel. It still answers when you "
                "say its name, @mention it, or reply to it."
            )
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
                "No agent registered in this channel — it answers here only when you "
                "say its name, @mention it, or reply to it. Use `/agent register` to have "
                "it join in on its own.",
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
        persona_display = config.get("persona") or "server default"
        if len(persona_display) > 100:
            persona_display = persona_display[:97] + "..."
        embed.add_field(name="Persona", value=persona_display, inline=True)
        embed.add_field(name="Context Window", value=str(config.get("context_window", 30)), inline=True)
        embed.add_field(name="Cooldown", value=f"{config.get('cooldown_seconds', 5)}s", inline=True)
        embed.add_field(name="Rate Limit", value=f"{config.get('max_responses_per_minute', 10)}/min", inline=True)
        embed.add_field(name="Response Mode", value=config.get("response_mode", "smart"), inline=True)
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
        persona="Change the agent's personality (free text)",
        model="Change the LLM model",
        context_window="Number of previous messages the agent sees",
        cooldown="Seconds between automatic responses",
        max_per_minute="Maximum responses per minute",
        response_mode="How the agent decides when to respond",
    )
    @app_commands.choices(model=model_choices(DEFAULT_AGENT_MODEL))
    @app_commands.choices(
        response_mode=[
            app_commands.Choice(name="Smart (LLM decides, default)", value="smart"),
            app_commands.Choice(name="Strict (only when addressed directly)", value="strict"),
            app_commands.Choice(name="Always (respond to everything)", value="always"),
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
        response_mode: Optional[str] = None,
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
        if response_mode is not None:
            updates["response_mode"] = response_mode

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
    # /agent tool
    # ------------------------------------------------------------------
    @agent_group.command(
        name="tool",
        description="Turn one of the agent's tools on or off in this channel",
    )
    @app_commands.describe(
        tool="Which tool to change",
        state="Whether the agent may use it here",
    )
    @app_commands.choices(
        tool=[
            app_commands.Choice(name=key, value=key) for key in AVAILABLE_TOOLS
        ],
        state=[
            app_commands.Choice(name="Enable", value="enable"),
            app_commands.Choice(name="Disable", value="disable"),
        ],
    )
    async def tool(
        self,
        interaction: discord.Interaction,
        tool: str,
        state: str,
    ) -> None:
        """Per-channel tool toggle.

        `/agent configure` has no tools option, so before this the only way to
        change a channel's tool list was to unregister and re-register, which
        discards its model, persona, and window. Opt-in tools were unreachable
        that way entirely.
        """
        guild_id = str(interaction.guild_id)
        channel_id = str(interaction.channel_id)

        config = await self.store.get_agent_config(guild_id, channel_id)
        if config is None:
            await interaction.response.send_message(
                "No agent registered in this channel. Use `/agent register` first.",
                ephemeral=True,
            )
            return

        tools = list(config.get("tools", []))
        enabling = state == "enable"

        if enabling and tool in tools:
            await interaction.response.send_message(
                f"`{tool}` is already enabled here.", ephemeral=True
            )
            return
        if not enabling and tool not in tools:
            await interaction.response.send_message(
                f"`{tool}` is already disabled here.", ephemeral=True
            )
            return

        if enabling:
            tools.append(tool)
        else:
            tools.remove(tool)

        await self.store.update_agent_config(guild_id, channel_id, {"tools": tools})
        await interaction.response.send_message(
            f"{'Enabled' if enabling else 'Disabled'} `{tool}` in this channel."
        )

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
            await interaction.response.send_message(
                "Agent paused — it will not answer here, even if addressed. "
                "Use `/agent resume` to reactivate."
            )
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
