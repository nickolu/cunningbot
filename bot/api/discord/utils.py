import discord

_SUPERSCRIPT_MAP = str.maketrans(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-.;",
    "ᵃᵇᶜᵈᵉᶠᵍʰᶦʲᵏˡᵐⁿᵒᵖᑫʳˢᵗᵘᵛʷˣʸᶻᴬᴮᶜᴰᴱᶠᴳᴴᴵᴶᴷᴸᴹᴺᴼᴾQᴿˢᵀᵁⱽᵂˣʸᶻ⁰¹²³⁴⁵⁶⁷⁸⁹⁻ˑˡ"
)

def to_tiny_text(text: str) -> str:
    """
    Convert text to Unicode tiny/superscript text where possible.
    Characters without superscript equivalents are left unchanged.
    """
    return text.translate(_SUPERSCRIPT_MAP)

def flatten_discord_message(message: discord.Message) -> str:
    content = ""
    if isinstance(message.content, str):
        content = message.content
    elif isinstance(message.content, list):
        processed_parts = []
        for part in message.content:
            if isinstance(part, str):
                processed_parts.append(part)
            elif isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                processed_parts.append(part["text"])
            # Other parts (e.g., images) could be handled or logged here if necessary
        content = "\n".join(processed_parts)
    else:
        content = str(message.content) # Fallback
    return content
    