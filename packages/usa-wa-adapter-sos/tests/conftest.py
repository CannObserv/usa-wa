"""Per-package pytest fixtures — SOS votewa cassette infrastructure.

Tests replay ``vcrpy`` cassettes in ``record_mode='none'``: any unmatched HTTP request is a
hard error, so live votewa (``eledataweb.votewa.gov``) is never silently contacted.

Re-recording is a deliberate one-shot dev workflow. To re-record a cassette, delete the
target file and run its test with ``SOS_RECORD=1`` set, e.g.::

    SOS_RECORD=1 uv run pytest --no-cov \
        packages/usa-wa-adapter-sos/tests/test_transport.py -k fetch_whofiled

which flips the fixture to ``record_mode='once'`` for that run (hits live votewa once).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import vcr
from usa_wa_adapter_sos.transport import configure_sos_rate_limit

CASSETTE_DIR = Path(__file__).parent / "cassettes"


@pytest.fixture(autouse=True)
def _zero_sos_rate_limit() -> None:
    """Disable the central votewa courtesy gate so tests never sleep on it."""
    configure_sos_rate_limit(0)


@pytest.fixture
def sos_vcr() -> vcr.VCR:
    """A pre-configured VCR instance pointed at the package's cassette dir.

    ``record_mode='none'`` (an unmatched request raises) unless ``SOS_RECORD`` is set, which
    flips to ``'once'`` for a deliberate re-record. Query strings participate in matching so a
    different election date is a distinct cassette.
    """
    record_mode = "once" if os.environ.get("SOS_RECORD") else "none"
    return vcr.VCR(
        cassette_library_dir=str(CASSETTE_DIR),
        record_mode=record_mode,
        match_on=["method", "scheme", "host", "port", "path", "query"],
        decode_compressed_response=True,
    )
