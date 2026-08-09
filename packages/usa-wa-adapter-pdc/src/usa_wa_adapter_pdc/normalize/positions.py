"""PDC-specific identifier keying for the House-position normalizer.

What is left of this module after #189 promoted the WA seat vocabulary and name folding it
also carried (`canonical_position`, `house_seat_role_source_id`, `house_span_discriminator`,
`parse_house_span_discriminator`, `fold_token`, `surname_match_set`) to `usa_wa_common.seats`
and `usa_wa_common.names` — none of which was about PDC, and all of which five
`usa-wa-adapter-sos` modules were importing from here across an adapter boundary.

These three are genuinely PDC's: the source slug PDC-provenance rows carry and the
`person_wa_pdc` child-identifier scheme.
"""

from __future__ import annotations

#: The ``source`` slug PDC-provenance rows (identifiers, House Assignments) carry — matches
#: :attr:`PDCAdapter.source_slug` and the ``Source`` row. Shared by both normalizers so the
#: literal is defined once.
PDC_SOURCE = "usa_wa_pdc"

#: Local ``PersonIdentifier.scheme`` for the PDC person id. The person descriptor maps a
#: Person's ``usa_wa_pdc`` source (and this child scheme) to the PM ``person_wa_pdc``
#: identifier_type; here the identifier is a *child* row on the WSL-sourced Person, carried
#: to PM as an ``additional_identifier``.
PDC_PERSON_ID_SCHEME = "wa_pdc"


def pdc_person_identifier_source_id(pdc_person_id: str) -> str:
    """Deterministic ``PersonIdentifier.source_id`` for the PDC id child row."""
    return f"{pdc_person_id}:{PDC_PERSON_ID_SCHEME}"
