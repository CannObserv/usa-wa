"""Regression test: JSON log records carry timestamp, level, and logger name."""

import json
import logging

from clearinghouse_core.logging import configure_logging, get_logger


def test_log_record_includes_structured_fields(capsys):
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    try:
        configure_logging()
        get_logger("clearinghouse_core.some.module").warning("hello %s", "world")
    finally:
        root.handlers = saved_handlers
        # setLevel (not attribute assignment) so manager._clear_cache() runs —
        # child loggers cache their effective level while root sits at INFO.
        root.setLevel(saved_level)

    record = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert record["message"] == "hello world"
    assert record["level"] == "WARNING"
    assert record["logger"] == "clearinghouse_core.some.module"
    assert "timestamp" in record
