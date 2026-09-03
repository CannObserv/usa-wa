"""Roster ↔ WSL sponsor exact rule (#308), as a dbt Python model.

Python, not SQL, because the roster key's name half MUST be the adapter's
`identity_fold` — the same fold the seeded registry keys carry
(`usa_wa_legislature_roster:<fold>:<first-session-year>`); a SQL approximation
would mint near-miss duplicates. Join evidence per the resolve path: same
biennium, chamber, district, and fold-equal names. A person whose roster
service began pre-1991 resolves to their seeded roster key (bridging the
minted identity onto the WSL member — noop when the seed already joined them,
CONFLICT when it disagrees, which is exactly the triage signal); a 1991+
person gets their roster attestation key appended to the WSL identity, which
is the crosswalk the spec publishes.
"""

import pandas as pd

from usa_wa_adapter_legislature.roster_pdf.identity import identity_fold


def model(dbt, session):
    dbt.config(materialized="table")
    roster = dbt.ref("stg_roster_members").df()
    sponsors = dbt.ref("stg_wsl_sponsors").df()
    if roster.empty or sponsors.empty:
        return pd.DataFrame(columns=["left_key", "right_key", "rule", "score"])

    roster = roster.copy()
    roster["fold"] = roster["name"].map(identity_fold)
    first_year = roster.groupby("fold")["year"].min().rename("first_year")
    roster = roster.join(first_year, on="fold")
    roster = roster[roster["year"] >= 1991]
    # session year → containing biennium label
    start = roster["year"].where(roster["year"] % 2 == 1, roster["year"] - 1)
    roster["biennium"] = start.astype(str) + "-" + ((start + 1) % 100).astype(str).str.zfill(2)
    roster["chamber_norm"] = roster["chamber"].str.lower()
    roster["district_num"] = pd.to_numeric(roster["district"], errors="coerce")

    sponsors = sponsors.copy()
    sponsors["fold"] = sponsors["name"].fillna(sponsors["long_name"]).map(identity_fold)
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
        return pd.DataFrame(columns=["left_key", "right_key", "rule", "score"])
    out = pd.DataFrame(
        {
            "left_key": "usa_wa_legislature_roster:"
            + joined["fold"]
            + ":"
            + joined["first_year"].astype(int).astype(str),
            "right_key": "usa_wa_legislature:" + joined["member_id"],
            "rule": "roster_wsl_seat_fold",
            "score": 1.0,
        }
    ).drop_duplicates()
    return out
