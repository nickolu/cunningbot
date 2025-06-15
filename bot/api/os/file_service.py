"""
file_service.py
Service for writing binary or text data to files.
"""

from typing import Union
from pathlib import Path
from bot.app.utils.logger import get_logger

logger = get_logger()

class FileService:
    @staticmethod
    def write_bytes(filepath: Union[str, Path], data: bytes) -> None:
        """Write binary data to a file, overwriting if exists."""
        try:
            path = Path(filepath)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("wb") as f:
                f.write(data)
        except Exception as e:
            logger.error(f"Failed to write bytes to {filepath}: {e}")
            raise

    @staticmethod
    def write_text(filepath: Union[str, Path], text: str, encoding: str = "utf-8") -> None:
        """Write text data to a file, overwriting if exists."""
        try:
            path = Path(filepath)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding=encoding) as f:
                f.write(text)
        except Exception as e:
            logger.error(f"Failed to write text to {filepath}: {e}")
            raise
