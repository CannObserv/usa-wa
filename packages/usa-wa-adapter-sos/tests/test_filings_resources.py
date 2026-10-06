"""SOS filings resource ids — the election-year keying the raw store and staging share."""

from __future__ import annotations

import pytest

from usa_wa_adapter_sos.filings.resources import (
    election_year_from_resource_id,
    whofiled_resource_id,
)

ELECTION_YEAR = 2016


def test_resource_id_round_trips_the_election_year() -> None:
    rid = whofiled_resource_id(ELECTION_YEAR)
    assert rid == "sos-whofiled:201611"
    assert election_year_from_resource_id(rid) == ELECTION_YEAR


def test_election_year_from_unknown_resource_id_raises() -> None:
    with pytest.raises(ValueError, match="unknown resource_id"):
        election_year_from_resource_id("house-winners:2016")
