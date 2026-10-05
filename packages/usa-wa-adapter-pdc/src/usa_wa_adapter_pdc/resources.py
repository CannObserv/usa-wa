"""The PDC archive's resource-id prefixes (#412).

Shared by the raw-store harvest and the #302 pipeline's PDC staging model. They moved out
of ``usa_wa_adapter_pdc.adapter`` and ``usa_wa_adapter_pdc.harvest``, the Postgres write
path deleted in #412 PR F, so the pipeline and the raw harvest could name a cohort without
importing either.
"""

from __future__ import annotations

#: ``fetch_one`` resource-id prefix for the seated House winner cohort.
HOUSE_WINNERS_RESOURCE_PREFIX = "house-winners:"

#: ``fetch_one`` resource-id prefix for a seated Senate winner cohort (#75).
SENATE_WINNERS_RESOURCE_PREFIX = "senate-winners:"
