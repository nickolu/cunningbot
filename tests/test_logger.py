from datetime import datetime
from pathlib import Path

import pytest
from loguru import logger as _loguru_logger

from bot.domain.logger import BaseSink, JSONSink, get_logger


def test_get_logger_returns_singleton() -> None:
    logger1 = get_logger()
    logger2 = get_logger()
    assert logger1 is logger2
    assert type(logger1) is type(_loguru_logger)

def test_jsonsink_writes_jsonl(tmp_path: Path) -> None:
    # Patch the logger to use a temp directory for JSONSink
    from bot.domain import logger as logger_mod
    original_jsonsink = logger_mod.JSONSink
    logger_mod._logger_instance = None  # Reset singleton so new sink is used
    today = datetime.now().date()
    log_file_name = f"test-{today}.jsonl"
    logger_mod.JSONSink = lambda log_dir=None: original_jsonsink(log_dir=str(tmp_path), log_file_name=log_file_name)
    try:
        log = logger_mod.get_logger()
        test_record = {"foo": "bar", "n": 42}
        log.info(test_record)
        log.complete()
    finally:
        logger_mod.JSONSink = original_jsonsink
        logger_mod._logger_instance = None  # Clean up for other tests
    log_file = tmp_path / log_file_name
    assert log_file.exists()
    import json
    content = log_file.read_text(encoding="utf-8")
    found = False
    for line in content.splitlines():
        try:
            obj = json.loads(line)
            msg = obj.get("record", {}).get("message", "")
            if msg == str(test_record):
                found = True
                break
        except Exception:
            continue
    assert found, f"Log message {test_record} not found in log file: {content}"

def test_basesink_protocol() -> None:
    class DummySink:
        def write(self, message: str) -> None:
            self.last = message
    sink: BaseSink = DummySink()
    sink.write("abc")
    assert getattr(sink, "last", None) == "abc"

@pytest.mark.skip(reason="MongoSink is a stub")
def test_mongosink_stub() -> None:
    from bot.domain.logger import MongoSink
    sink = MongoSink()
    assert hasattr(sink, "write")
