"""The crosswalk's merge rule, in one place (#366 CR 12).

A merge re-points, it does not delete: the loser's keys still name the same
person or body, now under the survivor's ULID, and the tombstone is the
published crosswalk's only re-point signal. Every conformed model that reads
the crosswalk therefore has to follow it.

Each of them used to say so separately — `spans.entity_index`,
`citations._resolve_tombstones` and `entities._live_entities` carried three
walks between them, and `clearinghouse_core.registry.resolve_merged` was a
fourth, in the framework, already cycle-guarded. **Divergence between those
copies is what #366 was**: two followed the tombstone and the third dropped the
loser's rows, so the survivor was published stripped of the identity the merge
had just given it. A fifth restatement would have fixed the instance; one
implementation removes the failure mode.

So the walk stays exactly where it was — :func:`resolve_merged`, re-exported
here — and what lives here is the one thing the framework should not know: the
shape of a crosswalk row.
"""

from __future__ import annotations

from typing import Any

from clearinghouse_core.registry import resolve_merged

__all__ = ["merge_map", "resolve_merged"]


def merge_map(crosswalk: list[dict[str, Any]]) -> dict[str, str]:
    """``entity_id → the entity it was merged into``, tombstoned rows only.

    A tombstone is a non-blank STRING. Screening on the type rather than on
    ``is not None`` is what makes this total across the frames these rows
    arrive in: the crosswalk models pin `merged_into` to pandas' ``string``
    dtype, whose null is ``pd.NA``, and the three walks this replaces each
    handled a different subset — `is not None` reads every live row as a
    tombstone pointing at nothing, and `str()` reads one as a tombstone
    pointing at the entity ``'<NA>'``. Neither fails loudly.
    """
    out: dict[str, str] = {}
    for row in crosswalk:
        target = row.get("merged_into")
        if isinstance(target, str) and target.strip():
            out[str(row["entity_id"])] = target
    return out
