"""Never-seated rows: senators the roster lists who never sat (#546).

The roster lists three senators who were elected or appointed and never took the
seat: one resigned before the session, one the Senate refused, one was appointed
and did not serve. Each row still projects a seat span and a party span, and
none of them held either. The person stays — the election or the appointment is
a fact about them — and the tenure rows go.

**A curated table**, the ``memberships.SUBSTITUTE_TENURES`` idiom (#501 D5), not
a parser and not an event:

- a parser would key on the refusal clause's wording, which differs in each of
  the three rows — ARCHITECTURE.md's second rule;
- an operator event states what happened **on a date** (ONTOLOGY.md § 3), and
  two of the three non-services have none;
- ``ARTIFACT_EXCLUSIONS_BY_BIENNIUM`` (#144) filters input rows before
  projection, and these rows must reach the projector: Mulligan's is the only
  interlude between Keiron Reardon's 1943 and 1945 terms, and without it #517's
  return rule re-merges them and his resignation erases 1945-48 again.

Each entry names one roster row's two span identities, ``(source, member_id,
span_kind, span_discriminator, span_start_biennium)`` for the seat and the
party, with the row's clause verbatim. It also pins the row's last biennium: a
span matching the identity but running past it has outgrown the row — a later
listing of the member, rejoining the party span under the same key (#289) — and
is kept, never removed. They are removed in
:func:`~usa_wa_pipeline.conformed.spans.build_families`, after every overlay and
the annotation windows and before the clip, so every caller sees the same
spans and the clip never reads a holder who was not there. An entry that removes
less than it names is stale — a span re-keyed, or its member was merged away —
and the ``assignments`` model refuses to build on it.

**Revisit when** a fourth case appears, or when a source publishes it: then this
becomes an operator attestation with a CLI and raw-store provenance, not a
longer list.
"""

from __future__ import annotations

from dataclasses import dataclass

from clearinghouse_domain_legislative.span_kinds import KIND_PARTY, KIND_SENATE
from clearinghouse_domain_legislative.tenure_spans import TenureSpan
from usa_wa_adapter_legislature.roster_pdf.coverage import ROSTER_SOURCE_SLUG

#: The roster PDF resource every entry was read from (``stg_roster_members.resource_id``).
_EDITION = "legroster:2025-06-05"

SpanIdentity = tuple[str, str, str, str, str]


@dataclass(frozen=True)
class NeverSeated:
    """One roster row whose holder never sat, and the roster evidence for it."""

    member_id: str
    name: str
    district: str
    party: str
    span_start_biennium: str
    #: The row's last biennium: a span running past it is real tenure, never removed.
    span_end_biennium: str
    #: The row's annotation, verbatim.
    clause: str
    edition: str

    def __post_init__(self) -> None:
        for name in ("clause", "edition"):
            if not getattr(self, name):
                raise ValueError(f"a never-seated row needs its evidence: {name} is empty")

    @property
    def keys(self) -> frozenset[SpanIdentity]:
        """The seat and party span identities the row projects."""
        return frozenset(
            (ROSTER_SOURCE_SLUG, self.member_id, kind, discriminator, self.span_start_biennium)
            for kind, discriminator in ((KIND_SENATE, self.district), (KIND_PARTY, self.party))
        )


NEVER_SEATED: tuple[NeverSeated, ...] = (
    NeverSeated(
        member_id="cacouplin:1921",
        name="C. A. Couplin",
        district="9",
        party="republican",
        span_start_biennium="1921-22",
        span_end_biennium="1923-24",
        clause="Elected and subsequently resigned without serving",
        edition=_EDITION,
    ),
    NeverSeated(
        member_id="lenuswestman:1941",
        name="Lenus Westman",
        district="39",
        party="democratic",
        span_start_biennium="1941-42",
        span_end_biennium="1941-42",
        clause="Elected but not seated by vote of the Senate, January 24, 1941",
        edition=_EDITION,
    ),
    NeverSeated(
        member_id="williampmulligan:1943",
        name="William P. Mulligan",
        district="39",
        party="democratic",
        span_start_biennium="1943-44",
        span_end_biennium="1943-44",
        clause="Appointed Feb. 28, 1944 to serve Ex. Session. Did not serve, seat left vacant",
        edition=_EDITION,
    ),
)


def _identity(source: str, span: TenureSpan) -> SpanIdentity:
    return (source, span.member_id, span.kind, span.discriminator, span.start_biennium)


def without_never_seated(
    families: dict[str, list[TenureSpan]],
    entries: tuple[NeverSeated, ...] = NEVER_SEATED,
) -> tuple[dict[str, list[TenureSpan]], list[NeverSeated]]:
    """``families`` without each entry's spans, order kept, and the stale entries:
    those that removed fewer spans than they name — one missing, or grown past the row."""
    end_of = {key: entry.span_end_biennium for entry in entries for key in entry.keys}
    removed: set[SpanIdentity] = set()
    kept: dict[str, list[TenureSpan]] = {}
    for source, spans in families.items():
        kept[source] = []
        for span in spans:
            identity = _identity(source, span)
            if end_of.get(identity) == span.end_biennium:
                removed.add(identity)
            else:
                kept[source].append(span)
    return kept, [entry for entry in entries if not entry.keys <= removed]
