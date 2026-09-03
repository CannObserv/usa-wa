"""Staging-tier row builders (#306): raw-store wires → natural-key rows.

One module per source family. These carry the staging layer's logic so pytest
owns its red/green; the dbt Python models under ``dbt/models/staging/`` are
thin adapters over them (docs/PIPELINE.md § TDD for dbt models). Staging rules
(replatform spec): one cleaning regime per source, natural keys only — no
ULIDs, no cross-source joins.
"""
