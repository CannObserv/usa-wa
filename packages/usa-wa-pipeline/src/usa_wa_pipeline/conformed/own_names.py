"""A person's own name, where the roster prints a married-name form (#384, #501 D9).

``name_full`` keeps publishing the roster's printed form unchanged — the faithful
record — and ``identity_fold`` and the registry keys keep reading it raw (#378's
separation). ``name_own`` carries the person's own name beside it, for exactly the
rows adjudicated here, and is null on every other row: it means "the source prints
a married-name form, and this is her own name", not a general display name.
Consumers read ``coalesce(name_own, name_full)``.

**A curated table, not a parser** (#384, owner 2026-09-23). The forms are not one
pattern, and ``Mrs. Jurie B.(Nettie Luella) Smith`` needed a human call on which
name is hers. Each entry is keyed by the roster natural key, pins the printed form
it adjudicated, and states its class:

- **A** — her own given name is printed inside the marital form: extracted.
- **B** — only her husband's name is printed: null. Nothing is invented.
- **C** — an honorific on her own name: dropped.

A new edition that prints another form withholds the value rather than attaching
a stale one, and the ``persons`` model refuses to build until the entry is
re-adjudicated (:func:`unmatched_own_names`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from usa_wa_adapter_legislature.roster_pdf.coverage import ROSTER_SOURCE_SLUG
from usa_wa_pipeline.conformed.crosswalk import merge_map, resolve_merged

#: The adjudication classes, in declared order.
OWN_NAME_CLASSES = ("A", "B", "C")

#: The legislature's own page for her, titled with the form decided 2026-10-13.
_SMITH_PAGE = "https://web.leg.wa.gov/WomenInTheLegislature/Members/SmithNL.htm"


@dataclass(frozen=True)
class OwnName:
    """One adjudicated married-name form."""

    #: The ``usa_wa_legislature_roster`` key value, ``<fold>:<first year>``.
    roster_key: str
    #: The ``name_full`` this entry adjudicated, exactly as published.
    printed: str
    own: str | None
    name_class: str
    evidence: str

    def __post_init__(self) -> None:
        if self.name_class not in OWN_NAME_CLASSES:
            raise ValueError(f"name_class {self.name_class!r} is not one of {OWN_NAME_CLASSES}")
        if not self.evidence.strip():
            raise ValueError(f"{self.roster_key}: an own name needs its evidence")
        if (self.own is None) != (self.name_class == "B"):
            raise ValueError(f"{self.roster_key}: class B, and only class B, has no own name")


OWN_NAMES: tuple[OwnName, ...] = (
    OwnName(
        "agneskehoe:1939",
        "Agnes (Mrs. Thomas E.) Kehoe",
        "Agnes Kehoe",
        "A",
        "her given name precedes the marital parenthetical",
    ),
    OwnName(
        "bellereeves:1923",
        "Belle (Mrs. Frank) Reeves",
        "Belle Reeves",
        "A",
        "her given name precedes the marital parenthetical",
    ),
    OwnName(
        "francesswayze:1953",
        "Frances (Mrs. Thomas A.) Swayze",
        "Frances Swayze",
        "A",
        "her given name precedes the marital parenthetical",
    ),
    OwnName(
        "margarethurley:1953",
        "Margaret (Mrs. Joseph E.) Hurley",
        "Margaret Hurley",
        "A",
        "her given name precedes the marital parenthetical; 1973-79 rows print Margaret Hurley",
    ),
    OwnName(
        "irwinlecocq:1953",
        "Mrs. Irwin LeCocq (Mary)",
        "Mary LeCocq",
        "A",
        "her given name follows her husband's in parentheses",
    ),
    OwnName(
        "juriebsmith:1943",
        "Mrs. Jurie B.(Nettie Luella) Smith",
        "Nettie Luella Smith",
        "A",
        "owner 2026-10-13 (#384): the legislature's own page is titled with this form, "
        + _SMITH_PAGE
        + "; her own key since the widow split (#525), not her husband's juriebsmith:1933",
    ),
    OwnName(
        "vincentfjones:1949",
        "Mrs. Vincent (Matilda) F. Jones",
        "Matilda Jones",
        "A",
        "her given name sits inside her husband's name, Vincent F. Jones",
    ),
    OwnName(
        "harryjohnmiller:1923",
        "Mrs. Harry John Miller",
        None,
        "B",
        "both rows (1923, 1925) print only her husband's name",
    ),
    OwnName(
        "johnwepton:1957",
        "Mrs. John W. Epton",
        "Kathryn Epton",
        "A",
        "her 1957 row prints `Mrs. John W. Epton (Kathryn)`; the later rows, which "
        "name_full publishes, drop it (owner 2026-10-20, #501)",
    ),
    OwnName(
        "evaanderson:1949",
        "Mrs. Eva Anderson",
        "Eva Anderson",
        "C",
        "an honorific on her own name",
    ),
    OwnName(
        "nellietucker:1937",
        "Mrs. Nellie Tucker",
        "Nellie Tucker",
        "C",
        "an honorific on her own name",
    ),
    OwnName(
        "marjoriebhappy:1959",
        "Mrs. Marjorie B. Happy",
        "Marjorie B. Happy",
        "C",
        "an honorific on her own name; registered in #514, after #384's count",
    ),
)


def own_name_for(
    keys: list[dict[str, Any]], name_full: str | None, table: tuple[OwnName, ...] = OWN_NAMES
) -> str | None:
    """The adjudicated own name for an entity with these crosswalk ``keys`` and this
    published ``name_full`` — ``None`` unless an entry names one of its roster keys AND
    the form it adjudicated is the one publishing."""
    by_key = {entry.roster_key: entry for entry in table}
    for key in keys:
        if key["key_namespace"] != ROSTER_SOURCE_SLUG:
            continue
        entry = by_key.get(key["key_value"])
        if entry is not None and entry.printed == name_full:
            return entry.own
    return None


def unmatched_own_names(
    rows: list[dict[str, Any]],
    crosswalk: list[dict[str, Any]],
    table: tuple[OwnName, ...] = OWN_NAMES,
) -> list[OwnName]:
    """Entries that resolve to no live person publishing the form they adjudicated:
    the key is unregistered, or a new edition prints another form. A key on a merged
    entity follows the tombstone to its survivor, as ``person_rows`` does (#366)."""
    printed = {row["entity_id"]: row["name_full"] for row in rows}
    merges = merge_map(crosswalk)
    published = {
        key["key_value"]: printed.get(resolve_merged(merges, str(key["entity_id"])))
        for key in crosswalk
        if key["key_namespace"] == ROSTER_SOURCE_SLUG
    }
    return [entry for entry in table if published.get(entry.roster_key) != entry.printed]
