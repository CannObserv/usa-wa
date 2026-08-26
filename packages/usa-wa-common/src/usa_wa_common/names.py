"""Name folding and the token-set surname match (#189).

The messy half of every cross-source person match in this deployment: a WSL member's clean
``LastName`` on one side, a free-form ballot or filer name on the other. Both the PDC matcher
and the two SOS normalizers use exactly these two functions, so the folding rules have to be
one implementation — a divergence here silently mismatches people rather than erroring.

Folding is **local** on purpose: a package below the adapters must not import the Layer-4
sidecar's ``normalize_name``.

The match strategy is a token-set test, not surname extraction. Upstream names are
inconsistently formatted (``"Strom Peterson"``, ``"JACOBSEN CYNTHIA P (Cyndy Jacobsen)"``,
``"J.T. Wilcox (JT Wilcox)"``), so rather than guess which token is the surname, fold every
alpha token and test whether the clean ``LastName`` is among them — robust within an LD's ≤2
winners.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection, Mapping

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _unaccent(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def fold_token(token: str) -> str:
    """Fold one name token for matching: casefold, unaccent, strip non-alphanumerics.

    ``"García"`` → ``"garcia"``, ``"O'Brien"`` → ``"obrien"``."""
    return _NON_ALNUM.sub("", _unaccent(token.casefold()))


#: Parenthetical segments — marital forms, printed nicknames, the odd leaked annotation.
_PARENTHETICAL = re.compile(r"\([^)]*\)")

#: Quoted nicknames: ``“Red”``, ``"Slim"``. The same person carries them in some listings
#: and not others, so they cannot participate in matching.
_QUOTED = re.compile(r"[“\"][^”\"]*[”\"]")

#: Honorifics carry no identity. Generational suffixes (``jr``/``sr``) are NOT here — they
#: distinguish two real people (usa-wa#228's Bill Day / Bill Day Jr).
_HONORIFICS = frozenset({"mr", "mrs", "ms", "dr", "rev", "hon"})


def strip_non_name_parts(full_name: str) -> str:
    """An upstream name with everything that is not a name removed.

    Quoted nicknames, parentheticals and honorific tokens. Shared (usa-wa#256) because two
    consumers must agree on what counts as a name: the roster identity fold, and the PM
    person match — where a raw ``Belle (Mrs. Frank) Reeves`` matched nothing, since PM's FTS
    ANDs every token and no PM name carries ``mrs`` or ``frank``. Divergence there mismatches
    people silently rather than erroring, which is the failure this module exists to prevent.
    """
    cleaned = _QUOTED.sub(" ", _PARENTHETICAL.sub(" ", full_name))
    kept = [word for word in cleaned.split() if fold_token(word) not in _HONORIFICS]
    return " ".join(kept)


def split_by_given_name(
    row_tokens: Collection[str],
    candidates: Mapping[str, Collection[str]],
    *,
    ignore_full: Collection[str] = (),
) -> tuple[set[str], set[str]]:
    """``(compatible, rejected)`` candidate ids, split on given-name agreement with a row.

    Single-sourced (usa-wa#277) because there are **two** consumers that must not drift: the
    roster succession resolver's member lookup and the identity join's WSL lookup. Both ask
    the same question — *can this WSL member be the person this roster row names?* — and both
    were written as the same rule, mirrored by hand. Rewriting one and not the other diverged
    them silently, which is the failure this module exists to prevent.

    Two tiers (#240 established the first, #277 added the second):

    * A shared **whole token** is the strong signal: ``tony`` picks ``Tony P`` over
      ``August P``, ``robert`` picks ``Robert C`` over ``Ruthe``. When *some* candidate agrees
      that way, the ones that do not are rejected.
    * When **none** does, the tier falls back to the given-name **initial**, which carries the
      benign variants the corpora are full of — ``Mike``/``Michael``, ``Moyne``/``Mike``,
      ``J. Bruce``/``Jeffrey``, ``C Louise``/``Louise``. A given name can itself be several
      tokens, so *any* of them agreeing is agreement.

    Tiering rather than replacing matters both ways: the initial rule alone leaves a
    same-initial relative compatible, and the full-token rule alone refuses every
    initials-only row the initial rule exists to keep.

    ``ignore_full`` names tokens that must not count as a whole-token match — the shared
    surname, in both consumers. Every candidate is surname-matched by construction, so
    counting it is free for all of them, and a rival whose given name merely *is* that surname
    would be promoted into the tier that then rejects the true subject.

    A blank candidate given name is always compatible, however its siblings match: absence of
    the signal is never evidence against a match (#240). Two candidates agreeing in full stay
    compatible — this narrows, it never breaks a tie by fiat; reporting the tie is the
    caller's job.

    Callers pass **already-folded** tokens, and choose their own preparation: the resolver
    reads the row through :func:`strip_other_party_parts` (a nickname is identity, a marital
    parenthetical is somebody else), while the identity join additionally strips position
    suffixes so the guard reads the same string its fold does.
    """
    row = {token for token in row_tokens if token}
    full_keys = {token for token in row if len(token) > 1} - set(ignore_full)
    row_initials = {token[0] for token in row}
    prepared = {
        candidate_id: {token for token in tokens if token}
        for candidate_id, tokens in candidates.items()
    }
    full_matched = {
        candidate_id
        for candidate_id, tokens in prepared.items()
        if {token for token in tokens if len(token) > 1} & full_keys
    }

    compatible: set[str] = set()
    rejected: set[str] = set()
    for candidate_id, tokens in prepared.items():
        if not tokens:
            compatible.add(candidate_id)
        elif full_matched:
            (compatible if candidate_id in full_matched else rejected).add(candidate_id)
        elif {token[0] for token in tokens} & row_initials:
            compatible.add(candidate_id)
        else:
            rejected.add(candidate_id)
    return compatible, rejected


def strip_other_party_parts(full_name: str) -> str:
    """An upstream name with only the parts naming **somebody else** removed.

    The narrower sibling of :func:`strip_non_name_parts`, for consumers that ask *"which of
    these tokens could be this person's own name?"* rather than *"which tokens are a name?"*.
    The two differ on the quoted nickname, and the difference is load-bearing (usa-wa#277):

    * A **parenthetical** marital form names a *third party*. ``Frances (Mrs. Thomas A.)
      Swayze`` contributes ``thomas`` and ``a``, which are her husband's — letting him pass a
      same-tokens identity guard as though he were her.
    * A **quoted nickname** is this person's own other name, and WSL often records it as the
      ``FirstName`` outright: the roster prints ``Robert "Bob" McCaslin,`` and WSL carries
      ``Bob``, so dropping it removes the only token the two sides share.

    :func:`strip_non_name_parts` drops both, which is right for PM's full-name FTS (where a
    nickname the other side lacks ANDs the query to nothing) and wrong for an identity guard.
    Honorifics go either way — they name nobody — so they are dropped here too.
    """
    kept = [
        word
        for word in _PARENTHETICAL.sub(" ", full_name).split()
        if fold_token(word) not in _HONORIFICS
    ]
    return " ".join(kept)


def folded_tokens(full_name: str) -> list[str]:
    """The ordered folded tokens of a free-form upstream name.

    Split only on whitespace and grouping punctuation (parens / commas), then fold each
    token — so intra-surname apostrophes and hyphens stay *inside* the token and are
    stripped by :func:`fold_token`, matching the WSL side. A whole-name split on every
    non-alnum would shred ``"Ortiz-Self"`` into ``ortiz`` + ``self`` and never match the
    WSL surname ``ortizself``.

    Public because the split rule has a second consumer: the roster resolver's given-name
    guard (#240) needs the *atomic* tokens, not :func:`surname_match_set`'s concatenations.
    Re-deriving the split there would fork the folding rule, which this module exists to
    prevent — a divergence mismatches people silently rather than erroring."""
    return [folded for raw in re.split(r"[\s(),]+", full_name) if (folded := fold_token(raw))]


#: Generational suffixes. Emphatically NOT honorifics — ``Jr`` is what distinguishes two
#: real people (usa-wa#228's Bill Day / Bill Day Jr), so it stays in the name and in every
#: identity comparison. It is simply never the *surname*. A bare roman ``v`` is deliberately
#: absent: it is far more often a middle initial than a fifth of a line.
_GENERATIONAL = frozenset({"jr", "sr", "ii", "iii", "iv"})


def probe_surname(full_name: str) -> str | None:
    """The folded token to *search* an upstream name by — its surname — or ``None``.

    Trailing generational suffixes are dropped, because the last token of
    ``Kemper Freeman, Jr.`` is ``jr``: a query that returns every PM name carrying that
    suffix (6 people, measured live) rather than the six Freemans. 25 of the roster's 2,494
    pre-1991 Persons end in one. Dropped from the *probe* only — the caller still confirms
    on the full cleaned name, where the suffix is load-bearing.

    ``None`` when nothing survives folding: the caller must not send an empty query, which
    would match on the search backend's ranking alone.
    """
    tokens = folded_tokens(strip_non_name_parts(full_name))
    while tokens and tokens[-1] in _GENERATIONAL:
        tokens.pop()
    return tokens[-1] if tokens else None


def split_name(full_name: str) -> tuple[list[str], str] | None:
    """``(given_tokens, surname)`` for an upstream name, or ``None`` if it folds to nothing.

    One definition of where the surname sits. Three call sites were re-deriving it — the
    roster identity seating index and the PM dedup adjudicator (usa-wa#226 CR) — and a
    divergence in this family of logic mismatches people silently rather than erroring, which
    is the whole reason this module exists.

    Trailing generational suffixes fall out with the surname: they belong to neither half,
    and :func:`probe_surname` already refuses to treat one as the surname. A bare surname
    yields empty given tokens — the shape a stub record takes — which callers must read as
    "no given-name evidence", never as agreement.
    """
    cleaned = strip_non_name_parts(full_name)
    surname = probe_surname(cleaned)
    if surname is None:
        return None
    tokens = folded_tokens(cleaned)
    last = len(tokens) - 1 - tokens[::-1].index(surname)
    return tokens[:last], surname


def surname_match_set(full_name: str) -> set[str]:
    """The set of folded name keys an upstream name matches on — atomic folded tokens
    (single words; single-letter initials survive but won't false-match a surname) **plus
    every consecutive-run concatenation** of them.

    The WSL side folds a member's ``LastName`` with :func:`fold_token`, which strips *all*
    non-alphanumerics **including spaces** — so a multi-word / particle surname collapses to
    one token (``"Van De Wege"`` → ``vandewege``) while the space-split upstream name yields
    ``{van, de, wege}``. Adding the consecutive joins (``van``, ``vande``, ``vandewege``, …)
    makes the joined WSL surname testable by membership without a fragile substring match.
    The WSL member's folded ``LastName`` is tested against this set to confirm a within-LD
    match."""
    tokens = folded_tokens(full_name)
    keys = set(tokens)
    for start in range(len(tokens)):
        joined = ""
        for token in tokens[start:]:
            joined += token
            keys.add(joined)
    return keys
