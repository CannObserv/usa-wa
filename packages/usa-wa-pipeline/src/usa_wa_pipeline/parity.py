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
    """One explained divergence: the key, which side has it, and why that is fine."""

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

    An allowlist entry whose key is not actually diverging is itself an error
    (a stale acceptance is a blindfold — the #300 exemption rule), surfaced by
    keeping it in the report's ``accepted`` only when it matched.
    """
    staging = set(staging_keys)
    canonical = set(canonical_keys)
    only_staging = staging - canonical
    only_canonical = canonical - staging
    matched: list[AcceptedDiff] = []
    stale: list[AcceptedDiff] = []
    for diff in accepted:
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
