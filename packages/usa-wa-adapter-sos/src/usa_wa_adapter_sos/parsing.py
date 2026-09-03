"""Offline archived-wire parsers (#307): the #302 pipeline's SOS parse seam.

Re-exports both sources' pure offline decoders (filings CSV, results CSV) so
`usa_wa_pipeline` staging depends on *parsing* without importing either
`transport` module (the layer contract).
"""

from usa_wa_adapter_sos.filings.transport import parse_whofiled
from usa_wa_adapter_sos.results.transport import parse_legislative_results

__all__ = ["parse_whofiled", "parse_legislative_results"]
