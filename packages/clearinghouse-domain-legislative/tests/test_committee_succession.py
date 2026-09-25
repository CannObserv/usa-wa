"""CommitteeSuccessionEvent model placement (usa-wa#124, #412)."""

from clearinghouse_domain_legislative.committee_succession import CommitteeSuccessionEvent


def test_committee_succession_events_live_in_the_registry_schema():
    """Curated human input sits beside ``registry.adjudications`` (#412 Q1), off the
    ``canonical`` schema PR F drops."""
    assert CommitteeSuccessionEvent.__table__.schema == "registry"
