
from typing import List, Optional
import discord


def split_message(text: str, max_length: int = 2000) -> List[str]:
    # Split at the last newline before max_length, or hard split if none
    chunks = []
    while len(text) > max_length:
        split_at = text.rfind('\n', 0, max_length)
        if split_at == -1:
            split_at = max_length
        chunks.append(text[:split_at])
        text = text[split_at:]
    if text:
        chunks.append(text)
    return chunks

def concat_url_params(**kwargs: Optional[str]) -> str:
    return "&".join([f"{key}={value}" for key, value in kwargs.items()])
    