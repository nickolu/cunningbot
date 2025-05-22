"""
logger.py
Logging utilities for the bot.
"""

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Protocol

from loguru import logger as _loguru_logger


# Sinks
class BaseSink(Protocol):
    def write(self, message: str) -> None:
        ...

class JSONSink:
    def __init__(self, log_dir: Optional[str] = None, log_file_name: Optional[str] = None) -> None:
        """
        Initialize a JSONSink that writes log entries to a file in a logs/ directory.
        By default, logs are written to <project_root>/logs/YYYY-MM-DD.jsonl
        Optionally, a custom log_file_name can be provided (e.g., 'test-YYYY-MM-DD.jsonl').
        """
        if log_dir is None:
            # Always use project root logs/ directory
            project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../'))
            log_dir = os.path.join(project_root, 'logs')
        self.log_dir = os.path.abspath(log_dir)
        Path(self.log_dir).mkdir(parents=True, exist_ok=True)
        if log_file_name is None:
            log_file_name = f"{datetime.now().date()}.jsonl"
        self.file_path = os.path.join(self.log_dir, log_file_name)
        self._file = open(self.file_path, "a", encoding="utf-8")

    def write(self, message: str) -> None:
        try:
            self._file.write(message)
            self._file.flush()
        except Exception:
            print(f"Failed to write log message: {message}")

    def __del__(self) -> None:
        try:
            self._file.close()
        except Exception:
            pass

class MongoSink:
    def write(self, message: str) -> None:
        # TODO: Implement MongoDB sink
        pass

# Singleton logger
_logger_instance: Any = None


def get_logger() -> Any:
    global _logger_instance
    if _logger_instance is not None:
        return _logger_instance

    json_sink = JSONSink()
    _loguru_logger.remove()
    _loguru_logger.add(json_sink.write, serialize=True, enqueue=True)
    _logger_instance = _loguru_logger
    return _logger_instance
