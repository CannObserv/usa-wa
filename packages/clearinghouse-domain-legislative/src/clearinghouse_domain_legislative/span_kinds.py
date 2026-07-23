"""Canonical legislative span-kind discriminators (issue #114).

A tenure span's ``kind`` names the dimension it tracks — a Senate seat, a House
seat, a committee membership, or a party affiliation. These strings appear in span
``source_id``\\s, in the ``owned_kinds`` a builder scopes its stale-sweep to, and in
the operator-overlay's ``seat_kind`` validation. They are a **domain** vocabulary
(Layer 2), shared by every Layer-3 builder that produces spans.

Defining them here, once, is what lets the domain's ``SEAT_KINDS`` guard and the
adapters' builders agree by construction: the adapters *import* these constants
rather than re-declaring literals, so a rename lands in exactly one place and cannot
drift (the failure #114 was filed to prevent). See the cross-layer pin in
``usa-wa-adapter-sos/tests/test_span_kinds_guard.py``.
"""

#: Party-affiliation span (the sponsor builder's ``party`` dimension). Not a seat.
KIND_PARTY = "party"

#: Senate seat span (one seat per LD; ``qualifier`` NULL).
KIND_SENATE = "chamber-senate"

#: House seat span (two seats per LD; ``qualifier`` Position 1/2).
KIND_HOUSE = "chamber-house"

#: Committee-membership span (discriminated by the committee's stable WSL ``Id``).
KIND_COMMITTEE = "committee"

#: The seat-scoped span kinds — the seats the builders own (Senate, House, committee).
#: A seat-scoped operator event MUST name one of these (see ``operator_events``); a
#: typo would otherwise record an event the overlay silently no-ops in every builder.
SEAT_KINDS = (KIND_SENATE, KIND_HOUSE, KIND_COMMITTEE)
