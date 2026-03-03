"""/af command for Animation Factory GIF search."""

from __future__ import annotations

import re
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from bot.api.animation_factory.client import AF_BASE_URL, AnimationFactoryClient
from bot.app.utils.logger import get_logger

logger = get_logger()


class AFCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.client = AnimationFactoryClient()

    @staticmethod
    def _to_absolute_url(path_or_url: str) -> str:
        if not path_or_url:
            return ""
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            return path_or_url
        if path_or_url.startswith("/"):
            return f"{AF_BASE_URL}{path_or_url}"
        return ""

    @staticmethod
    def _truncate(value: str, max_len: int) -> str:
        return value if len(value) <= max_len else value[:max_len]

    def _choice_value_from_result(self, result: dict[str, Any], fallback: str) -> str:
        filename = str(result.get("filename", "")).strip()
        if filename and len(filename) <= 100:
            return filename

        absolute_url = self._to_absolute_url(str(result.get("url", "")))
        if absolute_url and len(absolute_url) <= 100:
            return absolute_url

        return self._truncate(fallback, 100)

    def _resolve_style_url(self, result: dict[str, Any], style: str) -> str:
        style_map = {
            "clear": "Clear",
            "black": "Black",
            "default": "Default",
        }

        variants = result.get("variants", [])
        if isinstance(variants, list):
            expected_label = style_map.get(style, "Default")
            for variant in variants:
                if not isinstance(variant, dict):
                    continue
                if str(variant.get("label", "")).strip().lower() != expected_label.lower():
                    continue
                variant_url = self._to_absolute_url(str(variant.get("url", "")))
                if variant_url:
                    return variant_url

        return self._to_absolute_url(str(result.get("url", "")))

    def _apply_style_to_direct_url(self, url: str, style: str) -> str:
        suffix_by_style = {
            "clear": "c",
            "black": "b",
            "default": "a",
        }
        suffix = suffix_by_style.get(style)
        if not suffix:
            return url

        return re.sub(r"_h[a-z](\.gif)$", rf"_h{suffix}\1", url, flags=re.IGNORECASE)

    @app_commands.command(name="af", description="Search Animation Factory GIFs and post one.")
    @app_commands.describe(query="Search query for GIFs", style="GIF variant style")
    @app_commands.choices(
        style=[
            app_commands.Choice(name="Default", value="default"),
            app_commands.Choice(name="Clear", value="clear"),
            app_commands.Choice(name="Black", value="black"),
        ]
    )
    async def af(
        self,
        interaction: discord.Interaction,
        query: str,
        style: str = "default",
    ) -> None:
        await interaction.response.defer()

        try:
            selected_url = self._to_absolute_url(query.strip())
            if selected_url:
                await interaction.followup.send(
                    self._apply_style_to_direct_url(selected_url, style)
                )
                return

            search_query = query.strip()
            if search_query.lower().endswith(".gif"):
                matches = await self.client.search(search_query, limit=10)
                exact_match = next(
                    (
                        item
                        for item in matches
                        if str(item.get("filename", "")).lower() == search_query.lower()
                    ),
                    None,
                )
                picked = exact_match or (matches[0] if matches else None)
            else:
                matches = await self.client.search(search_query, limit=1)
                picked = matches[0] if matches else None

            if not picked:
                await interaction.followup.send(
                    f"No Animation Factory GIFs found for `{search_query}`.",
                    ephemeral=True,
                )
                return

            gif_url = self._resolve_style_url(picked, style)
            if not gif_url:
                await interaction.followup.send(
                    "Animation Factory returned an invalid GIF URL.",
                    ephemeral=True,
                )
                return

            await interaction.followup.send(gif_url)
        except RuntimeError as exc:
            logger.error(f"Animation Factory API error: {exc}")
            await interaction.followup.send(
                f"Could not fetch Animation Factory GIFs right now: {exc}",
                ephemeral=True,
            )
        except Exception as exc:
            logger.error(f"Unexpected /af command error: {exc}", exc_info=True)
            await interaction.followup.send(
                "Something went wrong while searching Animation Factory GIFs.",
                ephemeral=True,
            )

    @af.autocomplete("query")
    async def af_query_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        del interaction

        query = current.strip()
        if not query:
            return []

        try:
            results = await self.client.search(query, limit=25)
        except Exception:
            return []

        choices: list[app_commands.Choice[str]] = []
        for result in results[:25]:
            filename = str(result.get("filename", "")) or "Unknown GIF"
            path_labels = result.get("pathLabels", [])
            if isinstance(path_labels, list) and len(path_labels) >= 3:
                category = f"{path_labels[1]}/{path_labels[2]}"
                label = f"{filename} ({category})"
            else:
                label = filename

            choices.append(
                app_commands.Choice(
                    name=self._truncate(label, 100),
                    value=self._choice_value_from_result(result, fallback=query),
                )
            )

        return choices


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AFCog(bot))
