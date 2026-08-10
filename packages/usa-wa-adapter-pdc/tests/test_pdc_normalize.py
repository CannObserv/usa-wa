"""PDC-specific identifier keying — what is left after #189 promoted the shared vocabulary."""

from __future__ import annotations

from usa_wa_adapter_pdc.normalize.positions import (
    PDC_PERSON_ID_SCHEME,
    pdc_person_identifier_source_id,
)


def test_pdc_person_identifier_source_id_scoped_by_scheme() -> None:
    assert pdc_person_identifier_source_id("159") == f"159:{PDC_PERSON_ID_SCHEME}"
    assert PDC_PERSON_ID_SCHEME == "wa_pdc"
