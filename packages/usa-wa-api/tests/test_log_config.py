"""Regression tests: uvicorn's own loggers emit the same JSON as the app (#155).

uvicorn ships `uvicorn`, `uvicorn.access`, and `uvicorn.error` with
`propagate=False` and their own plain-text handlers, so `configure_logging()` —
which only rebinds the *root* logger — never reaches them and journald gets
mixed plain-text/JSON lines (gregoryfoster/skills#81). `log_config.json` is the
uvicorn `--log-config` dictConfig that routes all three through the shared
`build_json_formatter`; these tests pin that it stays valid, keeps
single-sourcing the formatter, and stays wired into every uvicorn invocation.
"""

import json
import logging
import logging.config
from pathlib import Path

import usa_wa_api

LOG_CONFIG_PATH = Path(usa_wa_api.__file__).parent / "log_config.json"
REPO_ROOT = Path(__file__).resolve().parents[3]
# The path the ExecStart / dev-server commands must pass to --log-config, spelled
# relative to the repo root (systemd's WorkingDirectory, and where a dev server
# is launched from).
LOG_CONFIG_REL = str(LOG_CONFIG_PATH.relative_to(REPO_ROOT))

UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


def _snapshot(names):
    """Capture mutable logger state so a dictConfig() call can be rolled back.

    dictConfig() mutates live loggers; leaking that into later tests is an
    order-dependent flake, so every attribute it touches is saved.
    """
    saved = {}
    for name in names:
        logger = logging.getLogger(name)
        saved[name] = {
            "handlers": logger.handlers[:],
            "filters": logger.filters[:],
            "propagate": logger.propagate,
            "level": logger.level,
        }
    return saved


def _restore(saved):
    for name, attrs in saved.items():
        logger = logging.getLogger(name)
        for attr, value in attrs.items():
            setattr(logger, attr, value)


def test_log_config_is_valid_and_shares_the_app_formatter():
    """dictConfig accepts the file, and it builds its formatter from the same
    factory configure_logging() uses — no duplicated fmt string to drift.

    A malformed file fails the service at boot, not in review.
    """
    config = json.loads(LOG_CONFIG_PATH.read_text())

    assert any(
        f.get("()") == "clearinghouse_core.logging.build_json_formatter"
        for f in config["formatters"].values()
    )
    # All three uvicorn loggers must be present, else they keep their plain
    # default handler — and each must carry the color_message strip. Placement
    # is asserted, not just the rendered effect: moving the filter onto the
    # stdout handler still produces clean JSON today, and that is the variant
    # that breaks silently under a sink reading record.__dict__ directly.
    for name in UVICORN_LOGGERS:
        assert name in config["loggers"]
        assert "strip_color_message" in config["loggers"][name]["filters"]
        assert config["loggers"][name]["propagate"] is False

    saved = _snapshot(("", *UVICORN_LOGGERS))
    try:
        logging.config.dictConfig(config)  # raises on a malformed config
    finally:
        _restore(saved)


def test_systemd_execstart_passes_the_log_config():
    """The live service must launch uvicorn with --log-config at the real path.

    A typo'd path is not a silent degradation — uvicorn refuses to boot — so
    pinning the exact string keeps the file and its one production consumer
    from drifting apart.
    """
    unit = (REPO_ROOT / "deploy" / "usa-wa.service").read_text()
    exec_start = next(line for line in unit.splitlines() if line.startswith("ExecStart="))
    assert f"--log-config {LOG_CONFIG_REL}" in exec_start


def test_documented_dev_server_commands_pass_the_log_config():
    """Every documented `uvicorn usa_wa_api.api.main:app` invocation carries the
    flag — a dev server without it reproduces the mixed-format logs in the one
    place the fix is most often eyeballed."""
    for doc in ("README.md", "AGENTS.md", "docs/COMMANDS.md"):
        for line in (REPO_ROOT / doc).read_text().splitlines():
            if "uvicorn usa_wa_api.api.main:app" not in line:
                continue
            assert f"--log-config {LOG_CONFIG_REL}" in line, f"{doc}: {line}"
