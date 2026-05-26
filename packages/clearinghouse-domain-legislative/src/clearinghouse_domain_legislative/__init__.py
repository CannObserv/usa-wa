"""Legislative-government domain entities (state + federal).

Importing this package registers every legislative-domain table with the
shared :class:`clearinghouse_core.models.Base` metadata as a side-effect, so
``Base.metadata.create_all`` (used in tests) and alembic autogen (P1a)
discover them.
"""

from clearinghouse_domain_legislative import bills, pdc, statutes  # noqa: F401

__all__ = ["bills", "statutes", "pdc"]
