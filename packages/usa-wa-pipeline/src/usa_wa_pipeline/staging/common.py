"""Shared staging plumbing (#306/#307): raw-store iteration + field coercion."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from clearinghouse_core.rawstore import RawStore


def latest_wires(store: RawStore, prefix: str) -> Iterator[tuple[str, bytes]]:
    """The newest ok wire per resource id under ``prefix``, id-sorted for determinism."""
    for resource_id, entry in sorted(store.latest().items()):
        if resource_id.startswith(prefix):
            yield resource_id, store.object_path(entry["sha256"]).read_bytes()


def text(value: Any) -> str | None:
    """Source ids as text, uniformly — sources render ints, resource ids carry strings."""
    return None if value is None else str(value)
