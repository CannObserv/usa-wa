"""The roster PDF's archive resource ids (#412).

Pure strings, shared by the archive adapter, the harvest and the #302 pipeline's
roster staging model. They live apart from
:mod:`usa_wa_adapter_legislature.roster_pdf.adapter`, which is built on the Postgres
adapter base that #412 PR F deletes, so the pipeline can read the roster archive
without importing it.
"""

from __future__ import annotations

#: ``fetch_one`` resource-id prefix for a roster edition.
ROSTER_RESOURCE_PREFIX = "legroster:"


def roster_resource_id(revision: str) -> str:
    """The archive resource id for a revision — ``legroster:<YYYY-MM-DD>``."""
    return f"{ROSTER_RESOURCE_PREFIX}{revision}"


def revision_from_resource_id(resource_id: str) -> str:
    """Recover the revision date from a ``legroster:<YYYY-MM-DD>`` resource id."""
    if not resource_id.startswith(ROSTER_RESOURCE_PREFIX):
        raise ValueError(f"unknown resource_id: {resource_id!r}")
    return resource_id[len(ROSTER_RESOURCE_PREFIX) :]
