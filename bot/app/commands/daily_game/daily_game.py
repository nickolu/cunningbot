import discord
from discord import app_commands
from discord.ext import commands
from typing import Any, Optional
from urllib.parse import urlparse

from bot.app.app_state import (
    set_state_value_from_interaction,
    get_state_value_from_interaction,
)
from bot.api.discord.thread_analyzer import ThreadAnalyzer
from bot.domain.daily_game.daily_game_stats_service import DailyGameStatsService

MINUTE_CHOICES = [0, 10, 20, 30, 40, 50]


def _is_valid_url(url: str) -> bool:
    """Simple URL validation."""
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


class DailyGameCog(commands.Cog):
    """Cog for registering daily game reminders."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.thread_analyzer = ThreadAnalyzer(bot)

    daily_game = app_commands.Group(
        name="daily-game", description="Manage daily scheduled game reminders."
    )

    @daily_game.command(name="register", description="Register a daily game for this channel.")
    @app_commands.describe(
        name="The short name of the game (e.g. framed.wtf)",
        link="A URL players will visit to play the game",
        hour="Hour of day (0–23) to post the game link (Pacific time)",
        minute="Minute of the hour (in increments of 10) to post the game link (Pacific time)",
    )
    @app_commands.choices(
        minute=[app_commands.Choice(name=f"{m:02d}", value=m) for m in MINUTE_CHOICES]
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def register(
        self,
        interaction: discord.Interaction,
        name: str,
        link: str,
        hour: app_commands.Range[int, 0, 23],
        minute: int,
    ) -> None:
        """Registers/updates a daily game reminder for the guild and channel."""
        # Extra safety: confirm minute is in our allowed list (should always be true via choices).
        if minute not in MINUTE_CHOICES:
            await interaction.response.send_message(
                f"Minute must be one of {MINUTE_CHOICES}.", ephemeral=True
            )
            return

        if not _is_valid_url(link):
            await interaction.response.send_message(
                "Please provide a valid URL starting with http:// or https://", ephemeral=True
            )
            return

        # Fetch current games dict
        games: dict[str, Any] | None = get_state_value_from_interaction(
            "daily_games", interaction.guild_id
        )
        if games is None:
            games = {}

        # Ensure uniqueness: if game exists in another channel, error
        if name in games and games[name]["channel_id"] != interaction.channel_id:
            await interaction.response.send_message(
                f"⚠️ A game with the name '{name}' is already registered in another channel. Please choose a different name or unregister the existing one first.",
                ephemeral=True,
            )
            return

        # Build/overwrite game info
        game_info: dict[str, Any] = {
            "name": name,
            "link": link,
            "hour": int(hour),
            "minute": int(minute),
            "channel_id": interaction.channel_id,
            "enabled": True,
        }

        games[name] = game_info

        # Save back to state
        set_state_value_from_interaction("daily_games", games, interaction.guild_id)

        await interaction.response.send_message(
            f"✅ Daily game **{name}** registered! I will post the link <{link}> to {interaction.channel.mention} every day at {int(hour):02d}:{int(minute):02d}.",
            ephemeral=True,
        )

    @daily_game.command(name="enable", description="Enable a registered daily game.")
    @app_commands.describe(name="The name of the registered game to enable")
    @app_commands.checks.has_permissions(administrator=True)
    async def enable_game(self, interaction: discord.Interaction, name: str) -> None:
        games = get_state_value_from_interaction("daily_games", interaction.guild_id) or {}

        if name not in games:
            await interaction.response.send_message(
                f"No registered game named '{name}' found for this guild.", ephemeral=True
            )
            return

        games[name]["enabled"] = True
        set_state_value_from_interaction("daily_games", games, interaction.guild_id)
        await interaction.response.send_message(
            f"✅ The game '{name}' has been enabled.", ephemeral=True
        )

    @daily_game.command(name="disable", description="Disable a registered daily game.")
    @app_commands.describe(name="The name of the registered game to disable")
    @app_commands.checks.has_permissions(administrator=True)
    async def disable_game(self, interaction: discord.Interaction, name: str) -> None:
        games = get_state_value_from_interaction("daily_games", interaction.guild_id) or {}

        if name not in games:
            await interaction.response.send_message(
                f"No registered game named '{name}' found for this guild.", ephemeral=True
            )
            return

        games[name]["enabled"] = False
        set_state_value_from_interaction("daily_games", games, interaction.guild_id)
        await interaction.response.send_message(
            f"🚫 The game '{name}' has been disabled.", ephemeral=True
        )

    @daily_game.command(name="delete", description="Delete a registered daily game completely.")
    @app_commands.describe(name="The name of the registered game to delete")
    @app_commands.checks.has_permissions(administrator=True)
    async def delete_game(self, interaction: discord.Interaction, name: str) -> None:
        games = get_state_value_from_interaction("daily_games", interaction.guild_id) or {}

        if name not in games:
            await interaction.response.send_message(
                f"No registered game named '{name}' found for this guild.", ephemeral=True
            )
            return

        # Store game info for confirmation message
        game_info = games[name]
        channel_id = game_info.get("channel_id")
        
        # Delete the game from the dictionary
        del games[name]
        
        # Save back to state
        set_state_value_from_interaction("daily_games", games, interaction.guild_id)
        
        # Confirmation message
        channel_mention = f"<#{channel_id}>" if channel_id else "Unknown channel"
        await interaction.response.send_message(
            f"🗑️ Daily game **{name}** has been deleted completely. It will no longer post to {channel_mention}.",
            ephemeral=True
        )

    @daily_game.command(name="list", description="List all registered daily games for this guild.")
    async def list_games(self, interaction: discord.Interaction) -> None:
        games = get_state_value_from_interaction("daily_games", interaction.guild_id) or {}

        if not games:
            await interaction.response.send_message(
                "No daily games are currently registered for this guild.", ephemeral=True
            )
            return

        # Build a formatted list of games
        game_list = []
        for name, game_info in games.items():
            status = "✅ Enabled" if game_info.get("enabled", True) else "🚫 Disabled"
            channel_id = game_info.get("channel_id")
            channel_mention = f"<#{channel_id}>" if channel_id else "Unknown channel"
            hour = game_info.get("hour", 0)
            minute = game_info.get("minute", 0)
            link = game_info.get("link", "No link")
            
            game_entry = (
                f"**{name}**\n"
                f"  • Status: {status}\n"
                f"  • Channel: {channel_mention}\n"
                f"  • Time: {hour:02d}:{minute:02d} Pacific\n"
                f"  • Link: <{link}>\n"
            )
            game_list.append(game_entry)

        # Create embed for better formatting
        embed = discord.Embed(
            title="📅 Registered Daily Games",
            description="\n".join(game_list),
            color=0x00ff00 if any(g.get("enabled", True) for g in games.values()) else 0xff0000
        )
        
        embed.set_footer(text=f"Total games: {len(games)}")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @daily_game.command(name="preview", description="Preview what a daily game message will look like.")
    @app_commands.describe(name="The name of the registered game to preview")
    async def preview_game(self, interaction: discord.Interaction, name: str) -> None:
        games = get_state_value_from_interaction("daily_games", interaction.guild_id) or {}

        if name not in games:
            await interaction.response.send_message(
                f"No registered game named '{name}' found for this guild.", ephemeral=True
            )
            return

        game = games[name]
        
        # Generate the same message format that the poster will use
        preview_msg = f"It's time for your daily **{game['name']}**! Play here: <{game['link']}>"
        
        # Create an embed to show the preview
        embed = discord.Embed(
            title="🔍 Daily Game Message Preview",
            description=f"Here's what the message will look like when posted:",
            color=0x0099ff
        )
        
        # Add the preview message as a field
        embed.add_field(
            name="Message Content",
            value=preview_msg,
            inline=False
        )
        
        # Add game details
        status = "✅ Enabled" if game.get("enabled", True) else "🚫 Disabled"
        channel_id = game.get("channel_id")
        channel_mention = f"<#{channel_id}>" if channel_id else "Unknown channel"
        hour = game.get("hour", 0)
        minute = game.get("minute", 0)
        
        embed.add_field(
            name="Game Details",
            value=(
                f"**Status:** {status}\n"
                f"**Channel:** {channel_mention}\n"
                f"**Time:** {hour:02d}:{minute:02d} Pacific\n"
                f"**Thread:** A new thread will be created for this message"
            ),
            inline=False
        )
        
        embed.set_footer(text="This is just a preview - no actual message will be posted.")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @daily_game.command(name="stats", description="Show participation statistics for a daily game.")
    @app_commands.describe(
        name="The name of the registered game to analyze",
        start_date="Start date (UTC timestamp or ISO format, optional - defaults to 30 days ago)",
        end_date="End date (UTC timestamp or ISO format, optional - defaults to today)"
    )
    async def stats(
        self, 
        interaction: discord.Interaction, 
        name: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> None:
        """Generate participation statistics for a daily game."""
        # Defer the response as this might take some time
        await interaction.response.defer()
        
        try:
            # Validate that the game exists
            games = get_state_value_from_interaction("daily_games", interaction.guild_id) or {}
            if name not in games:
                await interaction.followup.send(
                    f"No registered game named '{name}' found for this guild.", ephemeral=True
                )
                return
            
            game_info = games[name]
            
            # Check if the game is registered for this channel
            if game_info.get("channel_id") != interaction.channel_id:
                channel_mention = f"<#{game_info.get('channel_id')}>"
                await interaction.followup.send(
                    f"The game '{name}' is registered for {channel_mention}, not this channel.", 
                    ephemeral=True
                )
                return
            
            # Parse date range or use defaults
            stats_service = DailyGameStatsService()
            
            if start_date or end_date:
                try:
                    if start_date:
                        parsed_start_date = stats_service.parse_utc_timestamp(start_date)
                    else:
                        parsed_start_date, _ = stats_service.get_default_date_range()
                    
                    if end_date:
                        parsed_end_date = stats_service.parse_utc_timestamp(end_date)
                    else:
                        _, parsed_end_date = stats_service.get_default_date_range()
                        
                except ValueError as e:
                    await interaction.followup.send(
                        f"Invalid date format: {e}", ephemeral=True
                    )
                    return
            else:
                parsed_start_date, parsed_end_date = stats_service.get_default_date_range()
            
            # Validate date range
            try:
                stats_service.validate_date_range(parsed_start_date, parsed_end_date)
            except ValueError as e:
                await interaction.followup.send(
                    f"Invalid date range: {e}", ephemeral=True
                )
                return
            
            # Ensure we're working with a TextChannel
            if not isinstance(interaction.channel, discord.TextChannel):
                await interaction.followup.send(
                    "Stats can only be generated in text channels.", ephemeral=True
                )
                return
            
            # Analyze the stats
            try:
                stats_result = await self.thread_analyzer.analyze_daily_game_stats(
                    interaction.channel, name, parsed_start_date, parsed_end_date
                )
                
                # Format and send the response
                formatted_response = stats_service.format_stats_response(stats_result, self.bot)
                
                # Split message if it's too long for Discord
                if len(formatted_response) <= 2000:
                    await interaction.followup.send(formatted_response)
                else:
                    # Split into multiple messages
                    lines = formatted_response.split('\n')
                    current_message = []
                    current_length = 0
                    
                    for line in lines:
                        line_length = len(line) + 1  # +1 for newline
                        if current_length + line_length > 1900:  # Leave some buffer
                            if current_message:
                                await interaction.followup.send('\n'.join(current_message))
                                current_message = [line]
                                current_length = line_length
                            else:
                                # Single line too long, truncate it
                                await interaction.followup.send(line[:1900] + "...")
                        else:
                            current_message.append(line)
                            current_length += line_length
                    
                    # Send remaining lines
                    if current_message:
                        await interaction.followup.send('\n'.join(current_message))
                        
            except Exception as e:
                await interaction.followup.send(
                    f"Error analyzing game stats: {str(e)}", ephemeral=True
                )
                
        except Exception as e:
            await interaction.followup.send(
                f"An unexpected error occurred: {str(e)}", ephemeral=True
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(DailyGameCog(bot)) 