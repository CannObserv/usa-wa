"""Operator-event store: raw-store attestation + idempotency + supersede + read (#107)."""

import hashlib
import json
from datetime import date

import pytest

from clearinghouse_domain_legislative.operator_events import KIND_DEPARTED
from usa_wa_adapter_legislature.operators.raw import PendingAttestations
from usa_wa_adapter_legislature.operators.store import (
    current_events,
    record_operator_event,
    retract_event,
    supersede_event,
)

_RAMOS = dict(
    member_id="29091",
    kind=KIND_DEPARTED,
    reason="died",
    effective_date=date(2025, 4, 19),
    evidence_url="https://example.gov/ramos",
)


def _archived(raw: PendingAttestations, resource_id: str) -> bytes:
    """The newest bytes the raw store holds for ``resource_id``."""
    return raw.store.object_path(raw.store.latest()[resource_id]["sha256"]).read_bytes()


async def test_record_lands_its_attestation_in_the_raw_store(db_session, tmp_path):
    """The raw store is the attestation's only provenance since #412 PR F: the body is
    the canonical JSON of the event, stored under its sha256 and the event's natural key."""
    raw = PendingAttestations.for_operator(tmp_path)
    event = await record_operator_event(db_session, raw=raw, entered_by="greg", **_RAMOS)
    raw.flush()

    body = _archived(raw, event.source_id)
    assert raw.store.latest()[event.source_id]["sha256"] == hashlib.sha256(body).hexdigest()
    assert json.loads(body) == {
        "member_id": "29091",
        "kind": "departed",
        "reason": "died",
        "effective_date": "2025-04-19",
        "evidence_url": "https://example.gov/ramos",
        "seat_kind": None,
        "seat_discriminator": None,
    }
    assert event.member_id == "29091"


async def test_the_raw_buffer_is_required(db_session):
    """The Postgres half is gone, so a write without a raw buffer would leave the event
    with no provenance at all (#412 PR F made ``raw=`` required)."""
    with pytest.raises(TypeError, match="raw"):
        await record_operator_event(db_session, **_RAMOS)


async def test_record_is_idempotent(db_session, tmp_path):
    raw = PendingAttestations.for_operator(tmp_path)
    first = await record_operator_event(db_session, raw=raw, **_RAMOS)
    second = await record_operator_event(db_session, raw=raw, **_RAMOS)
    raw.flush()

    assert first.id == second.id
    assert len(raw.store.manifest_paths()) == 1
    assert set(raw.store.latest()) == {first.source_id}


async def test_rerecording_restores_a_lost_raw_copy(db_session, tmp_path):
    """The recovery the post-commit warning names: a write whose raw copy never landed is
    re-recorded as it stands, and the idempotent write archives its bytes this time."""
    lost = PendingAttestations.for_operator(tmp_path)
    event = await record_operator_event(db_session, raw=lost, **_RAMOS)  # never flushed

    raw = PendingAttestations.for_operator(tmp_path)
    again = await record_operator_event(db_session, raw=raw, **_RAMOS)
    raw.flush()

    assert again.id == event.id
    assert event.source_id in raw.store.latest()


async def test_supersede_stamps_prior_and_current_excludes_it(db_session, tmp_path):
    raw = PendingAttestations.for_operator(tmp_path)
    prior = await record_operator_event(db_session, raw=raw, **_RAMOS)
    corrected = await supersede_event(
        db_session,
        prior,
        raw=raw,
        reason="died",
        effective_date=date(2025, 4, 20),
        evidence_url="https://example.gov/ramos-official",
    )

    assert prior.superseded_by_id == corrected.id
    current = await current_events(db_session, member_ids=["29091"])
    assert [e.id for e in current] == [corrected.id]


async def test_supersede_lands_the_correction_in_the_raw_store(db_session, tmp_path):
    raw = PendingAttestations.for_operator(tmp_path)
    prior = await record_operator_event(db_session, raw=raw, **_RAMOS)
    corrected = await supersede_event(
        db_session,
        prior,
        raw=raw,
        reason="died",
        effective_date=date(2025, 4, 20),
        evidence_url="https://example.gov/ramos-2",
    )
    raw.flush()

    assert set(raw.store.latest()) == {prior.source_id, corrected.source_id}


class TestSupersedeReclassifies:
    """usa-wa#363. A correction may restate WHICH KIND of ending a boundary was.

    `departed` and `vacated` are two readings of one roster annotation — the member
    left the legislature, or moved seats within it. When the projection changes its
    mind, the correction has to be able to say so; without it the only recourse is
    editing provenance by hand, which #54 exists to forbid.
    """

    async def test_a_departure_can_be_corrected_to_a_seat_vacancy(
        self, db_session, tmp_path
    ) -> None:
        raw = PendingAttestations.for_operator(tmp_path)
        prior = await record_operator_event(
            db_session,
            raw=raw,
            member_id="15809",
            kind="departed",
            reason="resigned",
            effective_date=date(2019, 7, 1),
            evidence_url="https://example.gov/roster#page=22",
        )
        corrected = await supersede_event(
            db_session,
            prior,
            raw=raw,
            kind="vacated",
            reason="moved",
            effective_date=date(2019, 7, 1),
            evidence_url="https://example.gov/roster#page=22",
            seat_kind="chamber-house",
            seat_discriminator="ld-1-position-1",
        )
        assert corrected.kind == "vacated"
        assert corrected.seat_discriminator == "ld-1-position-1"
        assert prior.superseded_by_id == corrected.id

    async def test_a_beginning_can_never_correct_an_ending(self, db_session, tmp_path) -> None:
        """The latitude is within one direction. Turning a departure into a seating
        is not a reclassification, it is a different fact."""
        raw = PendingAttestations.for_operator(tmp_path)
        prior = await record_operator_event(
            db_session,
            raw=raw,
            member_id="15809",
            kind="departed",
            reason="resigned",
            effective_date=date(2019, 7, 1),
            evidence_url="https://example.gov/roster",
        )
        with pytest.raises(ValueError, match="ending"):
            await supersede_event(
                db_session,
                prior,
                raw=raw,
                kind="seated",
                reason="appointed",
                effective_date=date(2019, 7, 1),
                evidence_url="https://example.gov/roster",
                seat_kind="chamber-senate",
                seat_discriminator="1",
            )

    async def test_a_disagreeing_seat_without_a_kind_change_is_refused(
        self, db_session, tmp_path
    ) -> None:
        """CR 140. Without a kind change the seat is the prior's by contract. A caller
        that passes a different one is not silently corrected to the prior's — that
        is the "silently ignored" hazard the CLI guard exists for, and the library
        must not be the layer where it survives."""
        raw = PendingAttestations.for_operator(tmp_path)
        prior = await record_operator_event(
            db_session,
            raw=raw,
            member_id="35410",
            kind="seated",
            reason="appointed",
            effective_date=date(2025, 6, 3),
            evidence_url="https://example.gov/a",
            seat_kind="chamber-senate",
            seat_discriminator="5",
        )
        with pytest.raises(ValueError, match="seat"):
            await supersede_event(
                db_session,
                prior,
                raw=raw,
                reason="appointed",
                effective_date=date(2025, 6, 10),
                evidence_url="https://example.gov/b",
                seat_kind="chamber-senate",
                seat_discriminator="7",
            )

    async def test_reclassifying_to_a_seat_scoped_kind_needs_a_seat(
        self, db_session, tmp_path
    ) -> None:
        """CR 147. A `vacated` names the one seat it closes. Without one,
        `event_source_id` keys the row on placeholders and no overlay can ever
        match it — a silent, unmatchable event on the provenance surface."""
        raw = PendingAttestations.for_operator(tmp_path)
        prior = await record_operator_event(
            db_session,
            raw=raw,
            member_id="15809",
            kind="departed",
            reason="resigned",
            effective_date=date(2019, 7, 1),
            evidence_url="https://example.gov/roster",
        )
        with pytest.raises(ValueError, match="seat"):
            await supersede_event(
                db_session,
                prior,
                raw=raw,
                kind="vacated",
                reason="moved",
                effective_date=date(2019, 7, 1),
                evidence_url="https://example.gov/roster",
            )

    async def test_reclassifying_to_a_person_scoped_kind_refuses_a_seat(
        self, db_session, tmp_path
    ) -> None:
        """The mirror: a `departed` is person-scoped, and a seat handed to it is
        refused rather than written onto a row whose semantics ignore it."""
        raw = PendingAttestations.for_operator(tmp_path)
        prior = await record_operator_event(
            db_session,
            raw=raw,
            member_id="15809",
            kind="vacated",
            reason="moved",
            effective_date=date(2019, 7, 1),
            evidence_url="https://example.gov/roster",
            seat_kind="chamber-house",
            seat_discriminator="ld-1-position-1",
        )
        with pytest.raises(ValueError, match="seat"):
            await supersede_event(
                db_session,
                prior,
                raw=raw,
                kind="departed",
                reason="resigned",
                effective_date=date(2019, 7, 1),
                evidence_url="https://example.gov/roster",
                seat_kind="chamber-house",
                seat_discriminator="ld-1-position-1",
            )

    async def test_a_superseded_row_cannot_be_superseded_again(self, db_session, tmp_path) -> None:
        """CR 148, the invariant under the batch fix: `superseded_by_id` is a chain
        link, and re-stamping it orphans the correction it pointed at. A caller
        that reaches a superseded row is looking at the wrong row."""
        raw = PendingAttestations.for_operator(tmp_path)
        prior = await record_operator_event(
            db_session,
            raw=raw,
            member_id="29091",
            kind="departed",
            reason="died",
            effective_date=date(2025, 4, 19),
            evidence_url="https://example.gov/a",
        )
        first = await supersede_event(
            db_session,
            prior,
            raw=raw,
            reason="died",
            effective_date=date(2025, 4, 20),
            evidence_url="https://example.gov/b",
        )
        with pytest.raises(ValueError, match="already superseded"):
            await supersede_event(
                db_session,
                prior,
                raw=raw,
                reason="died",
                effective_date=date(2025, 4, 21),
                evidence_url="https://example.gov/c",
            )
        assert prior.superseded_by_id == first.id

    async def test_the_kind_still_defaults_to_the_prior(self, db_session, tmp_path) -> None:
        raw = PendingAttestations.for_operator(tmp_path)
        prior = await record_operator_event(
            db_session,
            raw=raw,
            member_id="29091",
            kind="departed",
            reason="died",
            effective_date=date(2025, 4, 19),
            evidence_url="https://example.gov/a",
        )
        corrected = await supersede_event(
            db_session,
            prior,
            raw=raw,
            reason="died",
            effective_date=date(2025, 4, 20),
            evidence_url="https://example.gov/b",
        )
        assert corrected.kind == "departed"
        assert corrected.seat_kind is None


class TestSeatScopeInvariant:
    """CR 147. kind in SEAT_SCOPED_KINDS <=> both seat parts present, enforced
    where every write path meets: `record_operator_event`. The CLI validates the
    same shape at its boundary; this is the layer that holds when a caller is
    not the CLI."""

    async def test_a_seat_scoped_event_cannot_be_recorded_without_a_seat(
        self, db_session, tmp_path
    ) -> None:
        with pytest.raises(ValueError, match="seat"):
            await record_operator_event(
                db_session,
                raw=PendingAttestations.for_operator(tmp_path),
                member_id="35410",
                kind="seated",
                reason="appointed",
                effective_date=date(2025, 6, 3),
                evidence_url="https://example.gov/a",
            )

    async def test_half_a_seat_is_no_seat(self, db_session, tmp_path) -> None:
        with pytest.raises(ValueError, match="seat"):
            await record_operator_event(
                db_session,
                raw=PendingAttestations.for_operator(tmp_path),
                member_id="35410",
                kind="seated",
                reason="appointed",
                effective_date=date(2025, 6, 3),
                evidence_url="https://example.gov/a",
                seat_kind="chamber-senate",
            )

    async def test_a_person_scoped_event_cannot_carry_a_seat(self, db_session, tmp_path) -> None:
        with pytest.raises(ValueError, match="seat"):
            await record_operator_event(
                db_session,
                raw=PendingAttestations.for_operator(tmp_path),
                member_id="29091",
                kind="departed",
                reason="died",
                effective_date=date(2025, 4, 19),
                evidence_url="https://example.gov/a",
                seat_kind="chamber-senate",
                seat_discriminator="5",
            )


_RETRACTION_URL = "https://example.gov/roster#page=69"


class TestRetract:
    """#468. An event can be wrong with nothing to correct it to: the 2026-08-17 roster
    backfill parsed Betty Sue Morris's 1996 resignation onto Jim Springer's 1993 row,
    so member 656 carries a departure he never made. Superseding needs a corrected
    event for the same member, and there is none. Retraction takes the row out of the
    current set and keeps it, with its retraction archived beside its attestation."""

    async def test_a_retracted_event_is_no_longer_current(self, db_session, tmp_path) -> None:
        raw = PendingAttestations.for_operator(tmp_path)
        event = await record_operator_event(db_session, raw=raw, **_RAMOS)

        retracted = await retract_event(
            db_session, event, raw=raw, evidence_url=_RETRACTION_URL, retracted_by="greg"
        )

        assert retracted.id == event.id
        assert retracted.retracted_at is not None
        assert retracted.superseded_by_id is None
        assert await current_events(db_session) == []

    async def test_the_retraction_lands_in_the_raw_store_under_the_event_key(
        self, db_session, tmp_path
    ) -> None:
        """The raw store is the only provenance, so the retraction is archived as the
        event's newest body: ``latest.json`` then names a body that says it no longer
        stands, and why."""
        raw = PendingAttestations.for_operator(tmp_path)
        event = await record_operator_event(db_session, raw=raw, **_RAMOS)
        await retract_event(
            db_session, event, raw=raw, evidence_url=_RETRACTION_URL, retracted_by="greg"
        )
        raw.flush()

        assert json.loads(_archived(raw, event.source_id)) == {
            "member_id": "29091",
            "kind": "departed",
            "reason": "died",
            "effective_date": "2025-04-19",
            "evidence_url": "https://example.gov/ramos",
            "seat_kind": None,
            "seat_discriminator": None,
            "retracted": True,
            "retraction_evidence_url": _RETRACTION_URL,
            "retracted_by": "greg",
        }

    async def test_a_retraction_keeps_who_attested_the_event(self, db_session, tmp_path) -> None:
        """CR 1: ``entered_by`` is the only record of who attested the event — the body
        does not carry it. Retracting 656 must leave ``roster-pdf-backfill`` on the row:
        it is the evidence of where the bad boundary came from."""
        raw = PendingAttestations.for_operator(tmp_path)
        event = await record_operator_event(
            db_session, raw=raw, entered_by="roster-pdf-backfill", **_RAMOS
        )

        await retract_event(
            db_session, event, raw=raw, evidence_url=_RETRACTION_URL, retracted_by="greg"
        )

        assert event.entered_by == "roster-pdf-backfill"

    async def test_retracting_again_restores_a_lost_raw_copy(self, db_session, tmp_path) -> None:
        """The post-commit recovery: the same retraction re-run is idempotent — it keeps
        the first retraction time and archives the bytes that never landed."""
        lost = PendingAttestations.for_operator(tmp_path)
        event = await record_operator_event(db_session, raw=lost, **_RAMOS)
        first = await retract_event(db_session, event, raw=lost, evidence_url=_RETRACTION_URL)
        stamped = first.retracted_at

        raw = PendingAttestations.for_operator(tmp_path)
        again = await retract_event(db_session, event, raw=raw, evidence_url=_RETRACTION_URL)
        raw.flush()

        assert again.retracted_at == stamped
        assert json.loads(_archived(raw, event.source_id))["retracted"] is True

    async def test_a_superseded_event_cannot_be_retracted(self, db_session, tmp_path) -> None:
        """Its correction is what stands; retracting the prior says nothing about it."""
        raw = PendingAttestations.for_operator(tmp_path)
        prior = await record_operator_event(db_session, raw=raw, **_RAMOS)
        await supersede_event(
            db_session,
            prior,
            raw=raw,
            reason="died",
            effective_date=date(2025, 4, 20),
            evidence_url="https://example.gov/b",
        )
        with pytest.raises(ValueError, match="superseded"):
            await retract_event(db_session, prior, raw=raw, evidence_url=_RETRACTION_URL)

    async def test_a_retracted_event_cannot_be_superseded(self, db_session, tmp_path) -> None:
        raw = PendingAttestations.for_operator(tmp_path)
        event = await record_operator_event(db_session, raw=raw, **_RAMOS)
        await retract_event(db_session, event, raw=raw, evidence_url=_RETRACTION_URL)
        with pytest.raises(ValueError, match="retracted"):
            await supersede_event(
                db_session,
                event,
                raw=raw,
                reason="died",
                effective_date=date(2025, 4, 20),
                evidence_url="https://example.gov/b",
            )

    async def test_a_retracted_event_cannot_be_recorded_again(self, db_session, tmp_path) -> None:
        """Re-recording its natural key would update a row no reader sees and report it
        recorded. Reviving a retracted boundary is a decision, not an upsert."""
        raw = PendingAttestations.for_operator(tmp_path)
        event = await record_operator_event(db_session, raw=raw, **_RAMOS)
        await retract_event(db_session, event, raw=raw, evidence_url=_RETRACTION_URL)
        with pytest.raises(ValueError, match="retracted"):
            await record_operator_event(db_session, raw=raw, **_RAMOS)
