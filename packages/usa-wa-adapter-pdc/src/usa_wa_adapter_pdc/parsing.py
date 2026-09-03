"""Offline archived-wire parsers (#307): the #302 pipeline's PDC parse seam.

Re-exports the transport's pure offline decoders so `usa_wa_pipeline` staging
depends on *parsing* without importing `transport` (the layer contract). No
client, no wire — SODA bodies are plain JSON arrays.
"""

from usa_wa_adapter_pdc.transport import parse_house_winners, parse_senate_winners

__all__ = ["parse_house_winners", "parse_senate_winners"]
