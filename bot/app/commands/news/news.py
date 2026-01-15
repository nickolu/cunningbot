import discord
from discord import app_commands
from discord.ext import commands
from typing import Any, Optional
from urllib.parse import urlparse
import feedparser
from datetime import datetime
from html.parser import HTMLParser
import hashlib
import logging

from bot.app.app_state import (
    set_state_value_from_interaction,
    get_state_value_from_interaction,
)
from bot.app.story_history import get_todays_story_history

logger = logging.getLogger("NewsCommands")


def _is_valid_url(url: str) -> bool:
    """Simple URL validation."""
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _get_item_id(entry) -> str:
    """Get unique identifier for RSS item."""
    # Try standard fields first
    if hasattr(entry, 'id') and entry.id:
        return str(entry.id)
    if hasattr(entry, 'guid') and entry.guid:
        return str(entry.guid)

    # Fallback: hash of title + link + published
    content = f"{entry.get('title', '')}{entry.get('link', '')}{entry.get('published', '')}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def _clean_html(html_text: str, max_length: int = 500) -> str:
    """Strip HTML tags and limit length for embed."""
    if not html_text:
        return ""

    import re

    # Remove common "related articles" sections before processing
    patterns_to_remove = [
        r'<h[23]>Related.*?</h[23]>.*',  # Related heading and everything after
        r'<div[^>]*class="[^"]*related[^"]*"[^>]*>.*?</div>',  # Related divs
        r'<aside[^>]*>.*?</aside>',  # Aside sections (often related content)
        r'Read more:.*',  # "Read more" sections
        r'Related:.*',  # "Related:" sections
        r'See also:.*',  # "See also" sections
    ]

    cleaned = html_text
    for pattern in patterns_to_remove:
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE | re.DOTALL)

    class HTMLStripper(HTMLParser):
        def __init__(self):
            super().__init__()
            self.text = []

        def handle_data(self, data):
            self.text.append(data)

        def get_text(self):
            return ''.join(self.text)

    stripper = HTMLStripper()
    try:
        stripper.feed(cleaned)
        text = stripper.get_text().strip()
    except Exception:
        # If HTML parsing fails, just strip basic tags
        text = re.sub(r'<[^>]+>', '', cleaned).strip()

    # Remove extra whitespace and newlines
    text = ' '.join(text.split())

    # Truncate with ellipsis
    if len(text) > max_length:
        return text[:max_length-3] + "..."
    return text


def _get_description(entry) -> str:
    """Extract description, preferring full content over summary."""
    if hasattr(entry, 'content') and entry.content:
        return entry.content[0].value
    if hasattr(entry, 'summary'):
        return entry.summary
    if hasattr(entry, 'description'):
        return entry.description
    return ""


def _get_source(entry, feed) -> str:
    """Get source/publication name."""
    # Try source field in entry
    if hasattr(entry, 'source') and hasattr(entry.source, 'title'):
        return entry.source.title
    # Try author
    if hasattr(entry, 'author') and entry.author:
        return entry.author
    # Fallback to feed title
    if hasattr(feed, 'feed') and hasattr(feed.feed, 'title'):
        return feed.feed.title
    return 'Unknown Source'


def _get_image_url(entry) -> str:
    """Extract thumbnail/image URL from RSS item."""
    # Try media:content
    if hasattr(entry, 'media_content') and entry.media_content:
        return entry.media_content[0].get('url', '')

    # Try enclosures
    if hasattr(entry, 'enclosures') and entry.enclosures:
        for enc in entry.enclosures:
            if enc.get('type', '').startswith('image/'):
                return enc.get('href', '')

    # Try media:thumbnail
    if hasattr(entry, 'media_thumbnail') and entry.media_thumbnail:
        return entry.media_thumbnail[0].get('url', '')

    return ""


def _format_feed_preview(entry, feed) -> discord.Embed:
    """Format an RSS entry as a Discord embed for preview."""
    title = entry.get('title', 'No title')[:256]
    link = entry.get('link', '')
    description = _clean_html(_get_description(entry))
    source = _get_source(entry, feed)
    image_url = _get_image_url(entry)

    # Parse published date
    published = entry.get('published', '')
    timestamp = None
    if published:
        try:
            from email.utils import parsedate_to_datetime
            timestamp = parsedate_to_datetime(published)
        except Exception:
            pass

    embed = discord.Embed(
        title=title,
        url=link,
        description=description if description else "No description available",
        color=0x00a8ff,
        timestamp=timestamp
    )

    # Add image if available
    if image_url:
        embed.set_image(url=image_url)

    # Add source attribution in footer
    embed.set_footer(text=f"Source: {source}")

    return embed


class NewsCog(commands.Cog):
    """Cog for managing RSS news feed subscriptions."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    news = app_commands.Group(
        name="news", description="Manage RSS news feed subscriptions."
    )

    @news.command(name="add", description="Add a new RSS feed to monitor in this channel.")
    @app_commands.describe(
        feed_name="Short name for the feed (e.g. 'San Diego News')",
        feed_url="RSS feed URL to monitor",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def add(
        self,
        interaction: discord.Interaction,
        feed_name: str,
        feed_url: str,
    ) -> None:
        """Register a new RSS feed for monitoring in the current channel."""

        # Get the current channel
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "This command can only be used in a text channel.", ephemeral=True
            )
            return

        # Validate URL
        if not _is_valid_url(feed_url):
            await interaction.response.send_message(
                "Please provide a valid URL starting with http:// or https://", ephemeral=True
            )
            return

        # Test feed URL by fetching it
        await interaction.response.defer(ephemeral=True)

        try:
            feed = feedparser.parse(feed_url)
            if feed.bozo and not feed.entries:
                error_msg = f"Failed to parse RSS feed. Please check the URL and try again.\nError: {feed.get('bozo_exception', 'Unknown error')}"

                # Check if this looks like a FreshRSS HTML page
                if 'freshrss' in feed_url.lower() or ('?rid=' in feed_url):
                    error_msg += (
                        "\n\n**⚠️ FreshRSS Detected**\n"
                        "It looks like you're trying to use a FreshRSS web interface URL.\n"
                        "Please use the actual RSS feed URL instead.\n\n"
                        "**For FreshRSS, the correct format is:**\n"
                        "`https://your-server/i/?a=rss&hours=168`\n\n"
                        "You can find the RSS feed link in the page source or try changing `?rid=...` to `?a=rss&hours=168`"
                    )

                await interaction.followup.send(error_msg, ephemeral=True)
                return
        except Exception as e:
            await interaction.followup.send(
                f"Failed to fetch RSS feed: {str(e)}",
                ephemeral=True
            )
            return

        # Fetch current feeds dict
        feeds: dict[str, Any] | None = get_state_value_from_interaction(
            "rss_feeds", interaction.guild_id
        )
        if feeds is None:
            feeds = {}

        # Check if feed name exists in another channel
        if feed_name in feeds and feeds[feed_name]["channel_id"] != channel.id:
            await interaction.followup.send(
                f"⚠️ A feed with the name '{feed_name}' is already registered in another channel. Please choose a different name or remove the existing one first.",
                ephemeral=True,
            )
            return

        # Build/overwrite feed info
        feed_info: dict[str, Any] = {
            "name": feed_name,
            "url": feed_url,
            "channel_id": channel.id,
            "enabled": True,
            "post_mode": "summary",  # "summary" or "direct"
            "filter_instructions": None,  # Custom filter instructions (e.g., "only San Diego articles")
            "last_check": datetime.utcnow().isoformat(),
            "seen_items": [],
            "max_seen_items": 100,
        }

        feeds[feed_name] = feed_info

        # Save back to state
        set_state_value_from_interaction("rss_feeds", feeds, interaction.guild_id)

        await interaction.followup.send(
            f"✅ RSS feed **{feed_name}** registered!\n"
            f"• Mode: **Summary** (articles aggregated for scheduled summaries)\n"
            f"• Use `/news set-schedule` to customize posting times\n"
            f"• Use `/news set-mode {feed_name} direct` to post articles immediately instead",
            ephemeral=True,
        )

    @news.command(name="enable", description="Enable a disabled RSS feed.")
    @app_commands.describe(feed_name="The name of the feed to enable")
    @app_commands.checks.has_permissions(administrator=True)
    async def enable(self, interaction: discord.Interaction, feed_name: str) -> None:
        feeds = get_state_value_from_interaction("rss_feeds", interaction.guild_id) or {}

        if feed_name not in feeds:
            await interaction.response.send_message(
                f"No feed named '{feed_name}' found for this guild.", ephemeral=True
            )
            return

        feeds[feed_name]["enabled"] = True
        set_state_value_from_interaction("rss_feeds", feeds, interaction.guild_id)
        await interaction.response.send_message(
            f"✅ The feed '{feed_name}' has been enabled.", ephemeral=True
        )

    @news.command(name="disable", description="Disable an RSS feed without deleting it.")
    @app_commands.describe(feed_name="The name of the feed to disable")
    @app_commands.checks.has_permissions(administrator=True)
    async def disable(self, interaction: discord.Interaction, feed_name: str) -> None:
        feeds = get_state_value_from_interaction("rss_feeds", interaction.guild_id) or {}

        if feed_name not in feeds:
            await interaction.response.send_message(
                f"No feed named '{feed_name}' found for this guild.", ephemeral=True
            )
            return

        feeds[feed_name]["enabled"] = False
        set_state_value_from_interaction("rss_feeds", feeds, interaction.guild_id)
        await interaction.response.send_message(
            f"🚫 The feed '{feed_name}' has been disabled.", ephemeral=True
        )

    @news.command(name="remove", description="Permanently delete an RSS feed.")
    @app_commands.describe(feed_name="The name of the feed to remove")
    @app_commands.checks.has_permissions(administrator=True)
    async def remove(self, interaction: discord.Interaction, feed_name: str) -> None:
        feeds = get_state_value_from_interaction("rss_feeds", interaction.guild_id) or {}

        if feed_name not in feeds:
            await interaction.response.send_message(
                f"No feed named '{feed_name}' found for this guild.", ephemeral=True
            )
            return

        # Store feed info for confirmation message
        feed_info = feeds[feed_name]
        channel_id = feed_info.get("channel_id")

        # Delete the feed from the dictionary
        del feeds[feed_name]

        # Save back to state
        set_state_value_from_interaction("rss_feeds", feeds, interaction.guild_id)

        # Confirmation message
        channel_mention = f"<#{channel_id}>" if channel_id else "Unknown channel"
        await interaction.response.send_message(
            f"🗑️ RSS feed **{feed_name}** has been deleted completely. It will no longer post to {channel_mention}.",
            ephemeral=True
        )

    @news.command(name="reset", description="Reset a feed to repost all items (clears seen items history).")
    @app_commands.describe(feed_name="The name of the feed to reset")
    @app_commands.checks.has_permissions(administrator=True)
    async def reset(self, interaction: discord.Interaction, feed_name: str) -> None:
        feeds = get_state_value_from_interaction("rss_feeds", interaction.guild_id) or {}

        if feed_name not in feeds:
            await interaction.response.send_message(
                f"No feed named '{feed_name}' found for this guild.", ephemeral=True
            )
            return

        # Clear the seen items list
        old_count = len(feeds[feed_name].get("seen_items", []))
        feeds[feed_name]["seen_items"] = []

        # Save back to state
        set_state_value_from_interaction("rss_feeds", feeds, interaction.guild_id)

        await interaction.response.send_message(
            f"🔄 Feed **{feed_name}** has been reset! Cleared {old_count} seen items.\n"
            f"The next poster run will post up to 5 recent items from this feed.",
            ephemeral=True
        )

    @news.command(name="set-mode", description="Set posting mode for a feed (summary or direct).")
    @app_commands.describe(
        feed_name="The name of the feed to configure",
        mode="Posting mode: 'summary' for aggregated summaries, 'direct' for immediate posts"
    )
    @app_commands.choices(mode=[
        app_commands.Choice(name="Summary (8am/8pm aggregated)", value="summary"),
        app_commands.Choice(name="Direct (immediate posting)", value="direct")
    ])
    @app_commands.checks.has_permissions(administrator=True)
    async def set_mode(
        self,
        interaction: discord.Interaction,
        feed_name: str,
        mode: str
    ) -> None:
        """Set the posting mode for an RSS feed."""
        feeds = get_state_value_from_interaction("rss_feeds", interaction.guild_id) or {}

        if feed_name not in feeds:
            await interaction.response.send_message(
                f"No feed named '{feed_name}' found for this guild.", ephemeral=True
            )
            return

        # Update the mode
        old_mode = feeds[feed_name].get("post_mode", "summary")
        feeds[feed_name]["post_mode"] = mode

        # Save back to state
        set_state_value_from_interaction("rss_feeds", feeds, interaction.guild_id)

        mode_description = {
            "summary": "📊 **Summary Mode** - Articles will be aggregated and posted as AI-generated summaries at 8am and 8pm PT",
            "direct": "⚡ **Direct Mode** - Articles will be posted immediately as they are discovered (every 10 minutes)"
        }

        await interaction.response.send_message(
            f"✅ Feed **{feed_name}** mode changed from **{old_mode}** to **{mode}**\n\n"
            f"{mode_description.get(mode, '')}",
            ephemeral=True
        )

    @news.command(name="set-filter", description="Set custom filter instructions for a feed.")
    @app_commands.describe(
        feed_name="The name of the feed to configure",
        instructions="Filter instructions (e.g., 'only San Diego articles') or 'none' to clear"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def set_filter(
        self,
        interaction: discord.Interaction,
        feed_name: str,
        instructions: str
    ) -> None:
        """Set or clear custom filter instructions for a feed."""
        feeds = get_state_value_from_interaction("rss_feeds", interaction.guild_id) or {}

        if feed_name not in feeds:
            await interaction.response.send_message(
                f"No feed named '{feed_name}' found for this guild.", ephemeral=True
            )
            return

        # Clear filter if "none"
        if instructions.lower() == "none":
            feeds[feed_name]["filter_instructions"] = None
            set_state_value_from_interaction("rss_feeds", feeds, interaction.guild_id)
            await interaction.response.send_message(
                f"✅ Filter cleared for **{feed_name}**\n"
                f"All articles from this feed will be included in summaries.",
                ephemeral=True
            )
        else:
            feeds[feed_name]["filter_instructions"] = instructions
            set_state_value_from_interaction("rss_feeds", feeds, interaction.guild_id)
            await interaction.response.send_message(
                f"✅ Filter set for **{feed_name}**\n"
                f"Active filter: \"{instructions}\"\n\n"
                f"Articles will be filtered using AI based on these instructions during summary generation.",
                ephemeral=True
            )

    @news.command(name="set-schedule", description="Set summary posting times for this channel.")
    @app_commands.describe(
        times="Comma-separated times in 24-hour format (e.g., '8:00,20:00' or '6:00,12:00,18:00'). Use 'default' to reset."
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def set_schedule(
        self,
        interaction: discord.Interaction,
        times: str
    ) -> None:
        """Configure summary posting schedule for current channel."""
        channel_id = interaction.channel_id

        # Load existing schedules
        schedules = get_state_value_from_interaction("channel_summary_schedules", interaction.guild_id) or {}

        # Handle "default" - remove custom schedule
        if times.lower() == "default":
            if str(channel_id) in schedules:
                del schedules[str(channel_id)]
                set_state_value_from_interaction("channel_summary_schedules", schedules, interaction.guild_id)
                await interaction.response.send_message(
                    "✅ Summary schedule reset to default (8:00 AM, 8:00 PM Pacific)",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "ℹ️ This channel already uses the default schedule (8:00 AM, 8:00 PM Pacific)",
                    ephemeral=True
                )
            return

        # Parse times
        try:
            time_list = []
            for time_str in times.split(','):
                time_str = time_str.strip()
                if ':' not in time_str:
                    raise ValueError(f"Invalid time format: {time_str}")

                hour_str, minute_str = time_str.split(':', 1)
                hour = int(hour_str)
                minute = int(minute_str)

                # Validate
                if not (0 <= hour <= 23):
                    raise ValueError(f"Hour must be 0-23, got {hour}")
                if not (0 <= minute <= 59):
                    raise ValueError(f"Minute must be 0-59, got {minute}")

                time_list.append((hour, minute))

            # Limit to 4 time slots
            if len(time_list) > 4:
                await interaction.response.send_message(
                    "⚠️ Maximum 4 time slots allowed. Please provide 1-4 times.",
                    ephemeral=True
                )
                return

            if len(time_list) == 0:
                await interaction.response.send_message(
                    "⚠️ Please provide at least one time. Use 'default' to reset to default schedule.",
                    ephemeral=True
                )
                return

            # Save schedule
            schedules[str(channel_id)] = time_list
            set_state_value_from_interaction("channel_summary_schedules", schedules, interaction.guild_id)

            # Format times for display
            formatted_times = [f"{h}:{m:02d}" for h, m in sorted(time_list)]

            await interaction.response.send_message(
                f"✅ Summary schedule updated for {interaction.channel.mention}\n"
                f"**New schedule (Pacific Time):** {', '.join(formatted_times)}\n\n"
                f"Summaries will be posted at these times when pending articles are available.",
                ephemeral=True
            )

        except ValueError as e:
            await interaction.response.send_message(
                f"❌ Invalid time format: {str(e)}\n\n"
                f"**Expected format:** Comma-separated times in 24-hour format\n"
                f"**Examples:**\n"
                f"  • `8:00,20:00` (8 AM and 8 PM)\n"
                f"  • `6:00,12:00,18:00` (6 AM, 12 PM, 6 PM)\n"
                f"  • `9:30,21:30` (9:30 AM and 9:30 PM)",
                ephemeral=True
            )

    @news.command(name="list", description="List all RSS feeds in this channel.")
    async def list_feeds(self, interaction: discord.Interaction) -> None:
        feeds = get_state_value_from_interaction("rss_feeds", interaction.guild_id) or {}

        if not feeds:
            await interaction.response.send_message(
                "No RSS feeds are currently registered for this guild.", ephemeral=True
            )
            return

        # Filter feeds for current channel only
        current_channel_id = interaction.channel_id
        channel_feeds = {
            name: info for name, info in feeds.items()
            if info.get("channel_id") == current_channel_id
        }

        if not channel_feeds:
            await interaction.response.send_message(
                f"No RSS feeds are registered for {interaction.channel.mention}.", ephemeral=True
            )
            return

        # Build a formatted list of feeds
        feed_list = []
        for name, feed_info in channel_feeds.items():
            status = "✅ Enabled" if feed_info.get("enabled", True) else "🚫 Disabled"
            url = feed_info.get("url", "No URL")
            last_check = feed_info.get("last_check", "Never")
            post_mode = feed_info.get("post_mode", "summary")
            mode_icon = "📊" if post_mode == "summary" else "⚡"

            # Format last check time
            if last_check and last_check != "Never":
                try:
                    dt = datetime.fromisoformat(last_check)
                    last_check_str = dt.strftime("%Y-%m-%d %H:%M UTC")
                except Exception:
                    last_check_str = "Unknown"
            else:
                last_check_str = "Never"

            # Add filter line if filter is set
            filter_instr = feed_info.get("filter_instructions")
            filter_line = f"  • Filter: \"{filter_instr}\"\n" if filter_instr else ""

            feed_entry = (
                f"**{name}**\n"
                f"  • Status: {status}\n"
                f"  • Mode: {mode_icon} {post_mode.title()}\n"
                f"{filter_line}"
                f"  • URL: <{url}>\n"
                f"  • Last Check: {last_check_str}\n"
            )
            feed_list.append(feed_entry)

        # Create embed for better formatting
        embed = discord.Embed(
            title=f"📰 RSS News Feeds in {interaction.channel.name}",
            description="\n".join(feed_list),
            color=0x00ff00 if any(f.get("enabled", True) for f in channel_feeds.values()) else 0xff0000
        )

        embed.set_footer(text=f"Total feeds in this channel: {len(channel_feeds)}")

        # Add schedule information
        schedules = get_state_value_from_interaction("channel_summary_schedules", interaction.guild_id) or {}
        channel_schedule = schedules.get(str(current_channel_id))

        if channel_schedule:
            formatted_times = [f"{h}:{m:02d}" for h, m in sorted(channel_schedule)]
            schedule_str = f"**Custom Schedule (PT):** {', '.join(formatted_times)}"
        else:
            schedule_str = "**Schedule (PT):** 8:00, 20:00 (default)"

        embed.add_field(
            name="📅 Summary Posting Schedule",
            value=schedule_str,
            inline=False
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @news.command(name="preview", description="Preview what a feed post will look like.")
    @app_commands.describe(feed_name="The name of the feed to preview")
    async def preview(self, interaction: discord.Interaction, feed_name: str) -> None:
        feeds = get_state_value_from_interaction("rss_feeds", interaction.guild_id) or {}

        if feed_name not in feeds:
            await interaction.response.send_message(
                f"No feed named '{feed_name}' found for this guild.", ephemeral=True
            )
            return

        feed_info = feeds[feed_name]
        feed_url = feed_info.get("url")

        # Defer response as fetching might take time
        await interaction.response.defer(ephemeral=True)

        try:
            # Fetch the feed
            feed = feedparser.parse(feed_url)

            if feed.bozo and not feed.entries:
                await interaction.followup.send(
                    f"Failed to fetch RSS feed. Error: {feed.get('bozo_exception', 'Unknown error')}",
                    ephemeral=True
                )
                return

            if not feed.entries:
                await interaction.followup.send(
                    "No entries found in the feed.",
                    ephemeral=True
                )
                return

            # Get the most recent entry
            latest_entry = feed.entries[0]

            # Format preview embed
            preview_embed = _format_feed_preview(latest_entry, feed)

            # Create wrapper embed to show it's a preview
            wrapper_embed = discord.Embed(
                title="🔍 RSS Feed Preview",
                description=f"Here's what the latest post from **{feed_name}** looks like:",
                color=0x0099ff
            )

            # Add feed details
            status = "✅ Enabled" if feed_info.get("enabled", True) else "🚫 Disabled"
            channel_id = feed_info.get("channel_id")
            channel_mention = f"<#{channel_id}>" if channel_id else "Unknown channel"

            wrapper_embed.add_field(
                name="Feed Details",
                value=(
                    f"**Status:** {status}\n"
                    f"**Channel:** {channel_mention}\n"
                    f"**URL:** <{feed_url}>"
                ),
                inline=False
            )

            wrapper_embed.set_footer(text="This is just a preview - showing the most recent item from the feed.")

            await interaction.followup.send(embeds=[wrapper_embed, preview_embed], ephemeral=True)

        except Exception as e:
            await interaction.followup.send(
                f"Error fetching feed preview: {str(e)}",
                ephemeral=True
            )

    @news.command(name="summary", description="Generate an on-demand news summary from pending articles.")
    @app_commands.describe(
        force="Generate even if no pending articles (shows recent articles instead)"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def summary(
        self,
        interaction: discord.Interaction,
        force: bool = False
    ) -> None:
        """Generate immediate news summary for current channel."""
        from datetime import datetime
        from bot.domain.news.news_summary_service import generate_news_summary
        from bot.app.pending_news import get_pending_articles_for_channel, clear_pending_articles_for_channel

        # Defer response (this may take time)
        await interaction.response.defer(ephemeral=True)

        # Get feeds for current channel (to check if configured)
        feeds = get_state_value_from_interaction("rss_feeds", interaction.guild_id) or {}
        channel_feeds = {
            name: info for name, info in feeds.items()
            if info.get("channel_id") == interaction.channel_id
        }

        if not channel_feeds:
            await interaction.followup.send(
                "No RSS feeds configured for this channel.",
                ephemeral=True
            )
            return

        # Get pending articles from pending_news.json
        guild_id_str = str(interaction.guild_id)
        pending_by_feed = get_pending_articles_for_channel(guild_id_str, interaction.channel_id)

        # Flatten articles and collect feed names
        all_pending = []
        feed_names = []
        for feed_name, articles in pending_by_feed.items():
            all_pending.extend(articles)
            if articles:
                feed_names.append(feed_name)

        if not all_pending and not force:
            await interaction.followup.send(
                "No pending articles to summarize. Use `force=True` to summarize recent articles anyway.",
                ephemeral=True
            )
            return

        # Build filter map
        filter_map = {
            name: channel_feeds[name].get('filter_instructions')
            for name in feed_names
            if channel_feeds[name].get('filter_instructions')
        }

        # Load today's story history for deduplication
        story_history = get_todays_story_history(guild_id_str, interaction.channel_id)

        # Generate summary
        try:
            logger.info(f"Generating on-demand summary for channel {interaction.channel_id}: {len(all_pending)} articles from {len(feed_names)} feeds")

            summary_result = await generate_news_summary(all_pending, feed_names, filter_map, story_history, "On-Demand")

            # Create embed
            embed = discord.Embed(
                title="📰 News Summary - On-Demand",
                description=summary_result["summary_text"],
                color=0x00a8ff,
                timestamp=datetime.utcnow()
            )

            feed_count = summary_result["feed_count"]
            article_count = summary_result["total_articles"]
            feed_text = "feed" if feed_count == 1 else "feeds"
            article_text = "article" if article_count == 1 else "articles"
            embed.set_footer(text=f"Summarized {article_count} {article_text} from {feed_count} {feed_text}")

            # Post to channel
            await interaction.channel.send(embed=embed)

            # Clear pending articles from pending_news.json
            cleared_count = clear_pending_articles_for_channel(guild_id_str, interaction.channel_id)
            logger.info(f"Cleared {cleared_count} pending articles for channel {interaction.channel_id}")

            # Update last_summary timestamp in app_state
            for name in feed_names:
                if name in feeds:
                    feeds[name]["last_summary"] = datetime.utcnow().isoformat()

            # Save state
            set_state_value_from_interaction("rss_feeds", feeds, interaction.guild_id)

            await interaction.followup.send(
                f"✅ Summary posted! Processed {article_count} articles.",
                ephemeral=True
            )

        except Exception as e:
            logger.error(f"Error generating summary: {e}", exc_info=True)
            await interaction.followup.send(
                f"Failed to generate summary: {str(e)}",
                ephemeral=True
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(NewsCog(bot))
