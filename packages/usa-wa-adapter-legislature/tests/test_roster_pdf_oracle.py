"""The #228 roster oracle — the pure gates a pre-1991 span build must pass.

Moved here from the Phase B builder's tests when #412 PR F deleted the builder: the oracle
outlived it (PR D extracted it to :mod:`usa_wa_adapter_legislature.roster_pdf.oracle`).
"""

from __future__ import annotations

from datetime import date

import pytest

from clearinghouse_domain_legislative.tenure_spans import TenureSpan
from usa_wa_adapter_legislature.roster_pdf.identity import (
    IDENTITY_MINTED,
    RosterIdentity,
)
from usa_wa_adapter_legislature.roster_pdf.normalize import RosterRecord
from usa_wa_adapter_legislature.roster_pdf.oracle import (
    OracleViolation,
    unattested_spans,
    verify_pre1991,
)


def _rec(name: str, year: int, **kw) -> RosterRecord:
    defaults = dict(district=1, chamber="senate", order=1, party_token="D", annotation=None)
    defaults.update(kw)
    return RosterRecord(year=year, name=name, page_number=1, **defaults)


def test_oracle_rejects_person_side_senate_simultaneity() -> None:
    """Oracle item 3, the person side: one member covering two Senate seats in one session
    year is corrupt data, and nothing downstream checks it — abort, with subjects."""
    identity = RosterIdentity(
        disposition=IDENTITY_MINTED,
        fold="xdouble",
        key="xdouble:1901",
        wsl_member_id=None,
        records=(
            _rec("X. Double", 1901, district=1),
            _rec("X. Double", 1901, district=2),
        ),
    )
    with pytest.raises(OracleViolation, match="xdouble"):
        verify_pre1991([identity], [r for r in identity.records])


def _span(member, kind, disc, start="1957-58"):
    return TenureSpan(
        member_id=member,
        kind=kind,
        discriminator=disc,
        start_biennium=start,
        end_biennium=start,
        valid_from=date(1957, 1, 1),
        valid_to=date(1972, 12, 31),
        is_active=False,
    )


def test_a_split_tail_is_not_an_unattested_span() -> None:
    """usa-wa#267. The CR-34 guard compared whole `source_id` sets, so the first real split —
    an added span — read as a synthesized seat and aborted the production build.

    A split tail is citable where a synthesized span is not: it is the same member's same
    tenure after a gap, and the edition lists them in those biennia. The seat, not the count,
    is what separates them.
    """
    built = [_span("huntley", "party", "republican")]
    tail = _span("huntley", "party", "republican", start="1967-68")
    assert unattested_spans([*built, tail], built) == []


def test_a_synthesized_seat_is_still_refused() -> None:
    """The guard must keep its teeth: a seat the wire built nothing for would cite an edition
    that never listed the member there, and this builder has no citation skip list."""
    built = [_span("huntley", "party", "republican")]
    synthesized = _span("huntley", "chamber-senate", "9", start="2025-26")
    assert unattested_spans([*built, synthesized], built) == [synthesized]
