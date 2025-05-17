def chunk_response(response: str, interaction: discord.Interaction, ephemeral: bool):
    try:
        for chunk in split_message(response):
            await interaction.followup.send(chunk, ephemeral=private)
        if not was_default:
            await interaction.followup.send(model_text, ephemeral=private)
    except Exception:
        pass