"""Unit tests for the curated member-artifact exclusion denylist (#144).

A small, evidenced denylist of ``(biennium, member_id)`` pairs the WSL ``GetSponsors`` archive
carries spuriously (chamber-conflation / clerical artifacts confirmed against the official WA
Legislature roster). Unioned into the ``exclude_ids_by_biennium`` set the span builders honour so
no rebuild re-derives the spurious span. Distinct from :mod:`sponsors.roster_hygiene` (data-driven,
committee-corroborated departed-ghost exclusion) — this is a manually-curated correction.
"""

from __future__ import annotations

from usa_wa_adapter_legislature.sponsors.artifacts import (
    ARTIFACT_EXCLUSIONS_BY_BIENNIUM,
    with_artifact_exclusions,
)


def test_wynne_ld39_senate_2001_02_is_a_curated_artifact():
    # John Wynne (WSL Id 481) — official roster: House-39 only (1991), never Senate; Val Stevens
    # held LD39 Senate continuously 1997-2012. His 2001-02 sponsor rows are a chamber-conflation
    # artifact (#144).
    assert "481" in ARTIFACT_EXCLUSIONS_BY_BIENNIUM["2001-02"]


def test_with_artifact_exclusions_unions_curated_set_into_empty():
    merged = with_artifact_exclusions({})
    assert "481" in merged["2001-02"]


def test_with_artifact_exclusions_preserves_existing_entries():
    # A caller's stale-exclusion set for the same biennium must be unioned, not replaced.
    merged = with_artifact_exclusions({"2001-02": {"999"}, "2025-26": {"111"}})
    assert merged["2001-02"] == {"999", "481"}
    assert merged["2025-26"] == {"111"}


def test_with_artifact_exclusions_does_not_mutate_caller_dict():
    caller = {"2001-02": {"999"}}
    with_artifact_exclusions(caller)
    assert caller == {"2001-02": {"999"}}  # pure — caller's set untouched
