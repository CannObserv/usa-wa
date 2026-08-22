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
