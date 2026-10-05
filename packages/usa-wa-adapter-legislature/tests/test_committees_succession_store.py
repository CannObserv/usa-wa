"""Committee-succession store: raw-store attestation + idempotency + supersede + read
(usa-wa#124 C2)."""

import hashlib
import json

import pytest
from sqlalchemy import func, select

from clearinghouse_domain_legislative.committee_succession import CommitteeSuccessionEvent
from usa_wa_adapter_legislature.committees.succession_store import (
    current_events,
    record_succession_event,
    succession_source_id,
    supersede_event,
    superseded_events,
)
from usa_wa_adapter_legislature.operators.raw import PendingAttestations

_LINK = dict(
    subject_source_id="14294",
    linked_source_id="28244",
    slug="succeeded_by",
    effective_year=2021,
    evidence_url="https://example.gov/lc",
)


@pytest.fixture
def raw(tmp_path):
    return PendingAttestations.for_operator(tmp_path)


def test_source_id_deterministic_with_and_without_year():
    assert succession_source_id("14294", "28244", "succeeded_by", 2021) == (
        "succeeded_by:14294:28244:2021"
    )
    assert succession_source_id("14294", "28244", "succeeded_by", None) == (
        "succeeded_by:14294:28244"
    )


async def test_record_lands_its_attestation_and_projection(db_session, raw):
    """Committee links share the ``usa_wa_operator`` source, so they reach the raw store
    the way operator events do: the canonical JSON, under its sha256 and the link's key."""
    event = await record_succession_event(
        db_session, raw=raw, notes="renamed", entered_by="greg", **_LINK
    )
    raw.flush()
    assert event.source_id == "succeeded_by:14294:28244:2021"

    latest = raw.store.latest()[event.source_id]
    body = raw.store.object_path(latest["sha256"]).read_bytes()
    assert latest["sha256"] == hashlib.sha256(body).hexdigest()
    assert json.loads(body)["notes"] == "renamed"


async def test_the_raw_buffer_is_required(db_session):
    """The raw store is the link's only provenance since #412 PR F."""
    with pytest.raises(TypeError, match="raw"):
        await record_succession_event(db_session, **_LINK)


async def test_record_is_idempotent_on_natural_key(db_session, raw):
    for _ in range(2):
        await record_succession_event(db_session, raw=raw, **_LINK)
    raw.flush()
    n_events = (
        await db_session.execute(select(func.count()).select_from(CommitteeSuccessionEvent))
    ).scalar_one()
    assert n_events == 1
    assert len(raw.store.manifest_paths()) == 1  # a byte-identical re-ingest archives once


async def test_supersede_relink_stamps_prior_and_appends_new(db_session, raw):
    """A re-link correction (wrong successor) is a distinct natural key: the prior link is
    superseded and a new row created (the create-new + retract-old shape, power-map#322)."""
    prior = await record_succession_event(
        db_session,
        raw=raw,
        **{**_LINK, "linked_source_id": "99999"},  # wrong successor
    )
    corrected = await supersede_event(
        db_session,
        prior,
        raw=raw,
        linked_source_id="28244",  # the real successor
        evidence_url="https://example.gov/lc-fixed",
    )
    assert corrected.id != prior.id
    assert prior.superseded_by_id == corrected.id
    assert corrected.linked_source_id == "28244"
    # Only the corrected link is "current".
    current = await current_events(db_session)
    assert [e.id for e in current] == [corrected.id]
    # The prior (re-linked) row is the producer's retract candidate (#127).
    superseded = await superseded_events(db_session)
    assert [e.id for e in superseded] == [prior.id]


async def test_supersede_can_clear_year(db_session, raw):
    """Passing ``effective_year=None`` explicitly CLEARS the year (a distinct key), vs
    omitting it (inherit prior's) — the sentinel distinguishes the two."""
    prior = await record_succession_event(db_session, raw=raw, **_LINK)
    corrected = await supersede_event(
        db_session, prior, raw=raw, effective_year=None, evidence_url="https://example.gov/fix"
    )
    assert corrected.id != prior.id
    assert corrected.effective_year is None
    assert prior.superseded_by_id == corrected.id


async def test_current_events_excludes_non_operator_source(db_session, raw):
    """The producer's input set is operator attestations only — a stray row under a
    different source must not leak in."""
    await record_succession_event(db_session, raw=raw, **_LINK)
    db_session.add(
        CommitteeSuccessionEvent(
            source="some_other_source",
            source_id="succeeded_by:1:2",
            subject_source_id="1",
            linked_source_id="2",
            slug="succeeded_by",
            evidence_url="https://example.gov/other",
        )
    )
    await db_session.flush()
    current = await current_events(db_session)
    assert [e.source for e in current] == ["usa_wa_operator"]


async def test_supersede_same_key_is_plain_update_not_self_superseded(db_session, raw):
    """A correction that changes only evidence/notes resolves to the prior row (same key) —
    an idempotent update, never self-superseded."""
    prior = await record_succession_event(db_session, raw=raw, **_LINK)
    same = await supersede_event(
        db_session, prior, raw=raw, evidence_url="https://example.gov/lc-better"
    )
    assert same.id == prior.id
    assert prior.superseded_by_id is None
    assert same.evidence_url == "https://example.gov/lc-better"


async def test_supersede_lands_the_correction_in_the_raw_store(db_session, raw):
    prior = await record_succession_event(db_session, raw=raw, **{**_LINK, "effective_year": None})
    corrected = await supersede_event(
        db_session,
        prior,
        raw=raw,
        linked_source_id="28245",
        evidence_url="https://example.gov/lc-2",
    )
    raw.flush()

    assert set(raw.store.latest()) == {prior.source_id, corrected.source_id}


async def test_rerecording_restores_a_lost_raw_copy(db_session, tmp_path):
    """The recovery the post-commit warning names, for a link: a write whose raw copy never
    landed is re-recorded as it stands, and the idempotent write archives its bytes."""
    lost = PendingAttestations.for_operator(tmp_path)
    event = await record_succession_event(db_session, raw=lost, **_LINK)  # never flushed

    raw = PendingAttestations.for_operator(tmp_path)
    again = await record_succession_event(db_session, raw=raw, **_LINK)
    raw.flush()

    assert again.id == event.id
    assert event.source_id in raw.store.latest()
