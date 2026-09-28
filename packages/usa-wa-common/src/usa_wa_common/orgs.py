"""Structural WA legislature organizations (#309): vocabulary, not wire.

The legislature, its two chambers, and the historical party organizations are
synthesized anchors (no source wire carries them); their names, types and
whether each is active (#428) are WA facts. Extracted verbatim from canonical
on 2026-09-03 (``active`` on 2026-09-28) so the conformed tier reproduces the
running system exactly; from now on this module is the source of truth. Keyed
by the ``usa_wa_legislature`` source id the registry crosswalk carries.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StructuralOrg:
    """One synthesized organization: source id, display name, org type, and
    whether it is active (#428).

    ``active`` is declared, not derived: no wire attests a party's or a chamber's
    existence, and seat-holding is not the test (a party with no seated member is
    still a party). The six historical parties are closed facts.
    """

    source_id: str
    name: str
    org_type: str
    active: bool


STRUCTURAL_ORGS: dict[str, StructuralOrg] = {
    org.source_id: org
    for org in (
        StructuralOrg("usa_wa_legislature", "Washington State Legislature", "legislature", True),
        StructuralOrg("usa_wa_house", "Washington State House of Representatives", "chamber", True),
        StructuralOrg("usa_wa_senate", "Washington State Senate", "chamber", True),
        StructuralOrg("party-democratic", "Washington State Democratic Party", "party", True),
        StructuralOrg("party-farmer-labor", "Washington State Farmer-Labor Party", "party", False),
        StructuralOrg("party-peoples", "Washington State People's Party", "party", False),
        StructuralOrg("party-populist", "Washington State Populist Party", "party", False),
        StructuralOrg("party-progressive", "Washington State Progressive Party", "party", False),
        StructuralOrg("party-republican", "Washington State Republican Party", "party", True),
        StructuralOrg(
            "party-silver-republican", "Washington State Silver Republican Party", "party", False
        ),
        StructuralOrg("party-socialist", "Socialist Party of Washington", "party", False),
    )
}
