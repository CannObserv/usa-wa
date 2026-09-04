"""Shared staging plumbing (#306/#307): raw-store iteration + field coercion."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from clearinghouse_core.rawstore import RawStore

#: The raw-store coordinates every staging row carries (#313). Appended last by
#: each builder's column list, so the published staging schemas stay additive.
PROVENANCE_COLUMNS = ["source", "resource_id"]


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
