"""The transition parity harness (#306): staging outputs vs. canonical rows.

The running system is the oracle (replatform spec § Transition plan): while the
old pipeline still writes Postgres, every new-pipeline tier is diffed against
the canonical rows it must reproduce before anything downstream trusts it.
Report-only by design — a report's ``clean`` is asserted by the integration
tests, with every accepted diff carried in an explicit allowlist that names its
reason, so "explained and accepted" is code, not a comment.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class AcceptedDiff:
    """One explained divergence: the dataset it belongs to, the key, which side
    has it, and why that is fine. ``dataset`` scopes the acceptance — the same
    key can exist in another dataset's keyspace (WSL committee and member ids
    share a numeric range) and must not be swallowed there (#302 CR)."""

    dataset: str
    key: str
    side: str  # "staging" | "canonical"
    reason: str


@dataclass(frozen=True)
class ParityReport:
    """A key-set comparison after allowlisting."""

    dataset: str
    only_staging: frozenset[str]
    only_canonical: frozenset[str]
    staging_total: int
    canonical_total: int
    accepted: tuple[AcceptedDiff, ...] = field(default=())

    @property
    def clean(self) -> bool:
        return not self.only_staging and not self.only_canonical

    def render(self) -> str:
        """Human-readable diff summary for the test failure message / triage log."""
        lines = [
            f"[{self.dataset}] staging={self.staging_total} canonical={self.canonical_total} "
            f"accepted={len(self.accepted)}"
        ]
        for label, keys in (
            ("only in staging", self.only_staging),
            ("only in canonical", self.only_canonical),
        ):
            if keys:
                shown = sorted(keys)[:20]
                suffix = "" if len(keys) <= 20 else f" … +{len(keys) - 20} more"
                lines.append(f"  {label} ({len(keys)}): {', '.join(shown)}{suffix}")
        return "\n".join(lines)


def key_set_parity(
    dataset: str,
    staging_keys: Iterable[str],
    canonical_keys: Iterable[str],
    *,
    accepted: Iterable[AcceptedDiff] = (),
) -> ParityReport:
    """Diff two key sets, removing allowlisted keys from their named side.

    Acceptances are scoped by ``dataset``: entries for other datasets pass
    through untouched, while every entry for THIS dataset must match a real
    divergence — a stale acceptance is a blindfold (the #300 exemption rule)
    and fails the run unconditionally, including when the key has vanished
    from both sides (#302 CR: presence pre-filtering defeated the guarantee).
    """
    staging = set(staging_keys)
    canonical = set(canonical_keys)
    only_staging = staging - canonical
    only_canonical = canonical - staging
    matched: list[AcceptedDiff] = []
    stale: list[AcceptedDiff] = []
    for diff in accepted:
        if diff.dataset != dataset:
            continue
        target = only_staging if diff.side == "staging" else only_canonical
        if diff.key in target:
            target.discard(diff.key)
            matched.append(diff)
        else:
            stale.append(diff)
    if stale:
        raise ValueError(
            f"[{dataset}] stale parity acceptances (no longer diverging): "
            + ", ".join(f"{d.side}:{d.key}" for d in stale)
        )
    return ParityReport(
        dataset=dataset,
        only_staging=frozenset(only_staging),
        only_canonical=frozenset(only_canonical),
        staging_total=len(staging),
        canonical_total=len(canonical),
        accepted=tuple(matched),
    )


def subset_parity(
    dataset: str,
    staging_keys: Iterable[str],
    canonical_keys: Iterable[str],
    *,
    accepted: Iterable[AcceptedDiff] = (),
) -> ParityReport:
    """One-directional parity: every canonical key must exist in staging.

    For datasets where staging legitimately holds MORE than canonical ever
    materialized (PDC winners: canonical links identifiers only for matched
    members). ``only_staging`` is reported as empty by construction — the
    surplus is the dataset's nature, not a divergence; ``clean`` means
    canonical ⊆ staging. Only canonical-side acceptances are meaningful here:
    a staging-side one would "match" against a surplus the report discards.
    """
    for diff in accepted:
        if diff.dataset == dataset and diff.side == "staging":
            raise ValueError(
                f"[{dataset}] staging-side acceptance {diff.key!r} is meaningless "
                "in subset mode (the staging surplus is not a divergence)"
            )
    staging = set(staging_keys)
    report = key_set_parity(dataset, staging, canonical_keys, accepted=accepted)
    return ParityReport(
        dataset=dataset,
        only_staging=frozenset(),
        only_canonical=report.only_canonical,
        staging_total=report.staging_total,
        canonical_total=report.canonical_total,
        accepted=report.accepted,
    )
