"""The PM anchor export (#312): base32 crosswalk seed for power-map cutover."""

from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pytest
from ulid import ULID

from clearinghouse_core.registry import KIND_ORG, KIND_PERSON, apply_decision, decide
from clearinghouse_domain_legislative.identity import (
    Assignment,
    Organization,
    Person,
    Role,
)
from usa_wa_pipeline import anchor_export, publish
from usa_wa_pipeline.adjudicate import adjudicate_merge
from usa_wa_pipeline.anchor_export import (
    ANCHOR_COLUMNS,
    ANCHOR_TABLE,
    anchor_rows,
    kind_counts,
    materialize_anchors,
    withheld_for_tombstones,
)


async def _register(db_session, person) -> None:
    """Bind the canonical row's own ULID as its registry entity — what
    `registry_seed` does, and what lets the two stores be joined by id."""
    await apply_decision(
        db_session,
        KIND_PERSON,
        decide(frozenset({f"usa_wa_legislature:{person.source_id}"}), {}),
        registered_by="test",
        entity_id=str(person.id),
    )


async def _anchored_assignment(db_session, person) -> Assignment:
    org = Organization(
        source="usa_wa_legislature", source_id="org-1", name="Org", org_type="chamber"
    )
    db_session.add(org)
    await db_session.flush()
    role = Role(
        source="usa_wa_legislature",
        source_id="role-1",
        organization_id=org.id,
        name="Member",
        role_type="party_member",
    )
    db_session.add(role)
    await db_session.flush()
    assignment = Assignment(
        source="usa_wa_legislature",
        source_id="a-1",
        person_id=person.id,
        role_id=role.id,
        valid_from=date(1977, 1, 1),
        valid_to=date(1985, 1, 11),
        is_active=False,
        pm_assignment_id=ULID(),
    )
    db_session.add(assignment)
    await db_session.flush()
    return assignment


@pytest.mark.db
async def test_anchor_rows_are_base32_pairs(db_session) -> None:
    pm_id = ULID()
    anchored = Person(source="usa_wa_legislature", source_id="1", name_full="A", pm_person_id=pm_id)
    unanchored = Person(source="usa_wa_legislature", source_id="2", name_full="B")
    db_session.add_all([anchored, unanchored])
    await db_session.flush()

    rows = await anchor_rows(db_session)

    assert kind_counts(rows) == {"person": 1, "organization": 0, "role": 0, "assignment": 0}
    [(kind, local_id, pm)] = rows
    assert kind == "person"
    assert local_id == str(anchored.id)
    assert pm == str(pm_id)
    # the PM-side hard requirement: 26-char Crockford base32, never UUID-hex
    assert len(pm) == 26
    assert "-" not in pm
    assert str(unanchored.id) not in {row[1] for row in rows}


@pytest.mark.db
async def test_retired_rows_are_absent_from_the_crosswalk(db_session) -> None:
    """#356: the crosswalk asserts a LIVE mapping, so a locally archived or
    deleted row must not appear — retraction-as-absence, the #302 publication
    contract, applied to the one dataset that was ignoring it.

    It leaked 34 rows to PM: 32 narrow spans their newer deepened anchors
    already supersede, plus the two John Wynne LD-39 claims both sides archived
    on 2026-08-05. Each one asked a human to adjudicate a row neither side
    believes."""
    live = Person(
        source="usa_wa_legislature", source_id="live", name_full="Live", pm_person_id=ULID()
    )
    archived = Person(
        source="usa_wa_legislature",
        source_id="arch",
        name_full="Archived",
        pm_person_id=ULID(),
        archived_at=datetime.now(UTC),
    )
    deleted = Person(
        source="usa_wa_legislature",
        source_id="del",
        name_full="Deleted",
        pm_person_id=ULID(),
        deleted_at=datetime.now(UTC),
    )
    db_session.add_all([live, archived, deleted])
    await db_session.flush()

    rows = await anchor_rows(db_session)

    exported = {row[1] for row in rows}
    assert str(live.id) in exported
    assert str(archived.id) not in exported
    assert str(deleted.id) not in exported
    assert kind_counts(rows)["person"] == 1


#: Non-assignment kinds carry an empty `span_key` — the column exists for
#: assignments (usa-wa#370), and `kind` is what tells the two apart.
ROWS = [("person", "01A", "01P", ""), ("role", "01C", "01D", "")]


def _table(db):
    con = duckdb.connect(str(db))
    try:
        columns = [row[0] for row in con.execute(f'describe "{ANCHOR_TABLE}"').fetchall()]
        types = [row[1] for row in con.execute(f'describe "{ANCHOR_TABLE}"').fetchall()]
        rows = con.execute(f'select * from "{ANCHOR_TABLE}" order by all').fetchall()
    finally:
        con.close()
    return columns, types, rows


def test_materialize_builds_the_table_from_rows(tmp_path) -> None:
    """#354 CR 112: the published table is built from the rows in hand, not by
    re-reading the local `--out` artifact. The two sinks stay independent, so
    retiring the `data/anchor-export/` tree is a deletion and not a rewrite."""
    db = tmp_path / "pipeline.duckdb"

    assert materialize_anchors(ROWS, db) == 2

    columns, _, rows = _table(db)
    assert columns == list(ANCHOR_COLUMNS)
    assert rows == [("person", "01A", "01P", ""), ("role", "01C", "01D", "")]


def test_materialize_replaces_rather_than_appends(tmp_path) -> None:
    """A re-export is a REPLACEMENT — the table is the whole live crosswalk, so a
    second run must not double it (retraction-as-absence, the #302 contract)."""
    db = tmp_path / "pipeline.duckdb"
    materialize_anchors(ROWS, db)

    assert materialize_anchors(ROWS, db) == 2
    assert len(_table(db)[2]) == 2


def test_materialize_types_every_column_as_text(tmp_path) -> None:
    """The PM encoding gotcha, one layer down: a ULID that happens to be all
    digits must stay a 26-char string, never a numeric column."""
    numeric = "01234567890123456789012345"
    db = tmp_path / "pipeline.duckdb"

    materialize_anchors([("person", numeric, numeric, numeric)], db)

    _, types, rows = _table(db)
    assert set(types) == {"VARCHAR"}
    assert rows == [("person", numeric, numeric, numeric)]


def test_export_and_publish_share_one_db_resolver() -> None:
    """#354 CR 110: export and publish must name the SAME duckdb. Asserting two
    copies of a literal agree is not that assertion — it passes while they drift.
    One resolver, referenced by both, is."""
    assert anchor_export.pipeline_db_path is publish.pipeline_db_path

    monkey = publish.pipeline_db_path
    assert monkey(None) == Path("data/pipeline.duckdb")
    assert monkey("/explicit.duckdb") == Path("/explicit.duckdb")


def test_materialize_of_an_empty_crosswalk_declares_the_columns(tmp_path) -> None:
    """The #314 shape: once the pm_* columns are dropped the export goes empty.
    That must land as an empty TABLE — which the publisher's shrink gate refuses
    as a 100% contraction — not as a missing one, which reads as a build failure
    and refuses the whole catalog for a different, misleading reason."""
    db = tmp_path / "pipeline.duckdb"

    assert materialize_anchors([], db) == 0

    columns, types, rows = _table(db)
    assert columns == list(ANCHOR_COLUMNS)
    assert set(types) == {"VARCHAR"}
    assert rows == []


def test_materialize_rejects_a_row_that_does_not_match_the_columns(tmp_path) -> None:
    """A short/long row is a caller bug, not something to pad or truncate."""
    with pytest.raises(ValueError, match="does not match columns"):
        materialize_anchors([("person", "01A")], tmp_path / "pipeline.duckdb")


def test_kind_counts_reports_every_kind_and_rejects_unknown_ones() -> None:
    """Per-kind totals are what PM verifies its cohort against (3,118 persons),
    and the catalog carries only a single `rows` total — so they live in the
    job's counters now, zeros included, rather than being inferred from absence."""
    assert kind_counts([("person", "01A", "01P"), ("person", "01B", "01Q")]) == {
        "person": 2,
        "organization": 0,
        "role": 0,
        "assignment": 0,
    }
    with pytest.raises(ValueError, match="unknown anchor kind"):
        kind_counts([("bogus", "01C", "01D")])


@pytest.mark.db
async def test_a_tombstoned_entitys_anchor_is_not_exported(db_session) -> None:
    """#368: a merge retires an id, and a retired id stops being addressable.

    `archived_at`/`deleted_at` (#356) are the LOCAL retraction signals; a
    registry tombstone is a third, and this export saw none of it. The #366 Heck
    merge was the first case: the loser's canonical row is neither archived nor
    deleted — nothing happened to it locally — so its anchor kept shipping and
    pointed at a PM row #514 then deleted. Re-seeding PM's crosswalk from that
    export blocks twice over: two usa-wa ids landing on one PM row reads as "PM
    merged what the producer holds apart", and once PM's tombstone retention
    lapses the id resolves as `missing`, which is unresolvable.
    """
    survivor = Person(
        source="usa_wa_legislature", source_id="live", name_full="Survivor", pm_person_id=ULID()
    )
    loser = Person(
        source="usa_wa_legislature", source_id="merged", name_full="Loser", pm_person_id=ULID()
    )
    db_session.add_all([survivor, loser])
    await db_session.flush()

    # the registry's own tombstone — the ids ARE the canonical ULIDs (the seed
    # preserved them), which is what lets this filter join the two stores at all
    for person in (survivor, loser):
        await _register(db_session, person)
    await adjudicate_merge(
        db_session, KIND_PERSON, loser=str(loser.id), survivor=str(survivor.id), note="#368 test"
    )

    rows = await anchor_rows(db_session)

    assert str(survivor.id) in {row[1] for row in rows}
    assert str(loser.id) not in {row[1] for row in rows}


@pytest.mark.db
async def test_the_losers_assignment_anchor_survives_the_merge(db_session) -> None:
    """Only the ENTITY id retires. PM's merge keeps an assignment's own id and
    only changes whose it is, so that anchor still resolves and dropping it
    would retract a mapping both sides still believe (#368)."""
    survivor = Person(
        source="usa_wa_legislature", source_id="live", name_full="Survivor", pm_person_id=ULID()
    )
    loser = Person(
        source="usa_wa_legislature", source_id="merged", name_full="Loser", pm_person_id=ULID()
    )
    db_session.add_all([survivor, loser])
    await db_session.flush()
    for person in (survivor, loser):
        await _register(db_session, person)
    await adjudicate_merge(
        db_session, KIND_PERSON, loser=str(loser.id), survivor=str(survivor.id), note="#368 test"
    )
    assignment = await _anchored_assignment(db_session, loser)

    rows = await anchor_rows(db_session)

    assert (("assignment", str(assignment.id), str(assignment.pm_assignment_id))) in rows


@pytest.mark.db
async def test_a_tombstoned_organizations_anchor_is_not_exported(db_session) -> None:
    """CR 16: the rule is not person-only, and nothing pinned the other half.

    `anchor_rows` screens all three registry kinds, but every test covered
    persons — so narrowing the loop back to `KIND_PERSON` would have left the
    suite green while re-opening exactly this defect for organizations, which is
    the half #368 names out loud ("a tombstoned person **or organization**").
    Committee merges are the live case: the lineage work (#124) retires bodies
    into their successors.
    """
    survivor = Organization(
        source="usa_wa_legislature",
        source_id="live-org",
        name="Survivor",
        org_type="committee",
        pm_organization_id=ULID(),
    )
    loser = Organization(
        source="usa_wa_legislature",
        source_id="merged-org",
        name="Loser",
        org_type="committee",
        pm_organization_id=ULID(),
    )
    db_session.add_all([survivor, loser])
    await db_session.flush()
    for org in (survivor, loser):
        await apply_decision(
            db_session,
            KIND_ORG,
            decide(frozenset({f"usa_wa_legislature:{org.source_id}"}), {}),
            registered_by="test",
            entity_id=str(org.id),
        )
    await adjudicate_merge(
        db_session, KIND_ORG, loser=str(loser.id), survivor=str(survivor.id), note="#368 CR 16"
    )

    rows = await anchor_rows(db_session)

    assert str(survivor.id) in {row[1] for row in rows}
    assert str(loser.id) not in {row[1] for row in rows}


@pytest.mark.db
async def test_the_withheld_tombstones_are_counted(db_session) -> None:
    """CR 17: the export withholds silently, and this is what names the cause.

    A bulk adjudication shrinks `pm_anchors`; a shrink past `max_shrink` refuses
    the publish, and a refused publish mints nothing at all. The refusal names
    this dataset's before/after counts, so it is findable — this counter is what
    tells the operator the rows went to MERGES rather than to the #356 archival
    screen.
    """
    survivor = Person(
        source="usa_wa_legislature", source_id="live", name_full="Survivor", pm_person_id=ULID()
    )
    loser = Person(
        source="usa_wa_legislature", source_id="merged", name_full="Loser", pm_person_id=ULID()
    )
    db_session.add_all([survivor, loser])
    await db_session.flush()
    assert await withheld_for_tombstones(db_session) == 0

    for person in (survivor, loser):
        await _register(db_session, person)
    await adjudicate_merge(
        db_session, KIND_PERSON, loser=str(loser.id), survivor=str(survivor.id), note="#368 CR 17"
    )

    assert await withheld_for_tombstones(db_session) == 1
    # and it counts the SAME rows the export dropped, which is the only claim
    # that makes the counter worth reading
    assert len(await anchor_rows(db_session)) == 1


@pytest.mark.db
async def test_a_locally_retired_row_is_not_counted_as_a_tombstone(db_session) -> None:
    """The two screens stay distinguishable: an archived row is #356's, not
    #368's, and conflating them would make the counter lie about the cause."""
    archived = Person(
        source="usa_wa_legislature",
        source_id="arch",
        name_full="Archived",
        pm_person_id=ULID(),
        archived_at=datetime.now(UTC),
    )
    db_session.add(archived)
    await db_session.flush()
    await _register(db_session, archived)

    assert await withheld_for_tombstones(db_session) == 0


def _assignments_table(db: Path, rows: list[tuple[str, str, str, str, str, str]]) -> None:
    """The built conformed `assignments`, reduced to the columns the join reads."""
    con = duckdb.connect(str(db))
    try:
        con.execute(
            "create or replace table assignments (source varchar, member_id varchar, "
            "span_kind varchar, span_discriminator varchar, span_start_biennium varchar, "
            "span_key varchar)"
        )
        if rows:
            con.executemany("insert into assignments values (?, ?, ?, ?, ?, ?)", rows)
    finally:
        con.close()


def test_an_assignment_anchor_copies_the_published_key_rather_than_deriving_it(tmp_path) -> None:
    """usa-wa#370 / power-map#490: the key is taken FROM the dataset.

    PM asked for one producer-serialized column so the two sides never disagree
    about how five fields become one string. Re-deriving it here would be a second
    implementation of that rule — and worse, a second implementation of the
    registry lookup the conformed tier does, which resolves merge tombstones. A
    join makes divergence unrepresentable rather than unlikely.
    """
    db = tmp_path / "pipeline.duckdb"
    _assignments_table(
        db,
        [
            (
                "usa_wa_legislature",
                "31521",
                "party",
                "republican",
                "2021-22",
                "01ENT|party-republican-member|party|republican|2021-22",
            )
        ],
    )

    keyed, counts = anchor_export.attach_span_keys(
        [("assignment", "01LOCAL", "01PM")],
        {"01LOCAL": ("usa_wa_legislature", "31521", "party", "republican", "2021-22")},
        db,
    )

    assert counts == {"matched": 1, "absent": 0, "unparseable": 0}
    assert keyed == [
        ("assignment", "01LOCAL", "01PM", "01ENT|party-republican-member|party|republican|2021-22")
    ]


def test_an_anchor_with_no_published_assignment_gets_an_empty_key(tmp_path) -> None:
    """The 384 measured on 2026-09-11 — canonical rows the pipeline stopped
    asserting, #289's collapsed party tails among them.

    PM keeps these anchors deliberately (power-map#490): an unanchored PM row is
    outside the applier's row scope, so withholding them would leave Rob Chase's
    and Jeremie Dufault's superseded party assignments open forever. An empty key
    is the producer saying "absent", which is the archive signal.
    """
    db = tmp_path / "pipeline.duckdb"
    _assignments_table(db, [])

    keyed, counts = anchor_export.attach_span_keys(
        [("assignment", "01GONE", "01PM"), ("person", "01P", "01PMP")],
        {"01GONE": ("usa_wa_legislature", "31521", "party", "republican", "2025-26")},
        db,
    )

    assert keyed == [("assignment", "01GONE", "01PM", ""), ("person", "01P", "01PMP", "")]
    # the person row carries an empty key and is NOT counted — `kind` is what
    # separates "not an assignment" from "an assignment with no published row"
    assert counts == {"matched": 0, "absent": 1, "unparseable": 0}


def test_a_missing_assignments_table_is_refused(tmp_path) -> None:
    """Silently exporting a keyless crosswalk would hand PM a seed it cannot
    re-key from, and the failure would only surface on their side at cutover."""
    db = tmp_path / "pipeline.duckdb"
    duckdb.connect(str(db)).close()

    with pytest.raises(RuntimeError, match="assignments"):
        anchor_export.attach_span_keys(
            [("assignment", "01A", "01B")], {"01A": ("s", "m", "k", "d", "b")}, db
        )


def test_an_unparseable_source_id_is_not_counted_as_absent(tmp_path) -> None:
    """CR 13: `span_key_absent` means one thing, not two.

    An anchor whose `source_id` does not right-split into four parts cannot be
    joined to a published row — but that is a LOCAL key defect, not the
    dataset-absence signal power-map#490 sizes its archive threshold from. Left
    conflated, a key-format or parser regression reaches PM disguised as ordinary
    cutover absence, on the one number they gate on.
    """
    db = tmp_path / "pipeline.duckdb"
    _assignments_table(db, [])

    keyed, counts = anchor_export.attach_span_keys(
        [("assignment", "01BAD", "01PM"), ("assignment", "01GONE", "01PM2")],
        # 01BAD is absent from the join map the way an unparseable key leaves it
        {"01GONE": ("usa_wa_legislature", "31521", "party", "republican", "2025-26")},
        db,
        unparseable={"01BAD"},
    )

    assert [row[3] for row in keyed] == ["", ""]
    assert counts == {"matched": 0, "absent": 1, "unparseable": 1}


def test_a_read_never_creates_the_database(tmp_path) -> None:
    """CR 14: duckdb creates the file it is asked to open, so the missing-table
    guard used to fire only AFTER minting an empty database at a typo'd path —
    which the next run then finds looking real."""
    missing = tmp_path / "not-here.duckdb"

    with pytest.raises(RuntimeError, match="does not exist"):
        anchor_export.attach_span_keys([("assignment", "01A", "01B")], {}, missing)

    assert not missing.exists()
