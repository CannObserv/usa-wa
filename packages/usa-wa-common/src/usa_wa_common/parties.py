"""Party canonicalization — one slug vocabulary for every WA source (#189).

Two upstreams, two encodings, one target vocabulary: WSL's ``Party`` field (``"R"`` /
``"Republican"``) and the SOS ballot's preference string (``"(Prefers Republican Party)"``,
sometimes ``GOP``). Both fold to the same slug, because the slug is what PM's
``org_wa_party`` identifier carries (power-map#270) and what the party-Org synthesis keys on.

`canonicalize_party` was defined in `usa_wa_adapter_legislature.normalize.members` — the WSL
SOAP normalizer — and imported by the PDC matcher, the PDC observation builder and the SOS
ballot module. `sos_party_slug` was defined in `usa_wa_adapter_sos.positions` and wrapped it.
Neither is about a wire: the *parse* is, the vocabulary is not.

**No Independent slug** — independent is the *absence* of a party assignment (power-map#270),
so an unrecognised or blank value canonicalizes to ``None`` and emits nothing.

**Two entry points, two input domains (#227).** ``canonicalize_party`` folds *wire* values —
what WSL's SOAP ``Party`` field and the SOS ballot string carry, which is R/D and nothing else.
``resolve_party_token`` folds the **roster PDF's** historical abbreviations, which run to seven
tokens across 166 member-year records back to 1891. They are kept apart deliberately, and the
issue's own wording ("extend ``canonicalize_party``") is not followed to the letter, because
``sos_party_slug`` splits its input on whitespace and punctuation before folding each piece:
put the historical ``S`` → socialist mapping in the shared table and a stray ``S`` anywhere in
a ballot string starts resolving to the Socialist Party. The hazard is one-directional, so the
vocabularies are too.

**Why a disposition instead of ``str | None`` (#227).** The R/D canonicaliser answers with a
slug or ``None``, and ``None`` is overloaded three ways: *deliberately unaffiliated*,
*not a party at all*, and *nobody has classified this token yet*. Collapsing the third into the
first is precisely how 166 records would have vanished on a run that reported success. So the
roster resolver returns a :class:`PartyResolution` whose ``disposition`` separates them, and the
caller that wants a slug has to walk past the tally to get one.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field

_PARTY_CANON = {
    "r": "republican",
    "republican": "republican",
    "d": "democratic",
    "democrat": "democratic",
    "democratic": "democratic",
}

#: WA SOS **ballot** party synonyms the WSL canonicaliser doesn't fold — e.g. the ``GOP``
#: abbreviation ``results.vote.wa.gov`` sometimes prints for Republican candidates (audited #101).
_BALLOT_PARTY_SYNONYMS = {"gop": "republican"}


def canonicalize_party(raw: str | None) -> str | None:
    """Fold a WSL ``Party`` value (either endpoint encoding) to a canonical slug.

    ``"R"``/``"Republican"`` → ``republican``; ``"D"``/``"Democrat"``/``"Democratic"`` →
    ``democratic``. Independent / blank / unknown → ``None`` (no party Assignment)."""
    if not raw:
        return None
    return _PARTY_CANON.get(raw.strip().lower())


def sos_party_slug(party_name: str | None) -> str | None:
    """Canonicalize a WA SOS ballot party string (``"(Prefers Republican Party)"``) to a party
    slug, reusing :func:`canonicalize_party` on the embedded token plus a small SOS-ballot
    synonym map. Non-partisan / blank / unrecognised → ``None``."""
    if not party_name:
        return None
    for token in re.split(r"[\s(),]+", party_name):
        if not token:
            continue
        slug = canonicalize_party(token) or _BALLOT_PARTY_SYNONYMS.get(token.lower())
        if slug is not None:
            return slug
    return None


# --- the roster PDF's historical vocabulary (#227) ---------------------------

#: Every slug this module can emit. The contract with Power Map: each one is the
#: ``org_wa_party`` identifier value of a real Org (power-map#270/#442/#443), so a party is
#: addressed by identifier rather than by name-match or ULID. A slug the resolver could emit
#: but this set did not declare would have no Org to attach to.
PARTY_SLUGS = frozenset(
    {
        "republican",
        "democratic",
        "peoples",
        "populist",
        "progressive",
        "silver-republican",
        "farmer-labor",
        "socialist",
    }
)

#: The token resolved to a party Org.
PARTY_RESOLVED = "resolved"
#: The token was recognised and deliberately yields **no** party Assignment — unaffiliated, a
#: ballot label rather than an organisation, or a year outside the Org's lifespan. A decision,
#: counted as one.
PARTY_DECLINED = "declined"
#: Nobody has classified this token. Never silently dropped: the caller must tally it, and a
#: future roster edition introducing a new abbreviation has to fail loudly rather than run clean.
PARTY_UNRECOGNIZED = "unrecognized"


@dataclass(frozen=True)
class PartyResolution:
    """What one roster party token resolved to, and — when it resolved to nothing — why.

    ``slug`` is non-``None`` exactly when ``disposition`` is :data:`PARTY_RESOLVED`. ``reason``
    is set on both non-resolving dispositions so a tally can distinguish "we decided this emits
    nothing" from "we have never seen this". ``token`` carries the input verbatim, which is what
    makes an unrecognized tally actionable rather than just a count.
    """

    token: str | None
    slug: str | None
    disposition: str
    reason: str | None = None

    def __post_init__(self) -> None:
        """Enforce the slug/disposition invariant the docstring states (CR #51).

        Not decoration: ``tally_party_tokens`` indexes ``resolved`` by ``slug``, so a
        resolution claiming success with no slug would put a ``None`` key into a
        ``Counter[str]`` — corrupting the census that is supposed to be the arithmetic proof
        that nothing was dropped.
        """
        resolved = self.disposition == PARTY_RESOLVED
        if resolved and self.slug is None:
            raise ValueError("a resolved PartyResolution must carry a slug")
        if not resolved and self.slug is not None:
            raise ValueError(f"a {self.disposition} PartyResolution must not carry a slug")
        if resolved and self.slug not in PARTY_SLUGS:
            raise ValueError(f"slug {self.slug!r} is not in the declared vocabulary")


#: Roster abbreviations that map to a party Org regardless of session year.
#:
#: ``s`` and ``soc.`` are the **same** party — the roster spells the Socialist Party of
#: Washington both ways, and both address the one Org (CR #49). The parser declares both
#: (``PARTY_TOKENS``), so leaving ``Soc.`` out here would have put a token its own upstream
#: recognises into the "nobody has adjudicated this" bucket.
_ROSTER_PARTY_CANON = {
    "r": "republican",
    "d": "democratic",
    "p.p.": "peoples",
    "pop.": "populist",
    "silver rep.": "silver-republican",
    "f.l.": "farmer-labor",
    "s": "socialist",
    "soc.": "socialist",
}

#: Roster abbreviations that are recognised and yield no party Assignment, with the reason.
#: ``Cit.`` is here rather than in the canon because the 1907 Jefferson County pair elected on
#: the Citizen's Party ticket "identified as a Republican and the other as a Democrat" once
#: seated — municipal archives show the label as a hyper-local ballot line, not a state party.
#: A ballot label is not an organisation, so there is no Org for a slug to point at.
#: ``ind`` is the dotless spelling the parser also declares (CR #49) — same decision.
_ROSTER_PARTY_DECLINED = {
    "cit.": "ballot_label",
    "independent": "unaffiliated",
    "ind.": "unaffiliated",
    "ind": "unaffiliated",
}

#: Tokens whose Org existed only across a bounded run of session years — ``token: (slug,
#: first_year, last_year)``, inclusive. The slug is spelled out rather than derived from the
#: token, so the vocabulary stays a lookup rather than a transformation.
#:
#: ``Prog.`` is the only one, and it is not a nicety. The label spans two nationally distinct
#: parties — Roosevelt's 1912 Bull Moose formation and La Follette's 1924 revival — and the Org
#: covers the former alone: 38 legislators in 1913, 10 in 1915, one senator in 1917, none by
#: 1919. The roster's lone 1927 row is Knute Hill (58th LD, seated 1927-01-10), elected a decade
#: after that body dissolved, who ran Farmer-Labor for Congress in 1920 and 1924 and entered
#: Congress a Democrat in 1933. A flat token→Org lookup would assert a membership that never
#: existed (power-map#442).
_ROSTER_PARTY_WINDOWS = {"prog.": ("progressive", 1913, 1917)}


def resolve_party_token(raw: str | None, *, year: int | None) -> PartyResolution:
    """Fold one roster PDF party abbreviation to a party slug, or say why it does not (#227).

    ``year`` is keyword-only and required — not defaulted — so a caller cannot omit it by
    accident on a token whose meaning depends on it. Passing ``None`` is an explicit statement
    of ignorance and surfaces as :data:`PARTY_UNRECOGNIZED` / ``year_required`` rather than as a
    plausible-looking ``None``.

    **Which year (CR #56).** It is the roster's ``RosterRecord.year`` — the session year a
    member's *term begins*, not every session they sit in. The roster lists a senator only at
    term start (see ``roster_pdf.audit``), so a Progressive senator seated in 1915 serves through
    the 1919 session on one record dated 1915. Two consequences a span builder must not
    discover later: the window is compared against a term-start, so passing a *sitting* year
    gives different answers at the boundary; and a span opened from a 1915 term-start runs past
    the Org's 1917 lifespan, which is #228's to resolve, not this function's.

    Matching folds case and **all** whitespace — surrounding and interior — but not interior
    punctuation: the roster is OCR-adjacent text, so ``Silver  Rep.`` with a doubled or
    non-breaking space must not fall to ``unknown_token`` (CR #54, and AGENTS.md's rule against
    keying a parser on an exact upstream string), while ``P.P.`` and ``Pop.`` are distinguished
    by exactly that punctuation.

    ``token`` on the returned resolution is always the stripped form, on every path (CR #55).
    """
    token = " ".join(raw.split()) if raw else None
    if not token:
        token = None  # whitespace-only collapses to nothing, same as blank (CR #55)
        return PartyResolution(
            token=token, slug=None, disposition=PARTY_DECLINED, reason="unaffiliated"
        )
    key = token.lower()

    slug = _ROSTER_PARTY_CANON.get(key)
    if slug is not None:
        return PartyResolution(token=token, slug=slug, disposition=PARTY_RESOLVED)

    reason = _ROSTER_PARTY_DECLINED.get(key)
    if reason is not None:
        return PartyResolution(token=token, slug=None, disposition=PARTY_DECLINED, reason=reason)

    window = _ROSTER_PARTY_WINDOWS.get(key)
    if window is not None:
        if year is None:
            return PartyResolution(
                token=token,
                slug=None,
                disposition=PARTY_UNRECOGNIZED,
                reason="year_required",
            )
        windowed_slug, first, last = window
        if first <= year <= last:
            return PartyResolution(token=token, slug=windowed_slug, disposition=PARTY_RESOLVED)
        return PartyResolution(
            token=token,
            slug=None,
            disposition=PARTY_DECLINED,
            reason="outside_org_lifespan",
        )

    return PartyResolution(
        token=token, slug=None, disposition=PARTY_UNRECOGNIZED, reason="unknown_token"
    )


@dataclass
class PartyTally:
    """The disposition census over a set of ``(token, year)`` pairs (#227).

    The shape the never-silently-drop rule needs: three counters that must add up to the input
    size, so "we emitted fewer spans than there were records" is arithmetic rather than
    inference. ``unrecognized`` is keyed by the **stripped token** so a non-clean tally names
    what to go classify; the other two are keyed by slug and reason.

    Deliberately **not** ``frozen`` (CR #52): the counters are mutated in place while tallying,
    and ``frozen=True`` blocks only attribute rebinding — it would have advertised an
    immutability this type does not have.
    """

    resolved: Counter[str] = field(default_factory=Counter)
    declined: Counter[str] = field(default_factory=Counter)
    unrecognized: Counter[str] = field(default_factory=Counter)

    @property
    def total(self) -> int:
        """Every pair seen — the sum of the three counters, by construction."""
        return (
            sum(self.resolved.values())
            + sum(self.declined.values())
            + sum(self.unrecognized.values())
        )

    @property
    def clean(self) -> bool:
        """Whether every token was classified. False means a roster edition introduced an
        abbreviation nobody has adjudicated, which must fail loudly rather than emit a
        quietly smaller cohort."""
        return not self.unrecognized


def tally_party_tokens(pairs: Iterable[tuple[str | None, int | None]]) -> PartyTally:
    """Resolve every ``(party_token, session_year)`` pair and count the dispositions (#227).

    Source-agnostic on purpose — it takes pairs, not roster records — so this stays Layer-2b
    vocabulary bookkeeping and the adapter keeps its own types.
    """
    tally = PartyTally()
    for token, year in pairs:
        result = resolve_party_token(token, year=year)
        if result.disposition == PARTY_RESOLVED and result.slug is not None:
            # ``__post_init__`` makes the slug guard unreachable (CR #51); it is written as a
            # condition rather than an ``assert`` because ruff's bandit rules run here and an
            # assert would vanish under ``-O``. Were it ever false the pair would fall through
            # to the unrecognized branch — counted, which is the safe direction.
            tally.resolved[result.slug] += 1
        elif result.disposition == PARTY_DECLINED:
            tally.declined[result.reason or "unspecified"] += 1
        else:
            tally.unrecognized[result.token or ""] += 1  # blank never reaches here
    return tally
