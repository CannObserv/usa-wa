"""The seat-occupancy gate (#359): one seat, one holder at a time.

The predicate is exercised by rendering the shipped dbt SQL and running it
against synthetic rows, never by restating it here. A test that retypes the SQL
it checks is two literals with a comment claiming they agree — the defect CR 117
caught one level up, and the reason #358's phantom could have returned unnoticed.

Three files ship it (#360 part A): the macro holding the predicate
(``dbt/macros/seat_overlap_pairs.sql``), which both tests call so neither holds a
copy; the gate (``assignments_seat_occupancy.sql``), which errors on any pair the
named list (``dbt/seeds/accepted_seat_overlaps.csv``) does not accept; and the
stale check (``assignments_seat_occupancy_stale.sql``), which warns on any listed
pair that no longer overlaps. They are rendered with a real Jinja environment
rather than regex-stripped, because the stale check branches on the hermetic
marker and a stripped ``{% if %}`` would run both branches at once.

**Their logic, not their schema** (CR 124). The synthetic table declares its own
columns, so a rename in the `assignments` model leaves these green; the dbt run
is what couples the predicate to the real shape. The split is deliberate — these
cases would otherwise need a built duckdb to run — but it means green here and
a passing `dbt build` say different things, and both are needed.
"""

import csv
from pathlib import Path

import duckdb
import jinja2
import pytest
import yaml

DBT = Path(__file__).resolve().parents[1] / "dbt"
TEST_SQL = DBT / "tests" / "assignments_seat_occupancy.sql"
STALE_SQL = DBT / "tests" / "assignments_seat_occupancy_stale.sql"
MACRO_SQL = DBT / "macros" / "seat_overlap_pairs.sql"
SEED_CSV = DBT / "seeds" / "accepted_seat_overlaps.csv"
SEED_SCHEMA = DBT / "seeds" / "schema.yml"

COLUMNS = (
    "entity_id varchar, role_key varchar, span_kind varchar, valid_from date, valid_to date,"
    " span_key varchar"
)
ROSTER_COLUMNS = "district integer, chamber varchar, year integer, name varchar, annotation varchar"
ACCEPTED_COLUMNS = "span_key_lo varchar, span_key_hi varchar, category varchar, reason varchar"


def _render(path: Path, *, hermetic: bool = False) -> tuple[str, dict]:
    """The shipped SQL rendered as dbt would, against local tables; and its config.

    ``ref`` resolves to a table of the same name, ``env_var`` reads only the
    hermetic marker, and the macro file is loaded ahead of the test so its call
    renders the shipped predicate. An unknown ``env_var`` raises rather than
    defaulting, so a new jinja dependency fails here loudly instead of quietly
    rendering a mangled query.
    """
    config: dict = {}

    def env_var(name: str, default: str | None = None) -> str:
        assert name == "USA_WA_PIPELINE_HERMETIC", f"unexpected env_var {name!r}"
        return "1" if hermetic else "0"

    def capture(**kwargs: object) -> str:
        config.update(kwargs)
        return ""

    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    sql = env.from_string(MACRO_SQL.read_text() + path.read_text()).render(
        ref=lambda name: name, env_var=env_var, config=capture
    )
    return sql, config


def _span_key(row: tuple) -> str:
    """A span_key shaped like the published one (entity, role, start year), unless
    the row carries its own as a sixth element."""
    if len(row) == 6:
        return row[5]
    entity, role, _kind, valid_from, _valid_to = row
    return f"{entity}|{role}|{valid_from[:4]}"


def _run(
    path: Path,
    rows: list[tuple],
    roster: list[tuple] | None = None,
    accepted: list[tuple] | None = None,
    *,
    hermetic: bool = False,
) -> list[tuple]:
    """Run one shipped test over synthetic spans, an optional roster and list.

    The roster drives the multi-member exclusion, so most cases pass none and
    get the default single-member reading — which is the conservative one.
    """
    con = duckdb.connect()
    try:
        con.execute(f"create table assignments ({COLUMNS})")
        if rows:
            con.executemany(
                "insert into assignments values (?, ?, ?, ?, ?, ?)",
                [(*row[:5], _span_key(row)) for row in rows],
            )
        con.execute(f"create table stg_roster_members ({ROSTER_COLUMNS})")
        if roster:
            con.executemany("insert into stg_roster_members values (?, ?, ?, ?, ?)", roster)
        con.execute(f"create table accepted_seat_overlaps ({ACCEPTED_COLUMNS})")
        if accepted:
            con.executemany("insert into accepted_seat_overlaps values (?, ?, ?, ?)", accepted)
        return con.execute(_render(path, hermetic=hermetic)[0]).fetchall()
    finally:
        con.close()


def _conflicts(rows: list[tuple], roster: list[tuple] | None = None) -> int:
    """How many pairs the gate reports, with nothing accepted."""
    return len(_run(TEST_SQL, rows, roster))


def _accept(a: tuple, b: tuple, category: str = "undated") -> tuple:
    """A list entry for the pair (a, b), normalised as the seed stores it."""
    lo, hi = sorted([_span_key(a), _span_key(b)])
    return (lo, hi, category, "test")


def test_the_gate_is_a_plain_error() -> None:
    """The count baseline is gone (#360 part A): every overlap the list does not
    name is an error, and nothing about the count can make one pass.

    A threshold left behind would quietly reinstate the ratchet's weakness —
    one new conflict hidden behind one repaired elsewhere — so the config must
    carry severity alone.
    """
    _sql, config = _render(TEST_SQL)
    assert config == {"severity": "error"}


def test_the_stale_check_warns_rather_than_errors() -> None:
    """A stale entry is housekeeping, not a defect in the published data: it
    must not abort the nightly before publish. `build_warnings` already fails the
    stage on any warn, so the entry is still loud every night until removed."""
    _sql, config = _render(STALE_SQL)
    assert config == {"severity": "warn"}


def _seed() -> list[dict[str, str]]:
    with SEED_CSV.open(newline="") as handle:
        return list(csv.DictReader(handle))


def test_the_list_is_stored_normalised() -> None:
    """Each entry is one unordered pair, stored ``lo < hi`` — the order the gate
    normalises to. An entry stored the other way round would match nothing and
    read as stale, so it fails here first, with its keys."""
    with SEED_CSV.open(newline="") as handle:
        assert next(csv.reader(handle)) == ["span_key_lo", "span_key_hi", "category", "reason"]
    rows = _seed()
    assert rows, "an empty list belongs in a plain gate, not a seed with no rows"
    backwards = [r for r in rows if not r["span_key_lo"] < r["span_key_hi"]]
    assert backwards == []
    pairs = [(r["span_key_lo"], r["span_key_hi"]) for r in rows]
    assert len(pairs) == len(set(pairs)), "a pair is listed twice"


def test_each_entry_names_two_spans_on_one_seat() -> None:
    """A span_key's second part is its role_key. An entry pairing two seats can
    never match an overlap, which only ever joins one role_key to itself."""
    mismatched = [
        r for r in _seed() if r["span_key_lo"].split("|")[1] != r["span_key_hi"].split("|")[1]
    ]
    assert mismatched == []


def test_each_entry_says_why() -> None:
    """The list is the adjudication (#360 part A, #489's set-equality answer):
    an entry without its reason is a silenced alarm, not an accepted one."""
    assert [r for r in _seed() if not r["reason"].strip()] == []


def test_categories_are_the_declared_vocabulary() -> None:
    """The closed set lives once, in the seed's ``accepted_values`` test, which
    the dbt build enforces on the loaded seed; this pins the CSV to it before a
    build has to run."""
    schema = yaml.safe_load(SEED_SCHEMA.read_text())
    (seed,) = [s for s in schema["seeds"] if s["name"] == "accepted_seat_overlaps"]
    (column,) = [c for c in seed["columns"] if c["name"] == "category"]
    (declared,) = [
        t["accepted_values"]["arguments"]["values"]
        for t in column["data_tests"]
        if isinstance(t, dict) and "accepted_values" in t
    ]
    assert {r["category"] for r in _seed()} <= set(declared)


A = ("stevens", "seat:senate:ld-39", "chamber-senate", "1997-01-01", "2012-12-31")
B = ("wynne", "seat:senate:ld-39", "chamber-senate", "2001-01-01", "2002-12-31")
C = ("third", "seat:senate:ld-39", "chamber-senate", "2005-01-01", "2006-12-31")


def test_an_accepted_pair_is_not_a_conflict() -> None:
    assert _run(TEST_SQL, [A, B], accepted=[_accept(A, B)]) == []


def test_acceptance_ignores_which_side_sorts_first() -> None:
    """Pairs are unordered. The gate pairs rows by ``entity_id`` and the list keys
    them by ``span_key``. A published span_key begins with its entity id, so the
    two orders agree today; the gate normalises anyway, so a match never rests on
    that accident of key layout."""
    a = ("aa-entity", "seat:senate:ld-39", "chamber-senate", "1997-01-01", "2012-12-31", "k2")
    b = ("zz-entity", "seat:senate:ld-39", "chamber-senate", "2001-01-01", "2002-12-31", "k1")
    assert _run(TEST_SQL, [a, b], accepted=[_accept(a, b)]) == []


def test_an_accepted_pair_excuses_only_itself() -> None:
    """An entry names one pair, not a seat: a third holder on the same seat is
    still caught against both of the accepted pair's members."""
    rows = _run(TEST_SQL, [A, B, C], accepted=[_accept(A, B)])
    assert len(rows) == 1 and {"stevens", "third"} <= set(rows[0])


def test_a_listed_pair_that_no_longer_overlaps_is_stale() -> None:
    """The list rots unless an entry that stops matching says so by name — the
    #333 / #452 rule. An operator event that dates the boundary resolves the
    pair; the entry must then be removed, and this is what says so."""
    resolved = (B[0], B[1], B[2], "2013-01-01", "2014-12-31")
    entry = _accept(A, resolved)
    assert _run(STALE_SQL, [A, resolved], accepted=[entry]) == [entry]


def test_a_listed_pair_that_still_overlaps_is_not_stale() -> None:
    assert _run(STALE_SQL, [A, B], accepted=[_accept(A, B)]) == []


def test_a_rekeyed_span_reads_as_stale_and_new() -> None:
    """A span_key ends in the start biennium, so a re-dating that crosses one
    re-keys the span. The old entry goes stale and the pair errors under its new
    key: both are reported, and neither is silently carried forward."""
    entry = _accept(A, B)
    redated = (B[0], B[1], B[2], "2003-01-01", "2004-12-31")
    assert _run(STALE_SQL, [A, redated], accepted=[entry]) == [entry]
    assert len(_run(TEST_SQL, [A, redated], accepted=[entry])) == 1


def test_staleness_is_not_judged_in_the_hermetic_build() -> None:
    """The hermetic build materializes conformed models empty, so every entry
    would read as stale on every pre-commit and CI run — the alert fatigue the
    old mode-aware baseline existed to prevent."""
    assert _run(STALE_SQL, [], accepted=[_accept(A, B)], hermetic=True) == []
    assert _run(STALE_SQL, [], accepted=[_accept(A, B)]) != []


def test_catches_the_wynne_shape() -> None:
    """#358: WSL asserted both Stevens and Wynne as Senate LD-39 for 2001-02.
    The roster observes Stevens at term-start 2001 and has no Wynne senate row
    at all, so the phantom is bad upstream data — and nothing in the pipeline
    would have said so. This is that guard."""
    conflicts = _conflicts(
        [
            ("stevens", "seat:senate:ld-39", "chamber-senate", "1997-01-01", "2012-12-31"),
            ("wynne", "seat:senate:ld-39", "chamber-senate", "2001-01-01", "2002-12-31"),
        ]
    )
    assert conflicts == 1


def test_a_shared_boundary_is_a_handoff_not_an_overlap() -> None:
    """A span ending the day its successor begins is how mid-term succession is
    spelled. Counting it would bury real conflicts under clean transitions."""
    assert (
        _conflicts(
            [
                ("pearson", "seat:senate:ld-39", "chamber-senate", "2013-01-01", "2017-11-12"),
                ("wagoner", "seat:senate:ld-39", "chamber-senate", "2017-11-12", None),
            ]
        )
        == 0
    )


def test_an_open_span_still_conflicts() -> None:
    """`valid_to IS NULL` means "still serving", not "no end to compare", so an
    open span overlaps anything starting after it. Without the explicit null
    branch a live conflict hides behind the incumbent's open end."""
    assert (
        _conflicts(
            [
                ("incumbent", "seat:senate:ld-1", "chamber-senate", "2019-01-01", None),
                ("claimant", "seat:senate:ld-1", "chamber-senate", "2021-01-01", "2022-12-31"),
            ]
        )
        == 1
    )


@pytest.mark.parametrize(
    "kind,role", [("party", "party-role:republican"), ("committee", "committee-member-role:7")]
)
def test_multi_holder_kinds_are_not_seats(kind: str, role: str) -> None:
    """Party membership and committee seats are legitimately multi-holder. The
    constraint is stated by KIND, so no exception list has to be maintained."""
    assert (
        _conflicts(
            [
                ("a", role, kind, "2001-01-01", "2002-12-31"),
                ("b", role, kind, "2001-01-01", "2002-12-31"),
            ]
        )
        == 0
    )


def test_one_person_two_spans_in_one_seat_is_not_a_conflict() -> None:
    """A returning member holds the same seat twice. Disjoint spans for ONE
    entity are how an interruption is represented (#358 q3), not a conflict."""
    assert (
        _conflicts(
            [
                ("comfort", "seat:senate:ld-2", "chamber-senate", "1943-01-01", "1952-12-31"),
                ("comfort", "seat:senate:ld-2", "chamber-senate", "1955-01-01", "1956-12-31"),
            ]
        )
        == 0
    )


def test_a_bare_multi_member_district_year_is_not_a_conflict() -> None:
    """#360: WA's 1889 legislature seated multi-member senate districts — LD-19
    carried five, all unannotated. `seat:senate:ld-N` collapses them into one
    role_key, so without this the gate reads five lawful senators as a fight."""
    spans = [
        ("a", "seat:senate:ld-19", "chamber-senate", "1889-01-01", "1890-12-31"),
        ("b", "seat:senate:ld-19", "chamber-senate", "1889-01-01", "1890-12-31"),
    ]
    roster = [(19, "senate", 1889, f"Senator {n}", None) for n in "ABCDE"]

    assert _conflicts(spans, roster) == 0
    # the same spans with no roster evidence stay a conflict: absent proof of a
    # multi-member seat, the conservative reading polices
    assert _conflicts(spans) == 1


def test_an_annotated_extra_row_is_succession_not_capacity() -> None:
    """An annotation means a SUCCESSOR within one seat, so the district is still
    single-member and a genuine overlap there must still be caught. Reading it
    as capacity would license exactly what this gate exists to find."""
    spans = [
        ("pelz", "seat:senate:ld-37", "chamber-senate", "1995-01-01", "1996-12-31"),
        ("kline", "seat:senate:ld-37", "chamber-senate", "1995-06-01", "1996-12-31"),
    ]
    roster = [
        (37, "senate", 1995, "Dwight Pelz", "Resigned January 13, 1997"),
        (37, "senate", 1995, "Adam Kline", "Appointed January 20, 1997"),
    ]
    assert _conflicts(spans, roster) == 1


def test_a_partly_annotated_district_year_is_not_multi_member() -> None:
    """One annotation explains the extra row. Ambiguity resolves toward
    policing, never toward excusing."""
    spans = [
        ("mccutcheon", "seat:senate:ld-29", "chamber-senate", "1971-01-01", "1972-12-31"),
        ("rasmussen", "seat:senate:ld-29", "chamber-senate", "1971-06-01", "1972-12-31"),
    ]
    roster = [
        (29, "senate", 1971, "John T. McCutcheon", "Deceased Aug. 9, 1971"),
        (29, "senate", 1971, "A. L. Rasmussen", None),
    ]
    assert _conflicts(spans, roster) == 1


def test_capacity_must_cover_the_overlap_not_merely_exist() -> None:
    """A district that was multi-member in 1889 is not licensed forever. The
    exclusion applies only where the bare biennium actually covers the overlap."""
    roster = [(19, "senate", 1889, f"Senator {n}", None) for n in "ABCDE"]
    later = [
        ("a", "seat:senate:ld-19", "chamber-senate", "1975-01-01", "1978-12-31"),
        ("b", "seat:senate:ld-19", "chamber-senate", "1976-01-01", "1978-12-31"),
    ]
    assert _conflicts(later, roster) == 1
