"""Per-package pytest fixtures — WSL cassette infrastructure.

The default test tier runs cassettes in ``record_mode='none'``: any unmatched
HTTP request causes a hard error so live WSL is never silently contacted.
Re-recording is a deliberate one-shot dev workflow (see package README).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import vcr

from usa_wa_adapter_legislature.transport import configure_wsl_rate_limit

CASSETTE_DIR = Path(__file__).parent / "cassettes"


@pytest.fixture(autouse=True)
def _no_wsl_rate_limit() -> None:
    """Disable the global WSL courtesy limiter for the suite so cassette-replayed SOAP
    calls don't incur the production inter-request sleep."""
    configure_wsl_rate_limit(0.0)


@pytest.fixture
def wsl_vcr() -> vcr.VCR:
    """A pre-configured VCR instance pointed at the package's cassette dir.

    ``record_mode='none'`` means an unmatched request raises rather than
    silently going live. Body matching is **off** because zeep's SOAP envelope
    serialization is not byte-stable across runs (namespace prefixes shuffle).
    Path matching is sufficient — each cassette is tied to a single SOAP
    operation by its endpoint path.
    """
    return vcr.VCR(
        cassette_library_dir=str(CASSETTE_DIR),
        record_mode="none",
        match_on=["method", "scheme", "host", "port", "path"],
        decode_compressed_response=True,
    )
