"""The nightly backup to GCS and the restore that reads it back (#434).

Two things on this host cannot be rebuilt from anything else: the registry in
Postgres (the ULIDs, the adjudicated merges, the operator attestations) and the
raw store under ``USA_WA_RAW_ROOT`` (the inputs every dataset is rebuilt from).
``run`` ships both, nightly and create-only; ``restore`` brings either back.
docs/RECOVERY.md is the runbook around both.
"""
