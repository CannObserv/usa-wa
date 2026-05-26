"""WA State Legislature SOAP adapter package.

Public surface: :class:`WALegislatureAdapter` — a ``clearinghouse_core.BaseAdapter``
subclass that maps WSL web services to the canonical legislative-domain entities.

SOAP client implementation lands in P1a. The shell here exists to validate the
Layer 3 package shape end-to-end.
"""

from usa_wa_adapter_legislature.adapter import WALegislatureAdapter

__all__ = ["WALegislatureAdapter"]
