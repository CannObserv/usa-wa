"""Structural WA legislature organizations (#309): vocabulary, not wire.

The legislature, its two chambers, and the historical party organizations are
synthesized anchors (no source wire carries them); their names and types are
WA facts. Extracted verbatim from canonical on 2026-09-03 so the conformed
tier reproduces the running system exactly; from now on this module is the
source of truth. Keyed by the ``usa_wa_legislature`` source id the registry
crosswalk carries.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StructuralOrg:
    """One synthesized organization: source id, display name, org type."""

    source_id: str
    name: str
    org_type: str


STRUCTURAL_ORGS: dict[str, StructuralOrg] = {
    org.source_id: org
    for org in (
        StructuralOrg("usa_wa_legislature", "Washington State Legislature", "legislature"),
        StructuralOrg("usa_wa_house", "Washington State House of Representatives", "chamber"),
        StructuralOrg("usa_wa_senate", "Washington State Senate", "chamber"),
        StructuralOrg("party-democratic", "Washington State Democratic Party", "party"),
        StructuralOrg("party-farmer-labor", "Washington State Farmer-Labor Party", "party"),
        StructuralOrg("party-peoples", "Washington State People's Party", "party"),
        StructuralOrg("party-populist", "Washington State Populist Party", "party"),
        StructuralOrg("party-progressive", "Washington State Progressive Party", "party"),
        StructuralOrg("party-republican", "Washington State Republican Party", "party"),
        StructuralOrg(
            "party-silver-republican", "Washington State Silver Republican Party", "party"
        ),
        StructuralOrg("party-socialist", "Socialist Party of Washington", "party"),
    )
}
