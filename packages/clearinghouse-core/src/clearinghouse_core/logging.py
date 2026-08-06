"""Structured JSON logging utilities."""

import logging
import sys

from pythonjsonlogger.json import JsonFormatter


def build_json_formatter() -> JsonFormatter:
    """The single JSON formatter definition for the whole process.

    Referenced by BOTH `configure_logging()` (non-uvicorn entry points) and
    `usa_wa_api/log_config.json` (uvicorn's `--log-config`, via the dictConfig
    `"()"` factory key), so app records and uvicorn's own access/error lines
    serialize with one identical schema — no drift, one place to change.

    Keys must be named in the fmt: a bare JsonFormatter() defaults to
    "%(message)s" and emits records with no level, logger, or timestamp.
    """
    return JsonFormatter(
        "%(levelname)s %(name)s %(message)s",
        timestamp=True,
        rename_fields={"levelname": "level", "name": "logger"},
    )


class ColorMessageFilter(logging.Filter):
    """Drop uvicorn's `color_message` extra before anything serializes it.

    uvicorn logs its lifecycle lines with an ANSI-coloured duplicate of the
    message attached as `extra={"color_message": ...}`, for its own
    colour-aware default formatter. Every extra reaches the JSON payload, so
    without this the records carry a second copy of the message full of escape
    sequences — the one thing structured logging exists to avoid.

    A *filter*, not the formatter's `reserved_attrs`, and on the *loggers*
    rather than the handler: both choices put the strip at the record's source,
    before any handler reads it. A handler that builds its payload from the
    record's `__dict__` instead of a `logging.Formatter` would otherwise
    resurrect the field the day the sink changes, silently and with no failing
    test.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Strip the extra if present. Never drops a record."""
        record.__dict__.pop("color_message", None)
        return True


def configure_logging(level: int = logging.INFO) -> None:
    """Configure the root logger with JSON formatting.

    Call once at entry points that do NOT run under uvicorn (the CLIs, alembic
    env, the timer oneshots, tests). Under uvicorn, `--log-config` configures
    the whole logging tree at boot instead; this call is then near-redundant
    (it reinstalls an equivalent root handler), which keeps app logs JSON even
    if someone launches uvicorn without the flag.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(build_json_formatter())
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers = [handler]


def get_logger(name: str) -> logging.Logger:
    """Return a named logger. Use in modules as: logger = get_logger(__name__)"""
    return logging.getLogger(name)
