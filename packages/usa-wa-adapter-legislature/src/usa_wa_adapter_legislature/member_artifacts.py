"""Curated member-artifact exclusion denylist (#144) — pure.

A small, **manually-curated, evidence-backed** set of ``(biennium, member_id)`` pairs the WSL
``GetSponsors`` archive carries spuriously — a chamber-conflation or clerical artifact confirmed
against the official WA Legislature members roster (1889-2025). Each entry is a member the wire
names in a biennium they did **not** serve, producing a phantom span that duplicates the real
incumbent's seat.

These are distinct from the :mod:`roster_hygiene` exclusion, which is *data-driven* — it flags a
departed-but-still-named member by their **absence** from that biennium's committee-roster archive.
An artifact here is a *fully-formed* WSL row (named, committee-present in the wire), so no
automatic signal catches it; it needs a curated correction. The denylist is unioned into the
``exclude_ids_by_biennium`` set :func:`sponsor_observations.build_sponsor_observations` already
honours, so no unrestricted rebuild ever re-derives the phantom span (#54-safe — the archive is
never rewritten; the correction lives in the canonical-derivation layer).

This is **Phase 1** of the #144 fix: it prevents *re-derivation*. Retracting the already-produced
PM-anchored rows is **Phase 2**, blocked on the producer retraction verb (power-map#391); Phase 1
is the prerequisite that makes any eventual retraction stick — without it the next backfill
re-produces the retracted assignment.

Curated entries:

- ``("2001-02", "481")`` — **John Wynne**. The official roster lists ``Wynne, John — H-39``
  (House only, one term 1991); Val Stevens held the LD39 **Senate** seat continuously 1997-2012.
  Wynne was not in the legislature in 2001-02 at all, so his ``sponsors:2001-02`` row (an LD39
  Senator) is a chamber-conflation artifact. His legitimate 1991 House party tenure
  (``sponsors:1991-92``) is untouched — only the 2001-02 biennium is excluded.
"""

from __future__ import annotations

#: ``{biennium: {member_id}}`` — spurious WSL ``GetSponsors`` appearances to exclude from span
#: derivation. Member ids are strings (the wire carries ints; the projection stringifies).
ARTIFACT_EXCLUSIONS_BY_BIENNIUM: dict[str, frozenset[str]] = {
    "2001-02": frozenset({"481"}),  # John Wynne — LD39 Senate chamber-conflation artifact (#144)
}


def with_artifact_exclusions(
    exclude_ids_by_biennium: dict[str, set[str]],
) -> dict[str, set[str]]:
    """Return a new exclusions map that unions the curated artifact denylist into ``exclude_ids_by
    _biennium`` (the caller's #105 stale-exclusion set). Pure — the caller's dict and its member
    sets are left unmutated; each biennium's set is copied before the union so the curated
    frozenset can't leak into a caller-owned set."""
    merged: dict[str, set[str]] = {b: set(ids) for b, ids in exclude_ids_by_biennium.items()}
    for biennium, ids in ARTIFACT_EXCLUSIONS_BY_BIENNIUM.items():
        merged.setdefault(biennium, set()).update(ids)
    return merged
