"""The structural organizations (#309) — the party Orgs the pipeline registers."""

from usa_wa_common.orgs import STRUCTURAL_ORGS
from usa_wa_common.parties import PARTY_SLUGS


def test_every_party_slug_has_exactly_one_structural_party_org():
    """``PARTY_SLUGS`` and the party rows of ``STRUCTURAL_ORGS`` are one set, both ways.

    The registrar registers every ``STRUCTURAL_ORGS`` key nightly, and the conformed
    ``roles`` model points a party role at ``party-<slug>``. A slug with no Org here
    would bind its party roles to nothing, and the only thing that counts it —
    ``role_rows``' ``unregistered_orgs`` — is discarded by the dbt model; ``parity_spans``
    reports it until #412 retires that probe. So this is the offline guard. It replaces
    the power-map live-Org probe deleted in #413, and outlives the Postgres-tier
    synthesis tests (``test_synthesis``, ``test_bootstrap``) that #412 deletes.
    """
    party_orgs = {key for key, org in STRUCTURAL_ORGS.items() if org.org_type == "party"}

    assert party_orgs == {f"party-{slug}" for slug in PARTY_SLUGS}
