"""Legislative-government domain entities (state + federal).

Importing this package registers every legislative-domain table with the
shared :class:`clearinghouse_core.models.Base` metadata as a side-effect,
so ``Base.metadata.create_all`` (tests) and alembic autogen (P0.5 step 5)
discover them.
"""

from clearinghouse_domain_legislative import (  # noqa: F401
    bills,
    identity,
    pdc,
    role_types,
    sessions,
    statutes,
    votes,
)

__all__ = ["identity", "sessions", "bills", "votes", "statutes", "pdc", "role_types"]
