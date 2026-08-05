"""One-shot producer retraction of spurious anchored assignments (#144 Phase 2).

power-map#391 shipped ``op:"retract"`` on the assignment observation channel; this CLI drives it
for a curated set of local assignment ``source_id``s — the sanctioned way to retire an artifact
tenure usa-wa produced (Wynne LD39 Senate 2001-02) without orphaning the PM anchor. Retracting an
anchored row POSTs the id-addressed ``op:"retract"`` payload and tombstones the local row
(``archived_at``); an unanchored / not-found id is skipped-and-counted; an unexpected disposition
does not tombstone. Retraction is terminal (no reversible ``archived:false``).
"""

from datetime import UTC, date, datetime

import pytest
from ulid import ULID

from clearinghouse_domain_legislative.identity import Assignment, Organization, Person, Role
from clearinghouse_sync_powermap.client import ObservationResult, RetryableClientError
from usa_wa_sync_powermap import retract_assignments as ra


async def _no_sleep(_seconds):
    """Backoff sleep stub — advances the retry loop without real delay."""


class _FakeClient:
    """Records every ``post_observation`` payload; returns a configurable disposition."""

    def __init__(self, disposition="retracted"):
        self._disposition = disposition
        self.posted: list[tuple[str, dict]] = []

    async def post_observation(self, observe_path, payload):
        self.posted.append((observe_path, payload))
        return ObservationResult(disposition=self._disposition, pm_id=None, raw={})

    async def aclose(self):
        pass


async def _add_assignment(session, *, source_id, anchor, is_active=False, archived_at=None):
    org = Organization(
        source="usa_wa_legislature",
        source_id=f"ORG-{source_id}",
        name="Senate",
        org_type="chamber",
        pm_organization_id=ULID(),
    )
    session.add(org)
    await session.flush()
    role = Role(
        source="usa_wa_legislature",
        source_id=f"R-{source_id}",
        organization_id=org.id,
        name="Senator",
        role_type="state_senator",
        pm_role_id=ULID(),
    )
    person = Person(
        source="usa_wa_legislature",
        source_id=f"M-{source_id}",
        name_full="John Wynne",
        pm_person_id=ULID(),
    )
    session.add_all([role, person])
    await session.flush()
    row = Assignment(
        source="usa_wa_legislature",
        source_id=source_id,
        person_id=person.id,
        role_id=role.id,
        valid_from=date(2001, 1, 1),
        valid_to=date(2002, 12, 31),
        is_active=is_active,
        pm_assignment_id=anchor,
        archived_at=archived_at,
    )
    session.add(row)
    await session.flush()
    return row


async def test_retracts_anchored_row_posts_op_retract_and_tombstones(db_session):
    anchor = ULID()
    row = await _add_assignment(
        db_session, source_id="481:chamber-senate:39:2001-02", anchor=anchor
    )
    client = _FakeClient(disposition="retracted")

    result = await ra.retract_assignments(db_session, client, ["481:chamber-senate:39:2001-02"])

    assert client.posted == [
        (
            "/api/v1/assignments/observations",
            {
                "identifier_type": "pm_assignment_id",
                "identifier_value": str(anchor),
                "op": "retract",
            },
        )
    ]
    assert row.archived_at is not None  # tombstoned locally
    assert result["retracted"] == 1
    assert result["not_found"] == 0
    assert result["not_anchored"] == 0
    assert result["unexpected"] == 0


async def test_unanchored_row_is_skipped_not_retracted(db_session):
    row = await _add_assignment(db_session, source_id="481:party:republican:2001-02", anchor=None)
    client = _FakeClient()

    result = await ra.retract_assignments(db_session, client, ["481:party:republican:2001-02"])

    assert client.posted == []  # never POSTed
    assert row.archived_at is None
    assert result["not_anchored"] == 1
    assert result["retracted"] == 0


async def test_unknown_source_id_is_counted_not_found(db_session):
    client = _FakeClient()
    result = await ra.retract_assignments(db_session, client, ["nope:does-not-exist"])
    assert client.posted == []
    assert result["not_found"] == 1
    assert result["retracted"] == 0


async def test_unexpected_disposition_does_not_tombstone(db_session):
    anchor = ULID()
    row = await _add_assignment(
        db_session, source_id="481:chamber-senate:39:2001-02", anchor=anchor
    )
    client = _FakeClient(disposition="rejected")

    result = await ra.retract_assignments(db_session, client, ["481:chamber-senate:39:2001-02"])

    assert len(client.posted) == 1  # attempted
    assert row.archived_at is None  # NOT tombstoned on a non-retracted disposition
    assert result["unexpected"] == 1
    assert result["retracted"] == 0


async def test_auto_attached_is_idempotent_retract_success(db_session):
    """A re-retract of an already-archived tenure returns ``auto-attached`` (PM's already-archived
    no-op, power-map#391) — an idempotent success, so it still tombstones locally."""
    anchor = ULID()
    row = await _add_assignment(
        db_session, source_id="481:chamber-senate:39:2001-02", anchor=anchor
    )
    client = _FakeClient(disposition="auto-attached")

    result = await ra.retract_assignments(db_session, client, ["481:chamber-senate:39:2001-02"])

    assert len(client.posted) == 1
    assert row.archived_at is not None  # already-archived no-op still converges the local tombstone
    assert result["retracted"] == 1
    assert result["unexpected"] == 0


async def test_dry_run_previews_without_posting_or_tombstoning(db_session):
    """``dry_run`` must NOT POST — a retract POST is an irreversible PM mutation a local rollback
    can't undo. It counts ``would_retract`` only, leaving PM + the local row untouched."""
    anchor = ULID()
    row = await _add_assignment(
        db_session, source_id="481:chamber-senate:39:2001-02", anchor=anchor
    )
    client = _FakeClient(disposition="retracted")

    result = await ra.retract_assignments(
        db_session, client, ["481:chamber-senate:39:2001-02"], dry_run=True
    )

    assert client.posted == []  # never POSTed to PM
    assert row.archived_at is None  # not tombstoned
    assert result["would_retract"] == 1
    assert result["retracted"] == 0


async def test_already_archived_row_is_idempotent_no_post(db_session):
    """A target already tombstoned (a completed prior run) is recognised as already-retracted —
    counted ``already_retracted``, NO re-POST — not mis-reported as ``not_found`` (which
    ``live_only`` resolution would do, breaking a clean re-run)."""
    anchor = ULID()
    await _add_assignment(
        db_session,
        source_id="481:chamber-senate:39:2001-02",
        anchor=anchor,
        archived_at=datetime(2026, 8, 5, tzinfo=UTC),
    )
    client = _FakeClient(disposition="retracted")

    result = await ra.retract_assignments(db_session, client, ["481:chamber-senate:39:2001-02"])

    assert client.posted == []  # already retracted → no re-POST
    assert result["already_retracted"] == 1
    assert result["not_found"] == 0
    assert result["retracted"] == 0


async def test_resolve_is_source_scoped(db_session):
    """The natural key is ``(source, source_id)``; a same-``source_id`` row under a different
    source must not be picked up (nor raise ``MultipleResultsFound``)."""
    anchor = ULID()
    await _add_assignment(db_session, source_id="481:chamber-senate:39:2001-02", anchor=anchor)
    # A colliding source_id under a different source (e.g. usa_wa_pdc) — must be ignored.
    org = Organization(
        source="usa_wa_pdc",
        source_id="ORG-pdc-collide",
        name="X",
        org_type="chamber",
        pm_organization_id=ULID(),
    )
    db_session.add(org)
    await db_session.flush()
    role = Role(
        source="usa_wa_pdc",
        source_id="R-pdc-collide",
        organization_id=org.id,
        name="Senator",
        role_type="state_senator",
        pm_role_id=ULID(),
    )
    person = Person(
        source="usa_wa_pdc", source_id="M-pdc-collide", name_full="Other", pm_person_id=ULID()
    )
    db_session.add_all([role, person])
    await db_session.flush()
    db_session.add(
        Assignment(
            source="usa_wa_pdc",
            source_id="481:chamber-senate:39:2001-02",  # same string, different source
            person_id=person.id,
            role_id=role.id,
            valid_from=date(2001, 1, 1),
            valid_to=date(2002, 12, 31),
            is_active=False,
            pm_assignment_id=ULID(),
        )
    )
    await db_session.flush()
    client = _FakeClient(disposition="retracted")

    result = await ra.retract_assignments(db_session, client, ["481:chamber-senate:39:2001-02"])

    # Exactly one POST — the usa_wa_legislature row — not two, and no MultipleResultsFound.
    assert len(client.posted) == 1
    assert client.posted[0][1]["identifier_value"] == str(anchor)
    assert result["retracted"] == 1


def test_exit_code_nonzero_when_targets_unsettled():
    assert ra.exit_code({"not_found": 0, "not_anchored": 0, "unexpected": 0}) == 0
    assert ra.exit_code({"not_found": 1, "not_anchored": 0, "unexpected": 0}) == 1
    assert ra.exit_code({"not_found": 0, "not_anchored": 2, "unexpected": 0}) == 1
    assert ra.exit_code({"not_found": 0, "not_anchored": 0, "unexpected": 3}) == 1


def test_exit_code_tolerates_partial_dict():
    # A missing count key must default to 0, not KeyError (finding 8).
    assert ra.exit_code({}) == 0
    assert ra.exit_code({"not_found": 1}) == 1
    assert ra.exit_code({"unexpected": 2}) == 1


class _FlakyClient:
    """Raises RetryableClientError for the first ``fail_n`` calls, then returns ``disposition``."""

    def __init__(self, *, fail_n, disposition="retracted"):
        self._fail_n = fail_n
        self._disposition = disposition
        self.calls = 0

    async def post_observation(self, observe_path, payload):
        self.calls += 1
        if self.calls <= self._fail_n:
            raise RetryableClientError("429")
        return ObservationResult(disposition=self._disposition, pm_id=None, raw={})


async def test_post_with_backoff_retries_then_succeeds():
    client = _FlakyClient(fail_n=2)
    result = await ra._post_with_backoff(client, {"op": "retract"}, sleep=_no_sleep)
    assert result.disposition == "retracted"
    assert client.calls == 3  # 2 transient failures then success


async def test_post_with_backoff_reraises_after_budget():
    client = _FlakyClient(fail_n=99)  # never recovers
    with pytest.raises(RetryableClientError):
        await ra._post_with_backoff(client, {"op": "retract"}, sleep=_no_sleep)


def test_main_catches_transient_outage(monkeypatch):
    """A persistent 429/5xx that exhausts the backoff surfaces as a clean exit 3, not a
    traceback (finding 7)."""

    async def _boom(_args):
        raise RetryableClientError("429")

    monkeypatch.setattr(ra, "_run", _boom)
    assert ra.main(["--source-id", "x"]) == 3
