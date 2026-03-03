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


class AFPickerView(discord.ui.View):
    def __init__(
        self,
        cog: "AFCog",
        owner_id: int,
        results: list[dict[str, Any]],
        style: str,
        timeout: float = 120,
    ) -> None:
        super().__init__(timeout=timeout)
        self.cog = cog
        self.owner_id = owner_id
        self.results = results
        self.style = style
        self.index = 0
        self.message: discord.Message | None = None
        self._set_nav_button_state()

    def _set_nav_button_state(self) -> None:
        for item in self.children:
            if not isinstance(item, discord.ui.Button):
                continue
            if item.label == "Prev":
                item.disabled = self.index == 0
            elif item.label == "Next":
                item.disabled = self.index >= len(self.results) - 1

    def _selected_result(self) -> dict[str, Any]:
        return self.results[self.index]

    def _selected_url(self) -> str:
        return self.cog._resolve_style_url(self._selected_result(), self.style)

    def _selected_filename(self) -> str:
        return str(self._selected_result().get("filename", "Unknown GIF"))

    def _build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="Animation Factory Results",
            description=f"`{self._selected_filename()}`",
            color=discord.Color.blurple(),
        )
        embed.set_image(url=self._selected_url())
        embed.set_footer(
            text=f"{self.index + 1}/{len(self.results)} • Style: {self.style.title()}"
        )
        return embed

    async def _refresh_message(self, interaction: discord.Interaction) -> None:
        self._set_nav_button_state()
        await interaction.response.edit_message(embed=self._build_embed(), view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only the command user can use this picker.",
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary)
    async def previous_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        del button
        if self.index > 0:
            self.index -= 1
        await self._refresh_message(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        del button
        if self.index < len(self.results) - 1:
            self.index += 1
        await self._refresh_message(interaction)

    @discord.ui.button(label="Send", style=discord.ButtonStyle.success)
    async def send_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        del button
        selected_url = self._selected_url()
        if not selected_url:
            await interaction.response.send_message(
                "This result has an invalid GIF URL.",
                ephemeral=True,
            )
            return

        if not interaction.channel:
            await interaction.response.send_message(
                "Could not access this channel to post the GIF.",
                ephemeral=True,
            )
            return

        try:
            await interaction.channel.send(selected_url)
        except Exception:
            await interaction.response.send_message(
                "I could not post that GIF in this channel.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        try:
            await interaction.delete_original_response()
        except Exception:
            for item in self.children:
                if isinstance(item, discord.ui.Button):
                    item.disabled = True
            await interaction.edit_original_response(
                content="Posted your selected Animation Factory GIF.",
                embed=self._build_embed(),
                view=self,
            )
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        del button
        await interaction.response.defer()
        try:
            await interaction.delete_original_response()
        except Exception:
            for item in self.children:
                if isinstance(item, discord.ui.Button):
                    item.disabled = True
            await interaction.edit_original_response(content="Picker closed.", view=self)
        self.stop()


class AFCommandGroup(app_commands.Group):
    def __init__(self, cog: "AFCog") -> None:
        super().__init__(
            name="af",
            description="Animation Factory GIF commands.",
        )
        self.cog = cog

    @app_commands.command(name="query", description="Search AF GIFs with a preview picker.")
    @app_commands.describe(query="Search query for GIFs", style="GIF variant style")
    @app_commands.choices(
        style=[
            app_commands.Choice(name="Clear", value="clear"),
            app_commands.Choice(name="Default", value="default"),
            app_commands.Choice(name="Black", value="black"),
        ]
    )
    async def query(
        self,
        interaction: discord.Interaction,
        query: str,
        style: str = "clear",
    ) -> None:
        await self.cog.run_af_query(interaction=interaction, query=query, style=style)

    @app_commands.command(
        name="file",
        description="Autocomplete AF GIF filenames and post directly.",
    )
    @app_commands.describe(file="GIF filename to post", style="GIF variant style")
    @app_commands.choices(
        style=[
            app_commands.Choice(name="Clear", value="clear"),
            app_commands.Choice(name="Default", value="default"),
            app_commands.Choice(name="Black", value="black"),
        ]
    )
    async def file(
        self,
        interaction: discord.Interaction,
        file: str,
        style: str = "clear",
    ) -> None:
        await self.cog.run_af_file(interaction=interaction, file=file, style=style)

    @file.autocomplete("file")
    async def file_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return await self.cog.af_file_autocomplete(interaction=interaction, current=current)


class AFCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.client = AnimationFactoryClient()
        self.af_group = AFCommandGroup(self)

    async def cog_load(self) -> None:
        try:
            self.bot.tree.add_command(self.af_group)
        except Exception as exc:
            logger.error(f"Could not register /af group command: {exc}")

    async def cog_unload(self) -> None:
        try:
            self.bot.tree.remove_command("af")
        except Exception:
            pass

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

    async def run_af_query(
        self,
        interaction: discord.Interaction,
        query: str,
        style: str = "clear",
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        try:
            selected_url = self._to_absolute_url(query.strip())
            if selected_url:
                await interaction.followup.send(
                    self._apply_style_to_direct_url(selected_url, style)
                )
                return

            search_query = query.strip()
            if search_query.lower().endswith(".gif"):
                matches = await self.client.search(search_query, limit=25)
                exact_match = next(
                    (
                        item
                        for item in matches
                        if str(item.get("filename", "")).lower() == search_query.lower()
                    ),
                    None,
                )
                if exact_match:
                    matches = [exact_match] + [
                        item for item in matches if item is not exact_match
                    ]
            else:
                matches = await self.client.search(search_query, limit=25)

            if not matches:
                await interaction.followup.send(
                    f"No Animation Factory GIFs found for `{search_query}`.",
                    ephemeral=True,
                )
                return

            view = AFPickerView(
                cog=self,
                owner_id=interaction.user.id,
                results=matches,
                style=style,
            )

            picker_message = await interaction.followup.send(
                content="Pick an Animation Factory GIF to send:",
                embed=view._build_embed(),
                view=view,
                ephemeral=True,
                wait=True,
            )
            view.message = picker_message
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

    async def run_af_file(
        self,
        interaction: discord.Interaction,
        file: str,
        style: str = "clear",
    ) -> None:
        await interaction.response.defer()

        try:
            selected_url = self._to_absolute_url(file.strip())
            if selected_url:
                await interaction.followup.send(
                    self._apply_style_to_direct_url(selected_url, style)
                )
                return

            search_query = file.strip()
            matches = await self.client.search(search_query, limit=25)
            exact_match = next(
                (
                    item
                    for item in matches
                    if str(item.get("filename", "")).lower() == search_query.lower()
                ),
                None,
            )
            picked = exact_match or (matches[0] if matches else None)

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
            logger.error(f"Unexpected /af-file command error: {exc}", exc_info=True)
            await interaction.followup.send(
                "Something went wrong while selecting Animation Factory GIFs.",
                ephemeral=True,
            )

    async def af_file_autocomplete(
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
