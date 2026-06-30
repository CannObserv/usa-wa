"""Tests for committee_seed.py — frozen seed (de)serialization (#39)."""

from __future__ import annotations

from usa_wa_adapter_legislature.committee_seed import (
    SeedCommittee,
    deserialize_seed,
    serialize_seed,
)


def _committees() -> list[SeedCommittee]:
    return [
        SeedCommittee("13945", "Joint Joint Committee on Energy …", "…", "ESEC", None),
        SeedCommittee(
            "-140",
            "Joint Joint Transportation Committee",
            "Joint Transportation Committee",
            "JTC",
            "(360) 786-7300",
        ),
        SeedCommittee("-5", "Joint JLARC", "JLARC", "JLARC", None),
    ]


def test_serialize_is_deterministic_and_source_id_sorted():
    """Same cohort → byte-identical seed regardless of input order (stable digest)."""
    a = serialize_seed(_committees(), bienniums=["2023-24", "2025-26"])
    b = serialize_seed(list(reversed(_committees())), bienniums=["2023-24", "2025-26"])
    assert a == b
    # Rows are ordered by source_id (string sort): "-140" < "-5" < "13945".
    text = a.decode("utf-8")
    assert text.index('"-140"') < text.index('"-5"') < text.index('"13945"')


def test_roundtrip_preserves_fields():
    content = serialize_seed(_committees(), bienniums=["2023-24"])
    back = deserialize_seed(content)
    by_id = {c.source_id: c for c in back}
    assert by_id["-140"].name == "Joint Joint Transportation Committee"
    assert by_id["-140"].acronym == "JTC"
    assert by_id["-140"].phone == "(360) 786-7300"
    assert by_id["-5"].phone is None


def test_unicode_names_survive_without_escaping():
    seed = [SeedCommittee("1", "Salish — Tribal Affairs", "Salish — Tribal Affairs", None, None)]
    content = serialize_seed(seed, bienniums=["2025-26"])
    assert "Salish — Tribal Affairs".encode() in content  # ensure_ascii=False
    assert deserialize_seed(content)[0].name == "Salish — Tribal Affairs"
