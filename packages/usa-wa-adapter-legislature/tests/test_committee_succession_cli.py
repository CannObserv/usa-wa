"""Committee-succession CLI (usa-wa#124 C2) — validation + record + supersede + batch."""

import pytest

from clearinghouse_domain_legislative.identity import Organization
from usa_wa_adapter_legislature.committee_succession import (
    LinkSpec,
    SuccessionError,
    load_specs,
    validate_and_record,
)
from usa_wa_adapter_legislature.committee_succession_store import get_or_create_operator_source
from usa_wa_adapter_legislature.provisioning import resolve_jurisdiction


async def _source(session):
    return await get_or_create_operator_source(session, await resolve_jurisdiction(session))


async def _committee(session, source_id):
    session.add(
        Organization(
            source="usa_wa_legislature",
            source_id=source_id,
            name=f"Committee {source_id}",
            org_type="committee",
        )
    )
    await session.flush()


def _link(subject="14294", linked="28244", slug="succeeded_by", year=2021, supersede_id=None):
    return LinkSpec(
        subject_source_id=subject,
        linked_source_id=linked,
        slug=slug,
        evidence_url="https://example.gov/x",
        effective_year=year,
        supersede_id=supersede_id,
    )


async def test_records_a_valid_link(db_session, usa_wa):
    await _committee(db_session, "14294")
    await _committee(db_session, "28244")
    source = await _source(db_session)
    event = await validate_and_record(db_session, source, _link())
    assert event.slug == "succeeded_by"
    assert event.subject_source_id == "14294"
    assert event.linked_source_id == "28244"


async def test_unknown_slug_rejected(db_session, usa_wa):
    source = await _source(db_session)
    with pytest.raises(SuccessionError, match="unknown slug"):
        await validate_and_record(db_session, source, _link(slug="dissolved"))


async def test_identical_ends_rejected(db_session, usa_wa):
    source = await _source(db_session)
    with pytest.raises(SuccessionError, match="must differ"):
        await validate_and_record(db_session, source, _link(subject="14294", linked="14294"))


async def test_unresolvable_subject_rejected(db_session, usa_wa):
    await _committee(db_session, "28244")  # linked exists; subject does not
    source = await _source(db_session)
    with pytest.raises(SuccessionError, match="--subject"):
        await validate_and_record(db_session, source, _link(subject="00000"))


async def test_unresolvable_linked_rejected(db_session, usa_wa):
    await _committee(db_session, "14294")  # subject exists; linked does not
    source = await _source(db_session)
    with pytest.raises(SuccessionError, match="--linked"):
        await validate_and_record(db_session, source, _link(linked="00000"))


async def test_non_committee_org_rejected(db_session, usa_wa):
    """A same-source non-committee org (e.g. a chamber) is not a valid link end."""
    await _committee(db_session, "14294")
    db_session.add(
        Organization(source="usa_wa_legislature", source_id="55", name="House", org_type="chamber")
    )
    await db_session.flush()
    source = await _source(db_session)
    with pytest.raises(SuccessionError, match="--linked"):
        await validate_and_record(db_session, source, _link(linked="55"))


async def test_supersede_relink(db_session, usa_wa):
    await _committee(db_session, "14294")
    await _committee(db_session, "28244")
    await _committee(db_session, "99999")
    source = await _source(db_session)
    prior = await validate_and_record(db_session, source, _link(linked="99999"))
    corrected = await validate_and_record(
        db_session, source, _link(linked="28244", supersede_id=str(prior.id))
    )
    assert corrected.id != prior.id
    assert prior.superseded_by_id == corrected.id


async def test_supersede_slug_mismatch_rejected(db_session, usa_wa):
    await _committee(db_session, "14294")
    await _committee(db_session, "28244")
    source = await _source(db_session)
    prior = await validate_and_record(db_session, source, _link(slug="succeeded_by"))
    with pytest.raises(SuccessionError, match="slug"):
        await validate_and_record(
            db_session,
            source,
            _link(slug="split_from", supersede_id=str(prior.id)),
        )


def test_load_specs_parses_batch():
    specs = load_specs(
        [
            {
                "subject": "14294",
                "linked": "28244",
                "slug": "succeeded_by",
                "year": 2021,
                "evidence_url": "https://x",
            },
            {
                "subject": "20900",
                "linked": "31639",
                "slug": "split_from",
                "evidence_url": "https://y",
            },
        ]
    )
    assert len(specs) == 2
    assert specs[0].effective_year == 2021
    assert specs[1].effective_year is None
    assert specs[1].slug == "split_from"


def test_load_specs_missing_field_rejected():
    with pytest.raises(SuccessionError, match="missing required field"):
        load_specs([{"subject": "1", "linked": "2", "slug": "succeeded_by"}])
