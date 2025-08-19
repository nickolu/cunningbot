"""
poll.py
General purpose polling commands for Discord bot.
Allows users to create polls with multiple options and emoji voting.
"""

import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from bot.app.utils.logger import get_logger

logger = get_logger()

# Emoji numbers for voting (0-9)
NUMBER_EMOJIS = ["0️⃣", "1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣"]

class PollCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Store active polls: {message_id: poll_data}
        self.active_polls: Dict[int, Dict] = {}

    @app_commands.command(name="poll", description="Create a poll with multiple options for voting")
    @app_commands.describe(
        title="The title/question for the poll",
        options="Poll options separated by semicolons (e.g. 'Option A; Option B; Option C')"
    )
    async def poll(
        self, 
        interaction: discord.Interaction, 
        title: str,
        options: str
    ) -> None:
        """Create a poll with emoji voting."""
        try:
            # Parse poll options
            poll_options = [option.strip() for option in options.split(';') if option.strip()]
            
            if len(poll_options) == 0:
                await interaction.response.send_message(
                    "❌ Please provide at least one poll option. Separate multiple options with semicolons.",
                    ephemeral=True
                )
                return
            
            if len(poll_options) > 10:
                await interaction.response.send_message(
                    "❌ Maximum 10 poll options allowed (limited by available emoji).",
                    ephemeral=True
                )
                return

            # Create poll embed
            embed = discord.Embed(
                title=f"📊 Poll: {title}",
                description="React with the corresponding number emoji to vote for your preferred option(s)!",
                color=0x00ff00,
                timestamp=datetime.now()
            )
            
            # Add poll options to embed
            options_text = ""
            for i, poll_option in enumerate(poll_options):
                options_text += f"{NUMBER_EMOJIS[i]} {poll_option}\n"
            
            embed.add_field(name="Options", value=options_text, inline=False)
            embed.add_field(name="Votes", value="*No votes yet*", inline=False)
            embed.set_footer(text=f"Poll created by {interaction.user.display_name}")

            # Send the poll message
            await interaction.response.send_message(embed=embed)
            message = await interaction.original_response()

            # Add reaction emojis
            for i in range(len(poll_options)):
                await message.add_reaction(NUMBER_EMOJIS[i])

            # Store poll data
            self.active_polls[message.id] = {
                'title': title,
                'options': poll_options,
                'creator': interaction.user.id,
                'channel_id': interaction.channel_id,
                'votes': {i: set() for i in range(len(poll_options))},  # {option_index: {user_ids}}
                'created_at': datetime.now()
            }

            logger.info(f"Poll created: '{title}' by {interaction.user.display_name} ({interaction.user.id})")

        except Exception as e:
            logger.error(f"Error creating poll: {str(e)}")
            error_msg = "❌ An error occurred while creating the poll. Please try again."
            if not interaction.response.is_done():
                await interaction.response.send_message(error_msg, ephemeral=True)
            else:
                await interaction.followup.send(error_msg, ephemeral=True)

    @app_commands.command(name="poll-results", description="Show current results for a poll")
    @app_commands.describe(
        message_id="The ID of the poll message (optional - if not provided, uses the most recent poll in this channel)"
    )
    async def poll_results(
        self, 
        interaction: discord.Interaction, 
        message_id: Optional[str] = None
    ) -> None:
        """Display current results for a poll."""
        try:
            msg_id = None
            
            if message_id is not None:
                # Message ID provided - use it
                try:
                    msg_id = int(message_id)
                except ValueError:
                    await interaction.response.send_message(
                        "❌ Invalid message ID. Please provide a valid message ID number.",
                        ephemeral=True
                    )
                    return
            else:
                # No message ID provided - find most recent poll in this channel
                channel_id = interaction.channel_id
                most_recent_poll = None
                most_recent_time = None
                
                for poll_msg_id, poll_data in self.active_polls.items():
                    if poll_data['channel_id'] == channel_id:
                        if most_recent_time is None or poll_data['created_at'] > most_recent_time:
                            most_recent_poll = poll_msg_id
                            most_recent_time = poll_data['created_at']
                
                if most_recent_poll is None:
                    await interaction.response.send_message(
                        "❌ No active polls found in this channel. Create a poll first using `/poll`.",
                        ephemeral=True
                    )
                    return
                
                msg_id = most_recent_poll

            if msg_id not in self.active_polls:
                await interaction.response.send_message(
                    "❌ Poll not found. Make sure you're using the correct message ID of an active poll.",
                    ephemeral=True
                )
                return

            poll_data = self.active_polls[msg_id]
            
            # Create results embed
            embed = discord.Embed(
                title=f"📊 Poll Results: {poll_data['title']}",
                color=0x0099ff,
                timestamp=datetime.now()
            )
            
            # Add note if this was auto-selected as the most recent poll
            if message_id is None:
                embed.description = "*Showing results for the most recent poll in this channel*"

            # Calculate and display results
            results_text = ""
            total_votes = 0
            
            for i, option in enumerate(poll_data['options']):
                vote_count = len(poll_data['votes'][i])
                total_votes += vote_count
                
                # Get voter names (up to 5, then show "and X more")
                voter_names = []
                for user_id in list(poll_data['votes'][i])[:5]:
                    try:
                        user = self.bot.get_user(user_id)
                        if user and hasattr(user, 'display_name'):
                            voter_names.append(user.display_name)
                        # If we can't get the user or display name, skip adding them
                    except:
                        # Skip users we can't fetch
                        pass
                
                voters_display = ""
                if voter_names:
                    voters_display = f" ({', '.join(voter_names)}"
                    # Only show "and X more" if we have more voters than names we could fetch
                    remaining_voters = len(poll_data['votes'][i]) - len(voter_names)
                    if remaining_voters > 0:
                        voters_display += f" and {remaining_voters} more"
                    voters_display += ")"
                
                results_text += f"{NUMBER_EMOJIS[i]} **{option}** - {vote_count} vote{'s' if vote_count != 1 else ''}{voters_display}\n"

            if total_votes == 0:
                results_text = "*No votes yet*"

            embed.add_field(name="Current Results", value=results_text, inline=False)
            embed.add_field(name="Total Votes", value=str(total_votes), inline=True)
            embed.add_field(name="Poll Created", value=poll_data['created_at'].strftime("%Y-%m-%d %H:%M"), inline=True)

            await interaction.response.send_message(embed=embed)

        except Exception as e:
            logger.error(f"Error showing poll results: {str(e)}")
            error_msg = "❌ An error occurred while fetching poll results."
            if not interaction.response.is_done():
                await interaction.response.send_message(error_msg, ephemeral=True)
            else:
                await interaction.followup.send(error_msg, ephemeral=True)

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.User) -> None:
        """Handle reaction additions for poll voting."""
        # Ignore bot reactions
        if user.bot:
            return

        message_id = reaction.message.id
        if message_id not in self.active_polls:
            return

        # Check if it's a valid voting emoji
        if str(reaction.emoji) not in NUMBER_EMOJIS:
            return

        poll_data = self.active_polls[message_id]
        emoji_index = NUMBER_EMOJIS.index(str(reaction.emoji))
        
        # Check if this emoji corresponds to a valid option
        if emoji_index >= len(poll_data['options']):
            return

        # Add user vote
        poll_data['votes'][emoji_index].add(user.id)
        
        # Update the poll message
        await self._update_poll_message(reaction.message, poll_data)
        
        logger.info(f"User {user.display_name} ({user.id}) voted for option {emoji_index} in poll '{poll_data['title']}'")

    @commands.Cog.listener()
    async def on_reaction_remove(self, reaction: discord.Reaction, user: discord.User) -> None:
        """Handle reaction removals for poll voting."""
        # Ignore bot reactions
        if user.bot:
            return

        message_id = reaction.message.id
        if message_id not in self.active_polls:
            return

        # Check if it's a valid voting emoji
        if str(reaction.emoji) not in NUMBER_EMOJIS:
            return

        poll_data = self.active_polls[message_id]
        emoji_index = NUMBER_EMOJIS.index(str(reaction.emoji))
        
        # Check if this emoji corresponds to a valid option
        if emoji_index >= len(poll_data['options']):
            return

        # Remove user vote
        poll_data['votes'][emoji_index].discard(user.id)
        
        # Update the poll message
        await self._update_poll_message(reaction.message, poll_data)
        
        logger.info(f"User {user.display_name} ({user.id}) removed vote for option {emoji_index} in poll '{poll_data['title']}'")

    async def _update_poll_message(self, message: discord.Message, poll_data: Dict) -> None:
        """Update the poll message embed with current vote counts."""
        try:
            # Create updated embed
            embed = discord.Embed(
                title=f"📊 Poll: {poll_data['title']}",
                description="React with the corresponding number emoji to vote for your preferred option(s)!",
                color=0x00ff00,
                timestamp=poll_data['created_at']
            )
            
            # Add poll options to embed
            options_text = ""
            for i, option in enumerate(poll_data['options']):
                options_text += f"{NUMBER_EMOJIS[i]} {option}\n"
            
            embed.add_field(name="Options", value=options_text, inline=False)
            
            # Add current vote counts
            votes_text = ""
            total_votes = 0
            
            for i, option in enumerate(poll_data['options']):
                vote_count = len(poll_data['votes'][i])
                total_votes += vote_count
                votes_text += f"{NUMBER_EMOJIS[i]} **{vote_count}** vote{'s' if vote_count != 1 else ''}\n"
            
            if total_votes == 0:
                votes_text = "*No votes yet*"
            
            embed.add_field(name="Current Votes", value=votes_text, inline=False)
            embed.set_footer(text=f"Poll created by {message.guild.get_member(poll_data['creator']).display_name if message.guild.get_member(poll_data['creator']) else 'Unknown User'} • Total votes: {total_votes}")

            await message.edit(embed=embed)

        except Exception as e:
            logger.error(f"Error updating poll message: {str(e)}")

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PollCog(bot))
