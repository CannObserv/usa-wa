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
    """Disable the global WSL courtesy limiter (#77) so a cassette-replayed SOAP call
    never incurs the production inter-request sleep.

    Lives here, not at the workspace root (#185). CR #77 hoisted it up on the theory
    that a WSL-cassette test could appear in any package; none did — this is the only
    package whose tests construct a real ``WSLClient`` (the sidecar's CLI tests patch
    the seam with fakes). Hoisted, it forced every Layer-1 ``clearinghouse-core`` test
    to import the Layer-3 SOAP transport, which is what kept the unit tier impossible.
    If another package ever drives a live-shaped ``WSLClient``, give it the same
    autouse fixture rather than moving this one back up.
    """
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


@pytest.fixture(scope="session")
def roster_pdf_bytes() -> bytes:
    """The roster-PDF fixture (#225): the source's District 2 pages, font-subset for size.

    A real excerpt rather than a synthetic document — the parser exists to survive this
    publisher's actual layout quirks, so a hand-built PDF would test the wrong thing.
    """
    return (Path(__file__).parent / "fixtures" / "roster_pdf_d2.pdf").read_bytes()
