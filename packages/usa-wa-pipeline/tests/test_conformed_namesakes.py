"""The collision gate's Python half (#378 step 4).

`persons` publishes two entities for one human whenever two seeds mint
separately and nothing reconciles them — 17 such pairs reached power-map before
anyone looked. The merges fixed those; this is what makes the 18th a build
failure instead of a consumer's discovery.

Grouping is by :func:`identity_fold`, the fold the roster↔WSL matcher already
uses, called here rather than restated: a second implementation is what CR 155
consolidated out of `usa_wa_common.names`, and a gate keyed on a private copy of
the fold would drift from the matcher it is meant to backstop.
"""

from __future__ import annotations

import pytest

from usa_wa_adapter_legislature.roster_pdf.identity import IDENTITY_SPLITS
from usa_wa_pipeline.conformed.namesakes import (
    COLLISION_COLUMNS,
    DISTINCT_NAMESAKES,
    allowed_folds,
    collision_rows,
)


def _person(entity_id: str, name_full: str | None, name_source: str | None = "wsl") -> dict:
    return {"entity_id": entity_id, "name_full": name_full, "name_source": name_source}


def test_two_entities_one_fold_is_a_collision() -> None:
    """The defect, in its plainest form: the same name published twice."""
    rows = collision_rows([_person("A", "Patty Murray"), _person("B", "Patty Murray")])
    assert [r["entity_id"] for r in rows] == ["A", "B"]
    assert {r["name_fold"] for r in rows} == {"pattymurray"}


def test_every_colliding_row_is_emitted_not_just_the_loser() -> None:
    """Which id is the survivor is an adjudication, not something a gate may guess.

    The 17 merges went roster → member-id, but that direction came from the
    issue's own reasoning about which side power-map had already resolved. The
    model is the work order; naming only one side would presume the answer.
    """
    rows = collision_rows(
        [_person("A", "John Smith"), _person("B", "John Smith"), _person("C", "John Smith")]
    )
    assert [r["entity_id"] for r in rows] == ["A", "B", "C"]


def test_distinct_names_do_not_collide() -> None:
    assert collision_rows([_person("A", "Patty Murray"), _person("B", "Jack Metcalf")]) == []


def test_the_fold_is_the_matcher_s_not_a_string_compare() -> None:
    """Punctuation, honorifics and quoted nicknames are not identity.

    `E. G. "Pat" Patterson` reached the issue as a pair a consumer matching on
    the name string would miss — one side curly-quoted, the other straight.
    """
    rows = collision_rows(
        [_person("A", 'E. G. "Pat" Patterson'), _person("B", "E. G. “Pat” Patterson")]
    )
    assert len(rows) == 2


def test_a_middle_initial_splits_the_fold() -> None:
    """The gate's known blind spot, stated as a test rather than left to be rediscovered.

    `Stanley Johnson` / `Stanley C. Johnson` and `Gerald "Jerry" Saling` /
    `Gerald L. "Jerry" Saling` were 2 of the 17 and this gate does not see
    either. The fold that would catch them collides 42 groups over the live
    corpus — including `N. B. Atkinson` / `N. P. Atkinson`, which
    `roster_pdf/identity.py` documents as five-ways distinct — so the miss is
    the deliberate half of a trade, not an oversight.
    """
    rows = collision_rows([_person("A", "Stanley Johnson"), _person("B", "Stanley C. Johnson")])
    assert rows == []


def test_an_unnamed_entity_never_collides_with_another() -> None:
    """A null name is the #366 acceptance, not a shared identity.

    Both rows fold to the empty string, which is exactly the shape that would
    group every unnamed entity in the corpus into one giant false collision.
    """
    assert collision_rows([_person("A", None, None), _person("B", None, None)]) == []


def test_an_all_annotation_name_never_collides() -> None:
    """Folds to empty for the same reason a null does, and must be treated the same.

    `persons_unannotated` gates the annotation itself; this is the guard against
    two of them being read as one person on their shared emptiness.
    """
    rows = collision_rows(
        [_person("A", "(Resgnd Dec. 31, 1982)"), _person("B", "(Apntd Dec. 13, 1933)")]
    )
    assert rows == []


@pytest.mark.parametrize(
    ("fold", "name"),
    [("bobmccaslin", "Bob McCaslin"), ("briansullivan", "Brian Sullivan")],
)
def test_an_allowlisted_fold_is_not_reported(fold: str, name: str) -> None:
    """The two WSL-internal namesakes, each verified against the corpus (#378)."""
    assert fold in DISTINCT_NAMESAKES
    assert fold in allowed_folds()
    assert collision_rows([_person("A", name), _person("B", name)]) == []


def test_the_roster_splits_are_allowed_by_derivation_not_by_copy() -> None:
    """A roster split IS a published collision, by construction.

    `IDENTITY_SPLITS` mints two entities from one fold on purpose. Restating
    those folds here would mean the next split added there breaks the nightly
    until someone edits a second file — so the allowlist reads them.
    """
    assert set(IDENTITY_SPLITS) <= allowed_folds()
    assert "elmerejohnston" in allowed_folds()
    rows = collision_rows(
        [_person("A", "Elmer E. Johnston", "roster"), _person("B", "Elmer E. Johnston", "roster")]
    )
    assert rows == []


def test_the_two_allowlists_are_disjoint() -> None:
    """A fold in both is one of them stating something it does not own."""
    assert not (set(DISTINCT_NAMESAKES) & set(IDENTITY_SPLITS))


def test_every_namesake_entry_carries_its_evidence() -> None:
    """An allowlist entry is an adjudication; an unexplained one is an amnesty.

    Allowlisting a fold tells the nightly to stop looking at it forever. The
    reason a human checked has to travel with it or the next reader cannot tell
    a verified namesake from a duplicate someone waved through.
    """
    for fold, why in DISTINCT_NAMESAKES.items():
        assert why.strip(), fold
        assert len(why.split()) >= 5, f"{fold}: {why!r} does not state evidence"


def test_columns_lead_with_the_fold() -> None:
    """The grouping key is what a reader sorts the work order by."""
    assert COLLISION_COLUMNS[0] == "name_fold"
    rows = collision_rows([_person("A", "Patty Murray"), _person("B", "Patty Murray")])
    assert list(rows[0]) == COLLISION_COLUMNS


@pytest.mark.parametrize("missing", ["name_full", "entity_id"])
def test_a_row_missing_a_required_key_is_a_loud_failure(missing: str) -> None:
    """Silently skipping a malformed row would make the gate under-report."""
    row = _person("A", "Patty Murray")
    del row[missing]
    with pytest.raises(KeyError):
        collision_rows([row])
