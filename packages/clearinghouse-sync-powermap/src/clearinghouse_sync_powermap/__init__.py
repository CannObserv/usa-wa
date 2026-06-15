"""Portable Power Map sync engine.

Importing this package registers the ``sync``-schema tables with the shared
:class:`clearinghouse_core.models.Base` metadata as a side-effect, so
``Base.metadata.create_all`` (tests) and alembic autogen discover them.
"""

from clearinghouse_sync_powermap import models as _models  # noqa: F401
from clearinghouse_sync_powermap.client import (
    ChangeItem,
    ChangePage,
    DiscoveredEntity,
    EntityPage,
    ObservationResult,
    PowerMapClient,
    SubscriptionResult,
)
from clearinghouse_sync_powermap.descriptors import (
    Authority,
    EntityDescriptor,
    ReadSource,
    as_ulid,
)
from clearinghouse_sync_powermap.models import OutboxEntry, SyncState

__all__ = [
    "Authority",
    "ChangeItem",
    "ChangePage",
    "DiscoveredEntity",
    "EntityDescriptor",
    "EntityPage",
    "ObservationResult",
    "OutboxEntry",
    "PowerMapClient",
    "ReadSource",
    "SubscriptionResult",
    "SyncState",
    "as_ulid",
]
