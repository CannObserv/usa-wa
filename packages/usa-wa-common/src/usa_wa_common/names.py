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
