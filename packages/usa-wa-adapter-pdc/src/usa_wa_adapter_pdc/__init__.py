"""WA Public Disclosure Commission (PDC) adapter package.

Sources a WA House member's ballot **Position** (1 / 2) from the PDC
``Campaign Finance Summary`` SODA dataset and emits the House ``state_representative``
seat Assignment P1b deferred (issue #69). See the package README + plan doc.
"""

from usa_wa_adapter_pdc.adapter import PDCAdapter

__all__ = ["PDCAdapter"]
