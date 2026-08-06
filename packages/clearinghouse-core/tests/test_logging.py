"""Regression tests: JSON log records carry timestamp, level, and logger name,
and the formatter uvicorn's own loggers share renders their records identically
(#133, #155 / gregoryfoster/skills#69, #81, #82).
"""

import json
import logging

from clearinghouse_core.logging import (
    ColorMessageFilter,
    build_json_formatter,
    configure_logging,
    get_logger,
)


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

    lines = capsys.readouterr().out.strip().splitlines()
    assert lines, "no log output captured"
    record = json.loads(lines[-1])
    assert record["message"] == "hello world"
    assert record["level"] == "WARNING"
    assert record["logger"] == "clearinghouse_core.some.module"
    assert "timestamp" in record


def test_shared_formatter_renders_a_uvicorn_access_record():
    """A uvicorn.access record formats to JSON with the same fields as an app
    record — the request line lands in `message`, not on a plain-text handler.

    uvicorn's own AccessFormatter is deliberately not used: a standard
    %(message)s render already interpolates the access record's %s-args into the
    request line.
    """
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:0", "GET", "/health", "1.1", 200),
        exc_info=None,
    )
    parsed = json.loads(build_json_formatter().format(record))
    assert parsed["logger"] == "uvicorn.access"
    assert parsed["level"] == "INFO"
    assert parsed["message"] == '127.0.0.1:0 - "GET /health HTTP/1.1" 200'
    assert "timestamp" in parsed


def test_color_message_filter_strips_the_extra_at_the_record_source():
    """uvicorn's ANSI-duplicate `color_message` extra never reaches a payload.

    Every `extra=` reaches the JSON payload, so routing uvicorn's loggers
    through the JSON formatter would otherwise carry a second copy of each
    lifecycle message full of escape sequences. Asserted on the record itself,
    not only the rendered JSON: the strip has to hold for any sink, including
    handlers that read `record.__dict__` directly rather than going through a
    logging.Formatter.
    """
    record = logging.LogRecord(
        name="uvicorn.error",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Started server process [%d]",
        args=(4066888,),
        exc_info=None,
    )
    record.color_message = "Started server process [\x1b[36m%d\x1b[0m]"

    assert ColorMessageFilter().filter(record) is True  # never drops a record
    assert not hasattr(record, "color_message")

    parsed = json.loads(build_json_formatter().format(record))
    assert "color_message" not in parsed
    assert parsed["message"] == "Started server process [4066888]"
