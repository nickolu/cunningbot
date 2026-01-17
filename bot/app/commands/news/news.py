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
from bot.app.story_history import (
    get_todays_story_history,
    get_stories_within_window,
    get_channel_dedup_window,
    MIN_DEDUP_WINDOW_HOURS,
    MAX_DEDUP_WINDOW_HOURS,
    DEFAULT_DEDUP_WINDOW_HOURS
)

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
            "max_seen_items": 500,
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
        from bot.app.pending_news import clear_pending_articles_for_feed

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

        # Clean up any pending articles for this feed
        guild_id_str = str(interaction.guild_id)
        if channel_id:
            cleared_count = clear_pending_articles_for_feed(guild_id_str, channel_id, feed_name)
            if cleared_count > 0:
                logger.info(f"Cleared {cleared_count} pending articles from removed feed {feed_name}")

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

    @news.command(name="set-limits", description="Configure article processing limits for this channel.")
    @app_commands.describe(
        initial_limit="Max articles to process initially (10-200, default: 50)",
        top_articles_limit="Max articles to rank and cluster (5-50, default: 18)",
        cluster_limit="Max story clusters to generate (3-20, default: 8)",
        reset="Set to 'true' to reset all limits to defaults"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def set_limits(
        self,
        interaction: discord.Interaction,
        initial_limit: Optional[int] = None,
        top_articles_limit: Optional[int] = None,
        cluster_limit: Optional[int] = None,
        reset: Optional[str] = None
    ) -> None:
        """Configure article processing limits for current channel."""
        from bot.domain.news.news_summary_service import (
            validate_limit_value,
            MIN_INITIAL_LIMIT, MAX_INITIAL_LIMIT,
            MIN_TOP_ARTICLES_LIMIT, MAX_TOP_ARTICLES_LIMIT,
            MIN_CLUSTER_LIMIT, MAX_CLUSTER_LIMIT,
            DEFAULT_INITIAL_LIMIT, DEFAULT_TOP_ARTICLES_LIMIT, DEFAULT_CLUSTER_LIMIT
        )

        channel_id = interaction.channel_id
        all_limits = get_state_value_from_interaction("channel_article_limits", interaction.guild_id) or {}

        # Handle reset
        if reset and reset.lower() == "true":
            if str(channel_id) in all_limits:
                del all_limits[str(channel_id)]
                set_state_value_from_interaction("channel_article_limits", all_limits, interaction.guild_id)
                await interaction.response.send_message(
                    f"✅ Article processing limits reset to defaults for {interaction.channel.mention}\n"
                    f"**Defaults:** {DEFAULT_INITIAL_LIMIT} → {DEFAULT_TOP_ARTICLES_LIMIT} → {DEFAULT_CLUSTER_LIMIT}",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "ℹ️ This channel already uses default limits.",
                    ephemeral=True
                )
            return

        # Check if at least one limit was provided
        if initial_limit is None and top_articles_limit is None and cluster_limit is None:
            await interaction.response.send_message(
                "⚠️ Please provide at least one limit to set, or use `reset=true`.\n\n"
                "**Examples:**\n"
                "  • `/news set-limits initial_limit:100`\n"
                "  • `/news set-limits top_articles_limit:25 cluster_limit:12`\n"
                "  • `/news set-limits reset:true`",
                ephemeral=True
            )
            return

        # Get current limits (or defaults)
        current_limits = all_limits.get(str(channel_id), {})
        new_limits = {
            "initial_limit": current_limits.get("initial_limit", DEFAULT_INITIAL_LIMIT),
            "top_articles_limit": current_limits.get("top_articles_limit", DEFAULT_TOP_ARTICLES_LIMIT),
            "cluster_limit": current_limits.get("cluster_limit", DEFAULT_CLUSTER_LIMIT)
        }

        # Validate and update provided limits
        try:
            if initial_limit is not None:
                validate_limit_value(initial_limit, MIN_INITIAL_LIMIT, MAX_INITIAL_LIMIT, "Initial limit")
                new_limits["initial_limit"] = initial_limit

            if top_articles_limit is not None:
                validate_limit_value(top_articles_limit, MIN_TOP_ARTICLES_LIMIT, MAX_TOP_ARTICLES_LIMIT, "Top articles limit")
                new_limits["top_articles_limit"] = top_articles_limit

            if cluster_limit is not None:
                validate_limit_value(cluster_limit, MIN_CLUSTER_LIMIT, MAX_CLUSTER_LIMIT, "Cluster limit")
                new_limits["cluster_limit"] = cluster_limit

            # Logical validation: top_articles_limit <= initial_limit
            if new_limits["top_articles_limit"] > new_limits["initial_limit"]:
                await interaction.response.send_message(
                    f"⚠️ Top articles limit ({new_limits['top_articles_limit']}) cannot exceed initial limit ({new_limits['initial_limit']}).",
                    ephemeral=True
                )
                return

            # Logical validation: cluster_limit <= top_articles_limit
            if new_limits["cluster_limit"] > new_limits["top_articles_limit"]:
                await interaction.response.send_message(
                    f"⚠️ Cluster limit ({new_limits['cluster_limit']}) cannot exceed top articles limit ({new_limits['top_articles_limit']}).",
                    ephemeral=True
                )
                return

        except ValueError as e:
            await interaction.response.send_message(
                f"❌ Invalid limit value: {str(e)}\n\n"
                f"**Valid ranges:**\n"
                f"  • Initial: {MIN_INITIAL_LIMIT}-{MAX_INITIAL_LIMIT}\n"
                f"  • Top articles: {MIN_TOP_ARTICLES_LIMIT}-{MAX_TOP_ARTICLES_LIMIT}\n"
                f"  • Clusters: {MIN_CLUSTER_LIMIT}-{MAX_CLUSTER_LIMIT}",
                ephemeral=True
            )
            return

        # Save new limits
        all_limits[str(channel_id)] = new_limits
        set_state_value_from_interaction("channel_article_limits", all_limits, interaction.guild_id)

        # Build response
        changes = []
        if initial_limit is not None:
            changes.append(f"Initial articles: **{initial_limit}**")
        if top_articles_limit is not None:
            changes.append(f"Top articles: **{top_articles_limit}**")
        if cluster_limit is not None:
            changes.append(f"Story clusters: **{cluster_limit}**")

        await interaction.response.send_message(
            f"✅ Article processing limits updated for {interaction.channel.mention}\n\n"
            f"**Updated:** " + ", ".join(changes) + "\n\n"
            f"**Current config:** {new_limits['initial_limit']} → {new_limits['top_articles_limit']} → {new_limits['cluster_limit']}",
            ephemeral=True
        )

    @news.command(name="set-window", description="Set deduplication time window for this channel.")
    @app_commands.describe(
        hours="Time window in hours (6-168). Use 'default' to reset to 24 hours."
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def set_window(
        self,
        interaction: discord.Interaction,
        hours: str
    ) -> None:
        """Configure deduplication window for current channel."""
        channel_id = interaction.channel_id
        windows = get_state_value_from_interaction("channel_dedup_windows", interaction.guild_id) or {}

        # Handle "default"
        if hours.lower() == "default":
            if str(channel_id) in windows:
                del windows[str(channel_id)]
                set_state_value_from_interaction("channel_dedup_windows", windows, interaction.guild_id)
                await interaction.response.send_message(
                    f"✅ Deduplication window reset to default ({DEFAULT_DEDUP_WINDOW_HOURS} hours)",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    f"ℹ️ This channel already uses the default window ({DEFAULT_DEDUP_WINDOW_HOURS} hours)",
                    ephemeral=True
                )
            return

        # Parse and validate
        try:
            hours_int = int(hours)

            if hours_int < MIN_DEDUP_WINDOW_HOURS or hours_int > MAX_DEDUP_WINDOW_HOURS:
                await interaction.response.send_message(
                    f"⚠️ Window must be between {MIN_DEDUP_WINDOW_HOURS} and {MAX_DEDUP_WINDOW_HOURS} hours.\n"
                    f"**Recommended:** 24h (1 day), 48h (2 days), 72h (3 days), 168h (7 days)",
                    ephemeral=True
                )
                return

            # Save
            windows[str(channel_id)] = hours_int
            set_state_value_from_interaction("channel_dedup_windows", windows, interaction.guild_id)

            # Format response
            days_str = ""
            if hours_int >= 24:
                days = hours_int / 24
                days_str = f" ({days:.1f} days)" if days != int(days) else f" ({int(days)} day{'s' if days != 1 else ''})"

            await interaction.response.send_message(
                f"✅ Deduplication window updated for {interaction.channel.mention}\n"
                f"**New window:** {hours_int} hours{days_str}\n\n"
                f"Stories posted within the last {hours_int} hours will be used for deduplication.",
                ephemeral=True
            )

        except ValueError:
            await interaction.response.send_message(
                f"❌ Invalid input. Provide a number (6-168) or 'default'.\n"
                f"**Examples:** `/news set-window hours:48` or `/news set-window hours:default`",
                ephemeral=True
            )

    @news.command(name="diversity", description="Configure feed diversity settings for this channel.")
    @app_commands.describe(
        action="Action to perform: configure, show, or reset",
        strategy="Diversity strategy: balanced, proportional, or disabled",
        max_per_feed="Maximum articles from any single feed (optional)",
        min_per_feed="Minimum articles per feed (optional)"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def diversity(
        self,
        interaction: discord.Interaction,
        action: str,
        strategy: Optional[str] = None,
        max_per_feed: Optional[int] = None,
        min_per_feed: Optional[int] = None
    ) -> None:
        """Configure feed diversity to ensure fair representation across feeds."""
        from bot.domain.news.feed_diversity import (
            DEFAULT_STRATEGY,
            RECOMMENDED_MAX_PER_FEED,
            RECOMMENDED_MIN_PER_FEED,
            get_channel_feed_diversity
        )

        channel_id = interaction.channel_id
        all_diversity = get_state_value_from_interaction("channel_feed_diversity", interaction.guild_id) or {}

        if action.lower() == "show":
            # Show current configuration
            config = get_channel_feed_diversity(interaction.guild_id, channel_id)
            if config.get("strategy") == "disabled":
                await interaction.response.send_message(
                    "📊 Feed diversity is currently **disabled** for this channel.\n"
                    "Articles are selected purely by recency (current behavior).",
                    ephemeral=True
                )
            else:
                strat = config.get("strategy", "disabled")
                max_per = config.get("max_articles_per_feed")
                min_per = config.get("min_articles_per_feed", 0)

                max_str = str(max_per) if max_per is not None else "No limit"
                min_str = str(min_per)

                await interaction.response.send_message(
                    f"📊 Feed Diversity Configuration for {interaction.channel.mention}\n\n"
                    f"**Strategy:** {strat}\n"
                    f"**Max per feed:** {max_str}\n"
                    f"**Min per feed:** {min_str}\n\n"
                    f"Use `/news diversity configure` to change settings.",
                    ephemeral=True
                )
            return

        elif action.lower() == "reset":
            # Reset to default (disabled)
            if str(channel_id) in all_diversity:
                del all_diversity[str(channel_id)]
                set_state_value_from_interaction("channel_feed_diversity", all_diversity, interaction.guild_id)
                await interaction.response.send_message(
                    "✅ Feed diversity reset to default (disabled).\n"
                    "Articles will be selected by recency without feed balancing.",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "ℹ️ This channel already uses default settings (disabled).",
                    ephemeral=True
                )
            return

        elif action.lower() == "configure":
            # Validate strategy
            if not strategy:
                await interaction.response.send_message(
                    "⚠️ Please specify a strategy: `balanced`, `proportional`, or `disabled`\n\n"
                    f"**Recommended for most channels:**\n"
                    f"`/news diversity action:configure strategy:balanced max_per_feed:{RECOMMENDED_MAX_PER_FEED} min_per_feed:{RECOMMENDED_MIN_PER_FEED}`",
                    ephemeral=True
                )
                return

            if strategy.lower() not in ["disabled", "balanced", "proportional"]:
                await interaction.response.send_message(
                    "❌ Invalid strategy. Choose: `balanced`, `proportional`, or `disabled`",
                    ephemeral=True
                )
                return

            # Validate bounds
            if max_per_feed is not None and max_per_feed < 1:
                await interaction.response.send_message(
                    "⚠️ max_per_feed must be at least 1",
                    ephemeral=True
                )
                return

            if min_per_feed is not None and min_per_feed < 0:
                await interaction.response.send_message(
                    "⚠️ min_per_feed cannot be negative",
                    ephemeral=True
                )
                return

            # Validate max vs min
            if max_per_feed is not None and min_per_feed is not None and min_per_feed > max_per_feed:
                await interaction.response.send_message(
                    "⚠️ min_per_feed cannot be greater than max_per_feed",
                    ephemeral=True
                )
                return

            # Save configuration
            config = {
                "strategy": strategy.lower(),
                "max_articles_per_feed": max_per_feed,
                "min_articles_per_feed": min_per_feed if min_per_feed is not None else 0
            }

            all_diversity[str(channel_id)] = config
            set_state_value_from_interaction("channel_feed_diversity", all_diversity, interaction.guild_id)

            # Format response
            max_str = str(max_per_feed) if max_per_feed is not None else "No limit"
            min_str = str(min_per_feed) if min_per_feed is not None else "0"

            await interaction.response.send_message(
                f"✅ Feed diversity configured for {interaction.channel.mention}\n\n"
                f"**Strategy:** {strategy.lower()}\n"
                f"**Max per feed:** {max_str}\n"
                f"**Min per feed:** {min_str}\n\n"
                f"Next summary will apply these diversity rules.",
                ephemeral=True
            )

        else:
            await interaction.response.send_message(
                "❌ Invalid action. Use: `configure`, `show`, or `reset`",
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

        # Add article processing limits information
        limits = get_state_value_from_interaction("channel_article_limits", interaction.guild_id) or {}
        channel_limits = limits.get(str(current_channel_id))

        if channel_limits:
            limits_str = (
                f"**Custom Limits:** "
                f"{channel_limits['initial_limit']} → "
                f"{channel_limits['top_articles_limit']} → "
                f"{channel_limits['cluster_limit']}"
            )
        else:
            from bot.domain.news.news_summary_service import (
                DEFAULT_INITIAL_LIMIT, DEFAULT_TOP_ARTICLES_LIMIT, DEFAULT_CLUSTER_LIMIT
            )
            limits_str = f"**Limits:** {DEFAULT_INITIAL_LIMIT} → {DEFAULT_TOP_ARTICLES_LIMIT} → {DEFAULT_CLUSTER_LIMIT} (default)"

        embed.add_field(
            name="⚙️ Article Processing Limits",
            value=limits_str,
            inline=False
        )

        # Add deduplication window information
        windows = get_state_value_from_interaction("channel_dedup_windows", interaction.guild_id) or {}
        channel_window = windows.get(str(current_channel_id))

        if channel_window:
            days_str = f" ({channel_window / 24:.1f} days)" if channel_window >= 24 else ""
            window_str = f"**Custom Window:** {channel_window} hours{days_str}"
        else:
            window_str = f"**Window:** {DEFAULT_DEDUP_WINDOW_HOURS} hours (default)"

        embed.add_field(
            name="🔄 Deduplication Window",
            value=window_str,
            inline=False
        )

        # Add feed diversity information
        from bot.domain.news.feed_diversity import get_channel_feed_diversity
        diversity_config = get_channel_feed_diversity(interaction.guild_id, current_channel_id)

        if diversity_config.get("strategy") != "disabled":
            strat = diversity_config.get("strategy", "disabled")
            max_per = diversity_config.get("max_articles_per_feed")
            min_per = diversity_config.get("min_articles_per_feed", 0)

            max_str = str(max_per) if max_per is not None else "No limit"
            min_str = str(min_per)

            diversity_str = (
                f"**Strategy:** {strat}\n"
                f"**Max per feed:** {max_str}\n"
                f"**Min per feed:** {min_str}"
            )
        else:
            diversity_str = "**Disabled** (articles selected by recency only)"

        embed.add_field(
            name="📊 Feed Diversity",
            value=diversity_str,
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
        from bot.app.pending_news import get_pending_articles_for_channel, clear_pending_articles_for_channel, clear_pending_articles_for_feed

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

        # Flatten articles and collect feed names (only for feeds that still exist)
        all_pending = []
        feed_names = []
        orphaned_feeds = []

        for feed_name, articles in pending_by_feed.items():
            # Skip and clean up articles from feeds that have been removed
            if feed_name not in channel_feeds:
                logger.warning(f"Found {len(articles)} orphaned articles from removed feed: {feed_name}")
                orphaned_feeds.append(feed_name)
                continue

            all_pending.extend(articles)
            if articles:
                feed_names.append(feed_name)

        # Clean up orphaned articles
        for orphaned_feed in orphaned_feeds:
            cleared = clear_pending_articles_for_feed(guild_id_str, interaction.channel_id, orphaned_feed)
            logger.info(f"Cleaned up {cleared} orphaned articles from removed feed: {orphaned_feed}")

        if not all_pending and not force:
            await interaction.followup.send(
                "No pending articles to summarize. Use `force=True` to summarize recent articles anyway.",
                ephemeral=True
            )
            return

        # Build filter map (feeds are guaranteed to exist in channel_feeds now)
        filter_map = {
            name: channel_feeds[name].get('filter_instructions')
            for name in feed_names
            if channel_feeds[name].get('filter_instructions')
        }

        # Load story history within deduplication window
        window_hours = get_channel_dedup_window(interaction.guild_id, interaction.channel_id)
        story_history = get_stories_within_window(guild_id_str, interaction.channel_id, window_hours)

        # Load article processing limits for this channel
        from bot.domain.news.news_summary_service import get_channel_article_limits
        limits = get_channel_article_limits(interaction.guild_id, interaction.channel_id)

        # Load feed diversity config for this channel
        from bot.domain.news.feed_diversity import get_channel_feed_diversity
        diversity_config = get_channel_feed_diversity(interaction.guild_id, interaction.channel_id)

        # Generate summary
        try:
            logger.info(f"Generating on-demand summary for channel {interaction.channel_id}: {len(all_pending)} articles from {len(feed_names)} feeds")

            summary_result = await generate_news_summary(
                articles=all_pending,
                feed_names=feed_names,
                filter_map=filter_map,
                story_history=story_history,
                edition="On-Demand",
                initial_limit=limits["initial_limit"],
                top_articles_limit=limits["top_articles_limit"],
                cluster_limit=limits["cluster_limit"],
                diversity_config=diversity_config
            )

            # Create embed with stats
            article_count = summary_result["total_articles"]
            feed_count = summary_result["feed_count"]
            stats = summary_result.get("stats", {})

            # Determine color based on whether there are stories
            color = 0x00a8ff if article_count > 0 else 0x808080

            embed = discord.Embed(
                title="📰 News Summary - On-Demand",
                description=summary_result["summary_text"],
                color=color,
                timestamp=datetime.utcnow()
            )

            # Build footer with statistics
            if stats:
                footer_parts = []

                # Original count
                original = stats.get("original_count", 0)
                footer_parts.append(f"Collected: {original}")

                # Filtering breakdown
                filter_parts = []
                if stats.get("filtered_by_limit", 0) > 0:
                    filter_parts.append(f"{stats['filtered_by_limit']} by limit")
                if stats.get("filtered_by_feed_filter", 0) > 0:
                    filter_parts.append(f"{stats['filtered_by_feed_filter']} by filters")
                if stats.get("filtered_by_url_dedup", 0) > 0:
                    filter_parts.append(f"{stats['filtered_by_url_dedup']} by URL dedup")
                if stats.get("filtered_by_story_dedup", 0) > 0:
                    filter_parts.append(f"{stats['filtered_by_story_dedup']} by story dedup")

                if filter_parts:
                    footer_parts.append(f"Filtered: {', '.join(filter_parts)}")

                # Feed distribution (if diversity applied)
                feed_dist = stats.get("feed_distribution")
                if feed_dist:
                    # Format as "Feed1: 5, Feed2: 3, Feed3: 2"
                    dist_items = sorted(feed_dist.items(), key=lambda x: -x[1])  # Sort by count desc
                    dist_str = ", ".join(f"{feed}: {count}" for feed, count in dist_items[:5])  # Top 5
                    if len(dist_items) > 5:
                        dist_str += f", +{len(dist_items) - 5} more"
                    footer_parts.append(f"Distribution: {dist_str}")

                # Feed count
                feed_text = "feed" if feed_count == 1 else "feeds"
                footer_parts.append(f"{feed_count} {feed_text}")

                embed.set_footer(text=" • ".join(footer_parts))
            else:
                # Fallback to old format
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

    @news.command(name="latest", description="View recent articles from RSS feeds with pagination.")
    @app_commands.describe(
        page="Page number to display (default: 1)",
        feed_name="Optional: filter by specific feed name"
    )
    async def latest(
        self,
        interaction: discord.Interaction,
        page: Optional[int] = 1,
        feed_name: Optional[str] = None
    ) -> None:
        """Browse recent articles from RSS feeds with pagination."""
        from bot.app.pending_news import get_pending_articles_for_channel

        ARTICLES_PER_PAGE = 10

        # Validate channel has feeds
        feeds = get_state_value_from_interaction("rss_feeds", interaction.guild_id) or {}
        channel_feeds = {name: info for name, info in feeds.items() if info.get("channel_id") == interaction.channel_id}

        if not channel_feeds:
            await interaction.response.send_message("No RSS feeds configured for this channel.", ephemeral=True)
            return

        # Get pending articles
        guild_id_str = str(interaction.guild_id)
        pending_by_feed = get_pending_articles_for_channel(guild_id_str, interaction.channel_id)

        # Handle feed filtering
        if feed_name:
            if feed_name not in pending_by_feed:
                available = ", ".join(f"'{n}'" for n in pending_by_feed.keys()) if pending_by_feed else "none"
                await interaction.response.send_message(
                    f"Feed '{feed_name}' not found.\nAvailable: {available}",
                    ephemeral=True
                )
                return
            all_articles = pending_by_feed[feed_name]
            feed_filter_text = f" - {feed_name}"
        else:
            all_articles = []
            for articles in pending_by_feed.values():
                all_articles.extend(articles)
            feed_filter_text = ""

        if not all_articles:
            await interaction.response.send_message("No pending articles available.", ephemeral=True)
            return

        # Sort by collected_at DESC
        all_articles.sort(key=lambda x: x.get('collected_at', ''), reverse=True)

        # Calculate pagination
        total_articles = len(all_articles)
        total_pages = max(1, (total_articles + ARTICLES_PER_PAGE - 1) // ARTICLES_PER_PAGE)

        # Validate page
        if page < 1:
            await interaction.response.send_message("Page must be 1 or greater.", ephemeral=True)
            return
        if page > total_pages:
            await interaction.response.send_message(
                f"Page {page} doesn't exist. Max: {total_pages}",
                ephemeral=True
            )
            return

        # Get page slice
        start_idx = (page - 1) * ARTICLES_PER_PAGE
        end_idx = start_idx + ARTICLES_PER_PAGE
        page_articles = all_articles[start_idx:end_idx]

        # Format article list
        article_list = _format_article_list(page_articles, start_idx + 1)

        # Create embed
        embed = discord.Embed(
            title=f"📰 Latest Articles{feed_filter_text}",
            description=article_list,
            color=0x00a8ff
        )

        # Footer with navigation
        footer_parts = [f"Page {page} of {total_pages}", f"{total_articles} total"]
        if page < total_pages:
            footer_parts.append(f"Use /news latest page:{page + 1} for next")
        embed.set_footer(text=" • ".join(footer_parts))

        await interaction.response.send_message(embed=embed, ephemeral=True)


def _format_article_list(articles: list[dict[str, Any]], start_number: int = 1) -> str:
    """Format articles as compact numbered list."""
    lines = []

    for idx, article in enumerate(articles, start=start_number):
        # Truncate title
        title = article.get('title', 'Untitled')
        if len(title) > 80:
            title = title[:77] + "..."

        # Get source and time
        source = article.get('source', 'Unknown Source')
        collected_at = article.get('collected_at', '')
        relative_time = _format_relative_time(collected_at)

        # Format link
        link = article.get('link', '')

        # Build entry
        lines.append(f"**{idx}. {title}**")
        lines.append(f"*{source}* • {relative_time}")
        if link:
            lines.append(f"[Read more]({link})")
        lines.append("")  # Blank line

    return "\n".join(lines)


def _format_relative_time(iso_timestamp: str) -> str:
    """Convert ISO timestamp to relative time (e.g., '2 hours ago')."""
    if not iso_timestamp:
        return "recently"

    try:
        from datetime import timezone

        article_time = datetime.fromisoformat(iso_timestamp.replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)

        if article_time.tzinfo is None:
            article_time = article_time.replace(tzinfo=timezone.utc)

        delta = now - article_time
        seconds = delta.total_seconds()

        if seconds < 60:
            return "just now"
        elif seconds < 3600:
            minutes = int(seconds / 60)
            return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
        elif seconds < 86400:
            hours = int(seconds / 3600)
            return f"{hours} hour{'s' if hours != 1 else ''} ago"
        else:
            days = int(seconds / 86400)
            return f"{days} day{'s' if days != 1 else ''} ago"
    except Exception:
        return "recently"


async def setup(bot: commands.Bot):
    await bot.add_cog(NewsCog(bot))
