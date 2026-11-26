"""Shared staging plumbing (#306/#307): raw-store iteration + field coercion."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from clearinghouse_core.rawstore import RawStore

#: The raw-store coordinates every staging row carries (#313). Appended last by
#: each builder's column list, so the published staging schemas stay additive.
PROVENANCE_SCHEMA = {"source": "VARCHAR", "resource_id": "VARCHAR"}
PROVENANCE_COLUMNS = list(PROVENANCE_SCHEMA)


def provenance(store: RawStore, resource_id: str) -> dict[str, str]:
    """The raw coordinates of the wire a row was read from (#313).

    Two columns, not five: the attestation itself (``sha256``, ``fetched_at``,
    the URL) lives once per resource in ``stg_raw_fetches``, and duplicating a
    64-char digest onto every one of ~10^4 staging rows would buy nothing a
    join does not. This pair is the join key.
    """
    return {"source": store.source, "resource_id": resource_id}


def latest_wires(store: RawStore, prefix: str) -> Iterator[tuple[str, bytes]]:
    """The newest ok wire per resource id under ``prefix``, id-sorted for determinism."""
    for resource_id, entry in sorted(store.latest().items()):
        if resource_id.startswith(prefix):
            yield resource_id, store.object_path(entry["sha256"]).read_bytes()


def text(value: Any) -> str | None:
    """Source ids as text, uniformly — sources render ints, resource ids carry strings."""
    return None if value is None else str(value)


def first_fetches(store: RawStore, prefix: str) -> dict[tuple[str, str], str]:
    """Every distinct wire ever archived under ``prefix`` → its EARLIEST ok fetch (#600).

    ``(resource_id, sha256) → fetched_at``, walking every run manifest: staging otherwise
    reads only the newest wire, and the nightly records an unchanged refetch again, so this
    is how a builder learns when a wire's content first appeared. Manifest timestamps share
    one format, so they order as text. A half-written manifest contributes nothing."""
    earliest: dict[tuple[str, str], str] = {}
    for path in store.manifest_paths():
        try:
            manifest = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        for entry in manifest.get("entries", []):
            resource_id = entry.get("resource_id") or ""
            sha, fetched_at = entry.get("sha256"), entry.get("fetched_at")
            if (
                resource_id.startswith(prefix)
                and entry.get("status") == "ok"
                and sha
                and fetched_at
            ):
                key = (resource_id, sha)
                earliest[key] = min(fetched_at, earliest.get(key, fetched_at))
    return earliest
