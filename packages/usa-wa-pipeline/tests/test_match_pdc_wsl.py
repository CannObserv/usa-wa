"""The PDC winner ↔ WSL sponsor exact rule, read from the shipped model SQL (#308).

Pins the property #135's early capture relies on: a winner cohort pairs only
with the sponsors of the biennium it SEATS. The Nov 2026 cohort, fetched from
November onward, seats 2027-28 — which has no sponsor roster until the
rollover — so it proposes no link, and the registrar (which never mints a PDC
key alone, #403) registers nothing from it before then.
"""

import re
from pathlib import Path

import duckdb

MODEL_SQL = (
    Path(__file__).resolve().parents[1] / "dbt" / "models" / "matching" / "match_pdc_wsl.sql"
)

WINNER_COLUMNS = (
    "person_id varchar, filer_name varchar, chamber varchar, "
    "legislative_district varchar, election_year bigint"
)
SPONSOR_COLUMNS = (
    "biennium varchar, member_id varchar, agency varchar, district varchar, last_name varchar"
)


def _model() -> str:
    sql = MODEL_SQL.read_text()
    sql = re.sub(r"\{\{\s*ref\('stg_pdc_winners'\)\s*\}\}", "stg_pdc_winners", sql)
    sql = re.sub(r"\{\{\s*ref\('stg_wsl_sponsors'\)\s*\}\}", "stg_wsl_sponsors", sql)
    assert "{{" not in sql and "{%" not in sql, f"unresolved jinja in {MODEL_SQL.name}"
    return sql


def _links(winners: list[tuple], sponsors: list[tuple]) -> list[tuple]:
    con = duckdb.connect(":memory:")
    con.execute(f"create table stg_pdc_winners ({WINNER_COLUMNS})")
    con.execute(f"create table stg_wsl_sponsors ({SPONSOR_COLUMNS})")
    if winners:
        con.executemany("insert into stg_pdc_winners values (?, ?, ?, ?, ?)", winners)
    if sponsors:
        con.executemany("insert into stg_wsl_sponsors values (?, ?, ?, ?, ?)", sponsors)
    return sorted((row[1], row[2]) for row in con.execute(_model()).fetchall())


def _winner(year: int, person_id: str = "P1") -> tuple:
    return (person_id, "RIVERA PAT", "Senate", "14", year)


def _sponsor(biennium: str, member_id: str = "1001") -> tuple:
    return (biennium, member_id, "Senate", "14", "Rivera")


def test_an_even_cohort_pairs_with_the_biennium_it_seats() -> None:
    assert _links([_winner(2024)], [_sponsor("2025-26")]) == [
        ("wa_pdc:P1", "usa_wa_legislature:1001")
    ]


def test_the_next_seating_cohort_pairs_with_nothing_before_the_rollover() -> None:
    """#135: the Nov 2026 cohort, captured early, seats 2027-28. The same senator's 2025-26
    sponsor row must not pair with it: the rule is era-pinned, not person-pinned."""
    assert _links([_winner(2026)], [_sponsor("2025-26")]) == []


def test_the_next_seating_cohort_pairs_once_its_roster_lands() -> None:
    """The forward flow the early capture feeds: once `GetSponsors(2027-28)` has rows, the
    already-archived 2026 cohort pairs in the same build."""
    assert _links([_winner(2026)], [_sponsor("2025-26"), _sponsor("2027-28")]) == [
        ("wa_pdc:P1", "usa_wa_legislature:1001")
    ]
