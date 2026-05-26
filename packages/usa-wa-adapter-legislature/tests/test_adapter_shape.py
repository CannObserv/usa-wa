"""Verify the WA Legislature adapter package shape.

These tests don't exercise SOAP or normalization (P1a). They just confirm
that the Layer 3 package layout works end-to-end:

- Importing the package yields a class
- The class subclasses :class:`clearinghouse_core.adapter.BaseAdapter`
- The required class-vars are populated with the expected slugs
"""

import pytest

from clearinghouse_core.adapter import BaseAdapter
from usa_wa_adapter_legislature import WALegislatureAdapter


def test_adapter_is_base_adapter_subclass():
    assert issubclass(WALegislatureAdapter, BaseAdapter)


def test_adapter_classvars_match_naming_convention():
    """Slugs match the project's naming convention (3-letter country + state)."""
    assert WALegislatureAdapter.source_slug == "usa_wa_legislature"
    assert WALegislatureAdapter.schema_name == "usa_wa_legislature"
    assert WALegislatureAdapter.jurisdiction_slug == "usa-wa"


async def test_adapter_methods_are_stubs_until_p1a():
    """The three abstract methods raise NotImplementedError in this skeleton."""
    adapter = WALegislatureAdapter()

    with pytest.raises(NotImplementedError, match="P1a"):
        await adapter.fetch_one("HB-1234-2025-26")

    with pytest.raises(NotImplementedError, match="P1a"):
        await adapter.normalize(payload=None)  # type: ignore[arg-type]
