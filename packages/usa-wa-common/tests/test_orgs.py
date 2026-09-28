"""The structural organizations (#309) — the party Orgs the pipeline registers."""

from usa_wa_common.orgs import STRUCTURAL_ORGS
from usa_wa_common.parties import PARTY_SLUGS


def test_every_party_slug_has_exactly_one_structural_party_org():
    """``PARTY_SLUGS`` and the party rows of ``STRUCTURAL_ORGS`` are one set, both ways.

    The registrar registers every ``STRUCTURAL_ORGS`` key nightly, and the conformed
    ``roles`` model points a party role at ``party-<slug>``. A slug with no Org here
    would bind its party roles to nothing. The dbt model discards ``role_rows``'
    ``unregistered_orgs`` counter; the nightly gate on it runs only after the registrar
    and after publish (``parity_spans`` today, a post-registrar probe once #412 retires
    the oracle), so it reports a gap that has already shipped. This is the offline guard
    that stops it before merge. It replaces the power-map live-Org probe deleted in
    #413, and outlives the Postgres-tier synthesis tests (``test_synthesis``,
    ``test_bootstrap``) that #412 deletes.
    """
    party_orgs = {key for key, org in STRUCTURAL_ORGS.items() if org.org_type == "party"}

    assert party_orgs == {f"party-{slug}" for slug in PARTY_SLUGS}


def test_structural_orgs_declare_whether_they_are_active():
    """``organizations.active`` (#428) for the synthesized anchors is a WA fact, not
    something a wire attests: the legislature and its chambers stand, the two major
    parties seat members today, and the six historical parties are gone for good.
    Seat-holding is not the test — a party with no seated member is still a party —
    so the flag is declared here rather than derived from assignments."""
    active = {key for key, org in STRUCTURAL_ORGS.items() if org.active}

    assert active == {
        "usa_wa_legislature",
        "usa_wa_house",
        "usa_wa_senate",
        "party-democratic",
        "party-republican",
    }
