"""Verify the WA Legislature adapter package shape.

Confirms the Layer 3 package layout works end-to-end:

- Importing the package yields a class
- The class subclasses :class:`clearinghouse_core.adapter.BaseAdapter`
- The required class-vars are populated with the expected slugs
"""

from clearinghouse_core.adapter import BaseAdapter
from usa_wa_adapter_legislature import WALegislatureAdapter


def test_adapter_is_base_adapter_subclass():
    assert issubclass(WALegislatureAdapter, BaseAdapter)


def test_adapter_classvars_match_naming_convention():
    """Slugs match the project's naming convention (3-letter country + state)."""
    assert WALegislatureAdapter.source_slug == "usa_wa_legislature"
    assert WALegislatureAdapter.schema_name == "usa_wa_legislature"
    assert WALegislatureAdapter.jurisdiction_slug == "usa-wa"
