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
