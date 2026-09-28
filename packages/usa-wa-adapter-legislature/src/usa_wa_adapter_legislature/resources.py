"""The WSL archive's resource ids: prefixes, builders and parsers (#412).

Pure strings, shared by the Postgres adapter, the raw-store harvest and the #302
pipeline's staging models. They live apart from :mod:`usa_wa_adapter_legislature.adapter`
because that module is the Postgres write path, which #412 PR F deletes: the pipeline and
the raw harvest must not import it (the import-linter contract "The pipeline, the API and
the raw harvests never import the retiring Postgres tier"). The committee-meetings key
lives beside its window arithmetic in :mod:`usa_wa_adapter_legislature.meetings.windows`.
"""

from __future__ import annotations

COMMITTEES_RESOURCE_PREFIX = "committees:"
#: Historical full-roster archive (sub-project 3), distinct from the daily
#: ``committees:<biennium>`` GetActiveCommittees archive — a *different* SOAP
#: operation (``GetCommittees(biennium)`` full roster vs GetActiveCommittees'
#: implicit-current active set), so the wire genuinely differs and the two keys
#: never collide. Phase B's rename-chain reads only this key.
COMMITTEES_ROSTER_RESOURCE_PREFIX = "committees-roster:"
#: The member roster (P1b): ``sponsors:<biennium>`` drives ``SponsorService.GetSponsors``.
SPONSORS_RESOURCE_PREFIX = "sponsors:"
#: The committee roster key (#82): ``committee-members-hist:<biennium>:<id>:<agency>:<name>``
#: drives ``GetCommitteeMembers(biennium, agency, name)``. The biennium leads so a committee's
#: rosters sort by era; the id (which the members payload doesn't carry) lets the span builder
#: resolve the committee Org and key citations per (biennium, committee). The daily fan-out
#: keys this same op by the *current* biennium — it returns exactly the set the retired
#: ``GetActiveCommitteeMembers`` pull did — so one uniform archive covers current + history.
COMMITTEE_MEMBERS_HIST_RESOURCE_PREFIX = "committee-members-hist:"


def committee_members_hist_resource_id(
    biennium: str, committee_source_id: str, agency: str, committee_name: str
) -> str:
    """Build the ``committee-members-hist:<biennium>:<id>:<agency>:<name>`` resource id (#82)."""
    return (
        f"{COMMITTEE_MEMBERS_HIST_RESOURCE_PREFIX}{biennium}:{committee_source_id}:"
        f"{agency}:{committee_name}"
    )


def parse_committee_members_hist_resource_id(resource_id: str) -> tuple[str, str, str, str]:
    """Parse ``committee-members-hist:<biennium>:<id>:<agency>:<name>`` →
    (biennium, committee_id, agency, name).

    Splits on the first three colons only, so a committee ``Name`` containing a colon
    (none do today) still round-trips in the trailing segment."""
    rest = resource_id[len(COMMITTEE_MEMBERS_HIST_RESOURCE_PREFIX) :]
    biennium, committee_source_id, agency, committee_name = rest.split(":", 3)
    return biennium, committee_source_id, agency, committee_name
