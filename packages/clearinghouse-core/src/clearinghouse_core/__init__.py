"""Clearinghouse framework primitives.

Importing this package registers the provenance models with ``Base.metadata``
as a side-effect (via ``models.py``), so ``Base.metadata.create_all`` and
alembic autogen see them.
"""

from clearinghouse_core import models  # noqa: F401  (side-effect: model registration)
