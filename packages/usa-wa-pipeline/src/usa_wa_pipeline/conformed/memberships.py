"""Substitute tenures: who served a seat while its principal was on leave (#501 D5).

``membership_type`` says how a tenure holds its role, and only a ``principal``
counts toward a seat's capacity. Three tenures are something else: military
substitutes, appointed to serve while the seat's principal was on leave. The
roster states both halves of each — the substitute's appointment, and the
principal's leave — so each pair is a true overlap of two people, not a
succession for the clip to resolve and not a conflict for the gate to report.

**A curated table**, the ``DISTINCT_NAMESAKES`` / ``JOIN_ADJUDICATIONS`` idiom,
not a parser and not an event:

- a parser would key on the leave clause's wording, which appears in two
  different columns across three cases (the annotation, and a parenthetical
  inside the principal's NAME) — ARCHITECTURE.md's second rule;
- a ``seated`` reason would be "evidence classification", and this changes
  behaviour (the clip and the gate), which ONTOLOGY.md § 3 says a reason must not;
- a new event kind is reserved for a new effect on spans (§ 5), and a
  substitution moves no boundary.

Each entry is keyed by the span identity ``(source, member_id, span_kind,
span_discriminator, span_start_biennium)``, stable across rebuilds, and carries
both roster clauses verbatim. It is applied in
:func:`~usa_wa_pipeline.conformed.spans.build_families`, after the overlay and
before the clip, so every caller sees the same spans.

**Revisit when** a fourth case appears, or when a source publishes
substitutions: then this becomes an operator attestation with a CLI and
raw-store provenance, not a longer list.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from clearinghouse_domain_legislative.membership_types import MEMBERSHIP_SUBSTITUTE
from clearinghouse_domain_legislative.span_kinds import KIND_SENATE
from clearinghouse_domain_legislative.tenure_spans import TenureSpan
from usa_wa_adapter_legislature.coverage import WSL_SOURCE_SLUG

#: The roster PDF resource every entry was read from (the edition the raw store
#: holds; ``stg_roster_members.resource_id``).
_EDITION = "legroster:2025-06-05"


@dataclass(frozen=True)
class SubstituteTenure:
    """One substitute's span, and the roster evidence for it."""

    source: str
    member_id: str
    span_kind: str
    span_discriminator: str
    span_start_biennium: str
    substitute: str
    principal: str
    #: The substitute's own annotation, verbatim.
    substitute_clause: str
    #: The principal's leave clause, verbatim — wherever the roster printed it.
    principal_clause: str
    edition: str

    def __post_init__(self) -> None:
        for name in ("substitute_clause", "principal_clause", "edition"):
            if not getattr(self, name):
                raise ValueError(f"a substitute tenure needs its evidence: {name} is empty")

    @property
    def key(self) -> tuple[str, str, str, str, str]:
        """The span identity this entry names."""
        return (
            self.source,
            self.member_id,
            self.span_kind,
            self.span_discriminator,
            self.span_start_biennium,
        )


SUBSTITUTE_TENURES: tuple[SubstituteTenure, ...] = (
    SubstituteTenure(
        source=WSL_SOURCE_SLUG,
        member_id="232",
        span_kind=KIND_SENATE,
        span_discriminator="22",
        span_start_biennium="1991-92",
        substitute="Lela Kreidler",
        principal="Myron “Mike” Kreidler",
        substitute_clause="Appointed January 14, 1991 to serve until April 18, 1991",
        # printed inside his NAME, which #378's strip_tenure_notes removes from name_full
        principal_clause="(On leave of absence for military duty Jan. 8, 1991 to April 18, 1991)",
        edition=_EDITION,
    ),
    SubstituteTenure(
        source=WSL_SOURCE_SLUG,
        member_id="11176",
        span_kind=KIND_SENATE,
        span_discriminator="6",
        span_start_biennium="2005-06",
        substitute="Jon Wyss",
        principal="Brad Benson",
        substitute_clause="Appointed to serve from April 14, 2005 until April 16, 2005",
        principal_clause="On leave of absence for military duty April 14, 2005 to April 16, 2005",
        edition=_EDITION,
    ),
    SubstituteTenure(
        source=WSL_SOURCE_SLUG,
        member_id="27981",
        span_kind=KIND_SENATE,
        span_discriminator="20",
        span_start_biennium="2017-18",
        substitute="Marlo Braun",
        principal="John E. Braun",
        substitute_clause="Appointed to temporarily serve from July 18, 2017 until July 23, 2017",
        # printed inside his NAME; #144 adjudicated her tenure GENUINE
        principal_clause=(
            "(Granted leave of absence for military duty 7/15-7/23/17, U.S. Naval Reserves)"
        ),
        edition=_EDITION,
    ),
)


def _span_key(source: str, span: TenureSpan) -> tuple[str, str, str, str, str]:
    return (source, span.member_id, span.kind, span.discriminator, span.start_biennium)


def apply_memberships(
    families: dict[str, list[TenureSpan]],
    tenures: tuple[SubstituteTenure, ...] = SUBSTITUTE_TENURES,
) -> dict[str, list[TenureSpan]]:
    """``families`` with each curated span's ``membership_type`` set; order kept."""
    named = {t.key for t in tenures}
    return {
        source: [
            replace(span, membership_type=MEMBERSHIP_SUBSTITUTE)
            if _span_key(source, span) in named
            else span
            for span in spans
        ]
        for source, spans in families.items()
    }


def unmatched_memberships(
    families: dict[str, list[TenureSpan]],
    tenures: tuple[SubstituteTenure, ...] = SUBSTITUTE_TENURES,
) -> list[SubstituteTenure]:
    """The entries no built span carries — an inert adjudication, which the
    ``assignments`` model refuses to build on (the span re-keyed, or its member
    was merged away)."""
    built = {
        _span_key(source, span)
        for source, spans in families.items()
        for span in spans
        if span.membership_type == MEMBERSHIP_SUBSTITUTE
    }
    return [t for t in tenures if t.key not in built]
