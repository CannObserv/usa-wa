"""Producer-side PM Person dedup for the roster cohort (#256).

PM's write path does **no name matching**: ``resolve_entity`` looks up ``(identifier_type,
identifier_value)`` and creates on a miss, full stop. So a pre-1991 legislator who already
exists in PM under a PDC key — or under no key at all, as an admin-curated record — is
invisible to that path and gets minted a second time. 1,707 of PM's 2,414 active Persons
carry no identifier at all, and the roster cohort adds ~2,494 more in one pass: the check
has to happen on our side, before the write, because the cleanup afterwards is the admin
merge tooling person by person, and merges are lossy in ways a pre-check is not.

The adjudicator here is deliberately conservative. An exact cleaned-name confirm is the only
disposition safe to act on unattended; anything that merely *could* be the same person is
surfaced for review. #228's rule holds — never silently merge or drop.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from clearinghouse_sync_powermap.descriptors import normalize_name
from usa_wa_common.names import (
    probe_surname,
    split_name,
    strip_non_name_parts,
    surname_match_set,
)

#: The roster key already resolves a PM Person — a previous sweep landed this one. The
#: idempotency disposition that makes the sweep safely re-runnable.
DISPOSITION_ALREADY = "already"
#: Exact equality of the cleaned name. The only automatic attach.
DISPOSITION_EXACT = "exact"
#: Surname matches and the given-name initials are compatible — but compatible is not proof
#: (``John A. Smith`` / ``John B. Smith`` share ``j``), so this is a review queue, not a verb.
DISPOSITION_GUARDED = "guarded"
#: More than one survivor. PM's ``identifiers`` table has no uniqueness on ``(type, value)``
#: and ``resolve_entity`` returns an arbitrary row when two share one, so a tie must never
#: be broken by guessing.
DISPOSITION_AMBIGUOUS = "ambiguous"
#: Nothing in PM's candidate window is this person. Mint as normal.
DISPOSITION_NEW = "new"

#: PM identifier types that mean "a local Person in a cohort we already produce owns this
#: record". A roster Person exists precisely BECAUSE the #251 join found no WSL member for
#: it, so a WSL-anchored PM Person cannot also be it — and a roster-keyed one belongs to a
#: different local roster Person. Measured: 54 of 160 guarded verdicts pointed at one of
#: these, including local ``William S. Day`` onto PM's ``William Day``, whose only
#: assignment starts in 1991 — Bill Day Jr, i.e. the son (#228).
OWNED_IDENTIFIER_SLUGS = frozenset(
    {"person_wa_legislature_member_id", "person_wa_legislature_roster"}
)


@dataclass(frozen=True)
class Candidate:
    """One PM person from the surname probe: the summary fields the search returns."""

    pm_id: str
    display_name: str
    #: PM identifier type slugs already on the record, from the person detail read.
    identifier_slugs: tuple[str, ...] = ()


@dataclass(frozen=True)
class Adjudication:
    """The verdict for one local Person against PM's candidate window."""

    disposition: str
    #: The PM ULID — set on :data:`DISPOSITION_EXACT` and :data:`DISPOSITION_GUARDED`, and
    #: always a value **PM itself just returned**, never one we stored or derived.
    pm_id: str | None = None
    #: Display names behind an ambiguous verdict, so the report is actionable.
    candidates: tuple[str, ...] = field(default_factory=tuple)
    #: Display names dropped for being owned by a cohort we already produce. Reported rather
    #: than discarded — a name-plausible record we refused to touch is a finding, not noise.
    excluded: tuple[str, ...] = field(default_factory=tuple)


def _given_initials(cleaned: str) -> set[str]:
    """First letters of every token *before* the surname.

    Given names only, on purpose. The surname's own initial is on both sides by
    construction — the probe matched on it — so folding it in makes every same-surname
    candidate "compatible" and the guard inert. Tokens after the surname are generational
    suffixes and fall out with it.
    """
    split = split_name(cleaned)
    if split is None:
        return set()
    given, _surname = split
    return {token[0] for token in given}


def adjudicate(name_full: str, candidates: list[Candidate]) -> Adjudication:
    """Decide what one local Person is, against PM's surname-probe candidate window.

    Two stages, both on names cleaned by
    :func:`~usa_wa_common.names.strip_non_name_parts`:

    1. **Exact** — the confirm the sidecar already applies. Exactly one survivor is a match.
    2. **Guard** — the #240 given-name-initial test over candidates that share the surname.
       A survivor here is *reported*, never attached unattended.
    """
    surname = probe_surname(name_full)
    if surname is None:
        # No surname means no probe was possible; there is nothing to compare against, and
        # an empty target would match any candidate that also cleans to nothing.
        return Adjudication(DISPOSITION_NEW)

    owned = [c for c in candidates if OWNED_IDENTIFIER_SLUGS.intersection(c.identifier_slugs)]
    excluded = tuple(c.display_name for c in owned)
    candidates = [c for c in candidates if c not in owned]

    cleaned = strip_non_name_parts(name_full)
    target = normalize_name(cleaned)
    exact = [
        c for c in candidates if normalize_name(strip_non_name_parts(c.display_name)) == target
    ]
    if len(exact) == 1:
        return Adjudication(DISPOSITION_EXACT, pm_id=exact[0].pm_id, excluded=excluded)
    if exact:
        return Adjudication(DISPOSITION_AMBIGUOUS, candidates=tuple(c.display_name for c in exact))

    ours = _given_initials(cleaned)
    compatible: list[Candidate] = []
    for candidate in candidates:
        their_name = strip_non_name_parts(candidate.display_name)
        if surname not in surname_match_set(their_name):
            continue  # an FTS prefix neighbour (``bone`` → ``Bonebrake``), not a candidate
        theirs = _given_initials(their_name)
        if not theirs or not ours:
            # A bare-surname record — PM holds Persons whose whole display name is
            # ``Morgan`` — is a stub, not a corroboration. #240's "a missing given name is
            # never evidence against" belongs to a context where the other side was a WSL
            # member row; here it made one stub compatible with every local person sharing
            # the surname (70 of 165 guarded verdicts pointed at a PM id claimed by 2-6
            # locals, one ``Morgan`` absorbing six). Excluded, not counted: a stub must not
            # manufacture an ambiguity that suppresses a real lone candidate either.
            continue
        if theirs & ours:
            compatible.append(candidate)
    if len(compatible) == 1:
        return Adjudication(DISPOSITION_GUARDED, pm_id=compatible[0].pm_id, excluded=excluded)
    if compatible:
        return Adjudication(
            DISPOSITION_AMBIGUOUS,
            candidates=tuple(c.display_name for c in compatible),
            excluded=excluded,
        )
    return Adjudication(DISPOSITION_NEW, excluded=excluded)
