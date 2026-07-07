"""Transport tests — SODA ``PDCClient`` against a recorded cassette.

The round-trip test replays a real PDC response and pins the field names + winner shape
the normalizer depends on, and proves the offline re-parser recovers the live parse from
the archived wire (the #56 cache path).
"""

from __future__ import annotations

import pytest
from usa_wa_adapter_pdc.transport import (
    OFFICE_STATE_REPRESENTATIVE,
    PDCClient,
    parse_house_winners,
)

# The 2025-26 biennium's House was elected Nov 2024.
ELECTION_YEAR = 2024


def test_house_winners_params_filter_to_seated_representatives() -> None:
    params = PDCClient.house_winners_params(ELECTION_YEAR)
    assert params["office"] == OFFICE_STATE_REPRESENTATIVE
    assert params["election_year"] == "2024"
    assert "general_election_status='Won in general'" in params["$where"]


def test_app_token_sent_only_when_set() -> None:
    assert "X-App-Token" not in PDCClient()._headers()
    assert PDCClient(app_token="tok")._headers()["X-App-Token"] == "tok"


def test_parse_house_winners_rejects_non_array() -> None:
    # A SODA error/unexpected body is a JSON object, not an array of rows.
    with pytest.raises(ValueError, match="expected a JSON array"):
        parse_house_winners(b'{"error": true, "message": "bad request"}')


@pytest.mark.asyncio
async def test_fetch_house_winners_round_trip(pdc_vcr) -> None:
    with pdc_vcr.use_cassette("campaign_finance_summary_house_2024.yaml"):
        fetch = await PDCClient().fetch_house_winners(ELECTION_YEAR)

    # Non-empty seated-House cohort.
    assert fetch.records, "expected seated House winners"
    assert fetch.wire, "expected pristine archival bytes"

    # Every row is a seated State Representative winner carrying the seat fields the
    # normalizer keys on.
    for row in fetch.records:
        assert row["office"] == OFFICE_STATE_REPRESENTATIVE
        assert row["general_election_status"] == "Won in general"
        assert row["legislative_district"]
        assert row["position"] in {"1", "2"}
        assert row["person_id"]  # the stable PDC person id (person_wa_pdc value)

    # Offline re-parse of the archived wire recovers the live parse (#56 cache path).
    assert parse_house_winners(fetch.wire) == fetch.records
