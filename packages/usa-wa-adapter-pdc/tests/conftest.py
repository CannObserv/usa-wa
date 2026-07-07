"""Per-package pytest fixtures — PDC SODA cassette infrastructure.

Tests replay ``vcrpy`` cassettes in ``record_mode='none'``: any unmatched HTTP request
is a hard error, so live PDC (data.wa.gov) is never silently contacted.

Re-recording is a deliberate one-shot dev workflow. To re-record a cassette, delete the
target file and run its test with ``PDC_RECORD=1`` set, e.g.::

    PDC_RECORD=1 uv run pytest --no-cov \
        packages/usa-wa-adapter-pdc/tests/test_transport.py -k fetch_house_winners

which flips the fixture to ``record_mode='once'`` for that run (hits live PDC once).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import vcr

CASSETTE_DIR = Path(__file__).parent / "cassettes"


@pytest.fixture
def pdc_vcr() -> vcr.VCR:
    """A pre-configured VCR instance pointed at the package's cassette dir.

    ``record_mode='none'`` (an unmatched request raises) unless ``PDC_RECORD`` is set,
    which flips to ``'once'`` for a deliberate re-record. Query strings participate in
    matching so a different SoQL filter is a distinct cassette.
    """
    record_mode = "once" if os.environ.get("PDC_RECORD") else "none"
    return vcr.VCR(
        cassette_library_dir=str(CASSETTE_DIR),
        record_mode=record_mode,
        match_on=["method", "scheme", "host", "port", "path", "query"],
        decode_compressed_response=True,
    )
