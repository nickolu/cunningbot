"""Integration tests for /af command."""

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.app.commands.af.af import AFCog


class TestAFCommandAutocomplete:
    @pytest.mark.asyncio
    async def test_autocomplete_returns_choices_from_api(self) -> None:
        mock_bot = MagicMock()
        cog = AFCog(mock_bot)
        cog.client.search = AsyncMock(
            return_value=[
                {
                    "url": "/af/gifs/d15/food/vegetables/potato_walking_hc.gif",
                    "filename": "potato_walking_hc.gif",
                    "pathLabels": ["d15", "food", "vegetables", "potato_walking_hc"],
                }
            ]
        )

        mock_interaction = AsyncMock(spec=discord.Interaction)

        choices = await cog.af_query_autocomplete(mock_interaction, "potato")

        assert len(choices) == 1
        assert choices[0].name.startswith("potato_walking_hc.gif")
        assert choices[0].value == "potato_walking_hc.gif"


class TestAFCommandExecution:
    @pytest.mark.asyncio
    async def test_uses_selected_url_without_search(self) -> None:
        mock_bot = MagicMock()
        cog = AFCog(mock_bot)
        cog.client.search = AsyncMock()

        mock_interaction = AsyncMock(spec=discord.Interaction)
        mock_interaction.response.defer = AsyncMock()
        mock_interaction.followup.send = AsyncMock()

        selected_url = "https://manchat.men/af/gifs/d15/food/vegetables/potato_walking_hc.gif"
        await cog.af.callback(cog, mock_interaction, query=selected_url)

        mock_interaction.response.defer.assert_called_once()
        mock_interaction.followup.send.assert_called_once_with(selected_url)
        cog.client.search.assert_not_called()

    @pytest.mark.asyncio
    async def test_applies_style_to_selected_url(self) -> None:
        mock_bot = MagicMock()
        cog = AFCog(mock_bot)
        cog.client.search = AsyncMock()

        mock_interaction = AsyncMock(spec=discord.Interaction)
        mock_interaction.response.defer = AsyncMock()
        mock_interaction.followup.send = AsyncMock()

        selected_url = "https://manchat.men/af/gifs/d15/food/vegetables/potato_walking_ha.gif"
        await cog.af.callback(cog, mock_interaction, query=selected_url, style="clear")

        mock_interaction.followup.send.assert_called_once_with(
            "https://manchat.men/af/gifs/d15/food/vegetables/potato_walking_hc.gif"
        )
        cog.client.search.assert_not_called()

    @pytest.mark.asyncio
    async def test_searches_and_posts_first_result_for_plain_text(self) -> None:
        mock_bot = MagicMock()
        cog = AFCog(mock_bot)
        cog.client.search = AsyncMock(
            return_value=[
                {
                    "url": "/af/gifs/d15/food/vegetables/potato_walking_hc.gif",
                    "filename": "potato_walking_hc.gif",
                }
            ]
        )

        mock_interaction = AsyncMock(spec=discord.Interaction)
        mock_interaction.response.defer = AsyncMock()
        mock_interaction.followup.send = AsyncMock()

        await cog.af.callback(cog, mock_interaction, query="potato")

        cog.client.search.assert_called_once_with("potato", limit=1)
        mock_interaction.followup.send.assert_called_once_with(
            "https://manchat.men/af/gifs/d15/food/vegetables/potato_walking_hc.gif"
        )

    @pytest.mark.asyncio
    async def test_uses_style_variant_from_api_result(self) -> None:
        mock_bot = MagicMock()
        cog = AFCog(mock_bot)
        cog.client.search = AsyncMock(
            return_value=[
                {
                    "url": "/af/gifs/d15/food/vegetables/potato_walking_ha.gif",
                    "filename": "potato_walking_ha.gif",
                    "variants": [
                        {
                            "label": "Clear",
                            "url": "/af/gifs/d15/food/vegetables/potato_walking_hc.gif",
                        },
                        {
                            "label": "Black",
                            "url": "/af/gifs/d15/food/vegetables/potato_walking_hb.gif",
                        },
                        {
                            "label": "Default",
                            "url": "/af/gifs/d15/food/vegetables/potato_walking_ha.gif",
                        },
                    ],
                }
            ]
        )

        mock_interaction = AsyncMock(spec=discord.Interaction)
        mock_interaction.response.defer = AsyncMock()
        mock_interaction.followup.send = AsyncMock()

        await cog.af.callback(cog, mock_interaction, query="potato", style="black")

        mock_interaction.followup.send.assert_called_once_with(
            "https://manchat.men/af/gifs/d15/food/vegetables/potato_walking_hb.gif"
        )

    @pytest.mark.asyncio
    async def test_handles_no_results(self) -> None:
        mock_bot = MagicMock()
        cog = AFCog(mock_bot)
        cog.client.search = AsyncMock(return_value=[])

        mock_interaction = AsyncMock(spec=discord.Interaction)
        mock_interaction.response.defer = AsyncMock()
        mock_interaction.followup.send = AsyncMock()

        await cog.af.callback(cog, mock_interaction, query="nosuchgif")

        mock_interaction.followup.send.assert_called_once()
        args, kwargs = mock_interaction.followup.send.call_args
        assert "No Animation Factory GIFs found" in args[0]
        assert kwargs["ephemeral"] is True

    @pytest.mark.asyncio
    async def test_handles_api_runtime_error(self) -> None:
        mock_bot = MagicMock()
        cog = AFCog(mock_bot)
        cog.client.search = AsyncMock(side_effect=RuntimeError("request timed out"))

        mock_interaction = AsyncMock(spec=discord.Interaction)
        mock_interaction.response.defer = AsyncMock()
        mock_interaction.followup.send = AsyncMock()

        await cog.af.callback(cog, mock_interaction, query="potato")

        mock_interaction.followup.send.assert_called_once()
        args, kwargs = mock_interaction.followup.send.call_args
        assert "Could not fetch Animation Factory GIFs" in args[0]
        assert kwargs["ephemeral"] is True
