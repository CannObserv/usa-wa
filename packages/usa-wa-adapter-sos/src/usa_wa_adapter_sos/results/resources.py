"""The SOS Legislative results archive's resource ids (#412).

Shared by the Postgres adapter, the raw-store harvest and the #302 pipeline's SOS staging
model. They live apart from :mod:`usa_wa_adapter_sos.results.adapter`, which is built on the
Postgres adapter base that #412 PR F deletes, so the pipeline and the raw harvest can key
a cohort without importing it.
"""

from __future__ import annotations

from usa_wa_adapter_sos.results.transport import general_election_date

#: ``fetch_one`` resource-id prefix for a general-election Legislative results cohort.
LEGRESULTS_RESOURCE_PREFIX = "sos-legresults:"


def legresults_resource_id(election_year: int) -> str:
    """The archive resource id for a general election's results — ``sos-legresults:<YYYYMMDD>``."""
    return f"{LEGRESULTS_RESOURCE_PREFIX}{general_election_date(election_year)}"


def election_year_from_resource_id(resource_id: str) -> int:
    """Recover the election year from a ``sos-legresults:<YYYYMMDD>`` resource id."""
    if not resource_id.startswith(LEGRESULTS_RESOURCE_PREFIX):
        raise ValueError(f"unknown resource_id: {resource_id!r}")
    return int(resource_id[len(LEGRESULTS_RESOURCE_PREFIX) : len(LEGRESULTS_RESOURCE_PREFIX) + 4])
