"""The raw attestation dimension (#313): ``latest.json`` → one row per resource.

The file-tier analog of the Postgres ``FetchEvent`` that ``Citation`` used to
join through. Every staging row carries ``(source, resource_id)``; this table
carries what is *known about* that resource — the digest of the bytes, when
they were pulled, the run that pulled them, and the URL they came from — so
``entity → staging row → resource → sha256`` closes without duplicating a
64-char digest onto ~10^4 staging rows.

**Discovered, not configured.** The sources are whatever directories the raw
root holds, because a source added to the harvest chain and forgotten here
would quietly publish uncitable entities — and a list is exactly the kind of
thing that gets forgotten. ``latest.json`` is the index of newest *ok* fetches,
so an erroring source contributes nothing rather than a row asserting bytes
that were never stored.

The digest and timestamp come from ``latest.json``; the URL and byte count come
from the run manifest it names. A manifest that has been pruned costs the row
its colour, never the row itself.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FETCH_COLUMNS = [
    "source",
    "resource_id",
    "sha256",
    "fetched_at",
    "run_id",
    "url",
    "bytes",
    "content_type",
]


def _manifest_entries(runs_dir: Path, run_id: str) -> dict[str, dict[str, Any]]:
    """One run manifest indexed by resource id. Missing/unreadable → ``{}``."""
    path = runs_dir / f"{run_id}.json"
    if not path.is_file():
        return {}
    try:
        manifest = json.loads(path.read_text())
    except json.JSONDecodeError:
        # A half-written manifest is a colour loss, not an integrity one: the
        # digest this row asserts came from `latest.json`, not from here.
        return {}
    return {entry["resource_id"]: entry for entry in manifest.get("entries", [])}


def fetch_rows(root: Path | str) -> list[dict[str, Any]]:
    """Every source's newest ok fetch per resource, source- then resource-sorted."""
    root = Path(root)
    if not root.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for source_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        index_path = source_dir / "latest.json"
        if not index_path.is_file():
            continue
        index = json.loads(index_path.read_text())
        # Manifests are read once per run, not once per resource: a harvest
        # records hundreds of resources under one run id.
        manifests: dict[str, dict[str, dict[str, Any]]] = {}
        for resource_id, entry in sorted(index.items()):
            run_id = entry.get("run_id")
            manifest = manifests.setdefault(
                run_id, _manifest_entries(source_dir / "runs", run_id) if run_id else {}
            )
            recorded = manifest.get(resource_id, {})
            rows.append(
                {
                    "source": source_dir.name,
                    "resource_id": resource_id,
                    "sha256": entry.get("sha256"),
                    "fetched_at": entry.get("fetched_at"),
                    "run_id": run_id,
                    "url": recorded.get("url"),
                    "bytes": recorded.get("bytes"),
                    "content_type": recorded.get("content_type"),
                }
            )
    return rows
