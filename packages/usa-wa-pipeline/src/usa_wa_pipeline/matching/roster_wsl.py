"""Roster ↔ WSL sponsor exact rule (#308), as a pure frame function.

Python, not SQL, because the roster key's name half MUST be the adapter's
`identity_fold` — the same fold the seeded registry keys carry
(`usa_wa_legislature_roster:<fold>:<first-session-year>`); a SQL approximation
would mint near-miss duplicates. Join evidence per the resolve path: same
biennium, chamber, district, and fold-equal names.

Two guards (#302 CR):

- a sponsor row with no usable name is dropped, never crashed on;
- a fold whose roster listing-years gap wider than the adapter's
  ``WIDE_GAP_YEARS`` is withheld entirely — the Jr/Sr signature. ``min(year)``
  would hand the younger member the ELDER's seeded roster key and propose a
  silent cross-person merge; the resolve path refuses exactly these folds
  (``RefusedIdentity``), so they are adjudication material here too.
"""

from __future__ import annotations

import pandas as pd

from usa_wa_adapter_legislature.roster_pdf.identity import WIDE_GAP_YEARS, identity_fold

LINK_COLUMNS = ["kind", "left_key", "right_key", "rule", "score"]

RULE = "roster_wsl_seat_fold"


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=LINK_COLUMNS)


def ambiguous_folds(roster: pd.DataFrame) -> set[str]:
    """Folds whose roster years gap wider than ``WIDE_GAP_YEARS`` — computed on
    the FULL roster (pre-1991 included): the elder half of a Jr/Sr pair is
    usually exactly the part the 1991 filter would hide."""
    out: set[str] = set()
    for fold, years in roster.groupby("fold")["year"]:
        ordered = sorted({int(y) for y in years})
        if any(b - a > WIDE_GAP_YEARS for a, b in zip(ordered, ordered[1:], strict=False)):
            out.add(fold)
    return out


def roster_wsl_links(roster: pd.DataFrame, sponsors: pd.DataFrame) -> pd.DataFrame:
    """(roster staging rows, sponsor staging rows) → link proposals."""
    if roster.empty or sponsors.empty:
        return _empty()

    roster = roster[roster["name"].notna()].copy()
    roster["fold"] = roster["name"].map(identity_fold)
    roster = roster[~roster["fold"].isin(ambiguous_folds(roster))]
    if roster.empty:
        return _empty()
    first_year = roster.groupby("fold")["year"].min().rename("first_year")
    roster = roster.join(first_year, on="fold")
    roster = roster[roster["year"] >= 1991]
    # session year → containing biennium label
    start = roster["year"].where(roster["year"] % 2 == 1, roster["year"] - 1)
    roster["biennium"] = start.astype(str) + "-" + ((start + 1) % 100).astype(str).str.zfill(2)
    roster["chamber_norm"] = roster["chamber"].str.lower()
    roster["district_num"] = pd.to_numeric(roster["district"], errors="coerce")

    sponsors = sponsors.copy()
    names = sponsors["name"].fillna(sponsors["long_name"])
    sponsors = sponsors[names.notna()].copy()
    if sponsors.empty:
        return _empty()
    sponsors["fold"] = names[names.notna()].map(identity_fold)
    sponsors["chamber_norm"] = (
        sponsors["agency"].str.lower().map({"house": "house", "senate": "senate"})
    )
    sponsors["district_num"] = pd.to_numeric(sponsors["district"], errors="coerce")

    joined = roster.merge(
        sponsors,
        on=["biennium", "chamber_norm", "district_num", "fold"],
        suffixes=("_roster", "_wsl"),
    )
    if joined.empty:
        return _empty()
    return pd.DataFrame(
        {
            "kind": "person",
            "left_key": "usa_wa_legislature_roster:"
            + joined["fold"]
            + ":"
            + joined["first_year"].astype(int).astype(str),
            "right_key": "usa_wa_legislature:" + joined["member_id"],
            "rule": RULE,
            "score": 1.0,
        }
    ).drop_duplicates()
