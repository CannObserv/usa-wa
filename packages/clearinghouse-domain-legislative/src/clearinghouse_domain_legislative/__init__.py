"""Legislative-government domain: the term calendar, the span engine and the operator
attestation models.

Importing this package registers the domain's two tables — ``registry.operator_events``
and ``registry.committee_succession_events`` — with the shared
:class:`clearinghouse_core.models.Base` metadata as a side-effect, so
``Base.metadata.create_all`` (tests) and alembic autogen discover them. The canonical
entity models (persons, organizations, roles, assignments, and the declared bill, vote,
statute, session and lobbying clusters) left with the Postgres canonical tier in #412 PR F.
"""

from clearinghouse_domain_legislative import (  # noqa: F401
    committee_succession,
    operator_events,
)

__all__ = [
    "operator_events",
    "committee_succession",
]
