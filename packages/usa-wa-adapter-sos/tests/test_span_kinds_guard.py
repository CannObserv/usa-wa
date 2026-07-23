"""Cross-layer pin for the canonical legislative span-kind constants (issue #114).

The seat-kind strings (``chamber-senate`` / ``chamber-house`` / ``committee``) and
the ``party`` span kind are defined **once** in the Layer-2 domain
(:mod:`clearinghouse_domain_legislative.span_kinds`); the Layer-3 builders import
them rather than re-declaring literals. That makes the drift #114 worried about
(``SEAT_KINDS`` silently diverging from a renamed builder constant) structurally
impossible.

Tests may import across layers, so this is the one place we assert the equivalence
end-to-end — a regression tripwire against anyone re-introducing a hardcoded literal
in an adapter. ``usa-wa-adapter-sos`` is the only package that depends on all three
adapters plus the domain, so it is the sole home for the unified assertion.
"""

from clearinghouse_domain_legislative.span_kinds import (
    KIND_COMMITTEE,
    KIND_HOUSE,
    KIND_PARTY,
    KIND_SENATE,
    SEAT_KINDS,
)


def test_seat_kinds_is_the_seat_constant_subset():
    assert SEAT_KINDS == (KIND_SENATE, KIND_HOUSE, KIND_COMMITTEE)


def test_operator_events_reexports_the_same_seat_kinds():
    from clearinghouse_domain_legislative.operator_events import SEAT_KINDS as oe_seat_kinds

    assert oe_seat_kinds is SEAT_KINDS


def test_adapter_span_kinds_are_the_domain_objects():
    from usa_wa_adapter_pdc.normalize.pdc_observations import KIND_HOUSE as pdc_house

    from usa_wa_adapter_legislature.committee_membership_observations import (
        KIND_COMMITTEE as leg_committee,
    )
    from usa_wa_adapter_legislature.sponsor_observations import (
        KIND_PARTY as leg_party,
    )
    from usa_wa_adapter_legislature.sponsor_observations import (
        KIND_SENATE as leg_senate,
    )

    # Identity, not just equality: the hyphenated literals are not auto-interned, so
    # ``is`` proves the adapters import the constant instead of re-declaring it.
    assert leg_party is KIND_PARTY
    assert leg_senate is KIND_SENATE
    assert leg_committee is KIND_COMMITTEE
    assert pdc_house is KIND_HOUSE
