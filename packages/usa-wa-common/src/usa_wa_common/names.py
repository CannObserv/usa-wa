"""Name folding and the token-set surname match (#189).

The messy half of every cross-source person match in this deployment: a WSL member's clean
``LastName`` on one side, a free-form ballot or filer name on the other. The WSL House roster,
the SOS results normalizer and the roster identity join all fold through here, so the folding
rules have to be one implementation — a divergence here silently mismatches people rather than
erroring.

Folding is **local** on purpose: a package below the adapters could not import the Layer-4
PM sidecar's ``normalize_name``, which #314 has since deleted.

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


#: Parenthetical segments — marital forms, printed nicknames, the odd leaked
#: annotation. The inner text is captured because :func:`strip_tenure_notes`
#: has to read it to decide; a capture group is inert for the ``.sub(" ", …)``
#: callers, so ONE pattern serves all three rather than a plain twin sitting
#: beside a grouped one. This module's sibling consolidation records why that
#: matters — `conformed/crosswalk.py`: "Divergence between those copies is what
#: #366 was" (CR 155).
_PARENTHETICAL = re.compile(r"\(([^)]*)\)")

#: Quoted nicknames: ``“Red”``, ``"Slim"``. The same person carries them in some listings
#: and not others, so they cannot participate in matching.
_QUOTED = re.compile(r"[“\"][^”\"]*[”\"]")

#: Honorifics carry no identity. Generational suffixes (``jr``/``sr``) are NOT here — they
#: distinguish two real people (usa-wa#228's Bill Day / Bill Day Jr).
_HONORIFICS = frozenset({"mr", "mrs", "ms", "dr", "rev", "hon"})


def strip_non_name_parts(full_name: str) -> str:
    """An upstream name with everything that is not a name removed.

    Quoted nicknames, parentheticals and honorific tokens. Shared (usa-wa#256) because two
    consumers had to agree on what counts as a name: the roster identity fold, and the PM
    person match (deleted with the sidecar, #314) — where a raw ``Belle (Mrs. Frank) Reeves``
    matched nothing, since PM's FTS ANDs every token and no PM name carries ``mrs`` or
    ``frank``. Divergence there mismatches people silently rather than erroring, which is the
    failure this module exists to prevent.
    """
    cleaned = _QUOTED.sub(" ", _PARENTHETICAL.sub(" ", full_name))
    kept = [word for word in cleaned.split() if fold_token(word) not in _HONORIFICS]
    return " ".join(kept)


#: A parenthetical this long is prose, not a name. The roster's name-shaped
#: parentheticals run to three tokens (``(Mrs. Thomas E.)``); its shortest
#: annotation runs to ten (``(Select House Cmte upheld election challenge, Hogan
#: declared duly elected)``). The threshold sits in a gap that wide on purpose —
#: it is a screen against prose, not a measurement of one corpus.
_NOTE_MAX_TOKENS = 4

#: A digit inside a parenthetical means a date, and a date means an event.
_HAS_DIGIT = re.compile(r"\d")


def _is_tenure_note(inner: str) -> bool:
    """Is this parenthetical's content an annotation rather than name content?"""
    return bool(_HAS_DIGIT.search(inner)) or len(inner.split()) > _NOTE_MAX_TOKENS


def strip_tenure_notes(full_name: str) -> str:
    """An upstream name with printed *annotations* removed and names left alone.

    The narrowest of this module's four name screens, and the only one meant for
    a name that gets **published** rather than matched. The roster's name column
    carries two unrelated things in parentheses (usa-wa#378):

    * **tenure events** — ``(Resgnd Dec. 31, 1982)``, ``(On leave of absence for
      military duty Jan. 8, 1991 to April 18, 1991)``, ``(Left Seattle July 2,
      1900 Named Court Clerk, 3rd Judcl Dvn, AK Terr.)``. Not names at all: facts
      about a person's service, printed inside the name. These go.
    * **name content** — marital print forms (``(Mrs. Thomas E.)``) and legal-name
      glosses (``Jack (John T.) Dootson``). These stay.

    Why this is not :func:`strip_non_name_parts`, which would be the obvious
    reach: that function is documented for *matching* and drops every
    parenthetical, every quoted nickname and every honorific. Publishing its
    output would strip ``A. L. "Slim" Rasmussen`` down to ``A. L. Rasmussen``
    (his quoted nickname is how he is known, and five already-published people
    carry one), and would turn ``Mrs. Irwin LeCocq (Mary)`` into ``Irwin
    LeCocq`` — her husband's name, published as hers. A matching screen may
    over-strip because nothing downstream reads its output; a publication screen
    may not.

    **Whether a woman should be published under a marital form at all is a real
    editorial question and deliberately not answered here** (usa-wa#378 follow-on
    2). Leaving those untouched keeps it a decision someone makes, rather than
    one this function makes silently on the way past.

    The discriminator is a digit or more than :data:`_NOTE_MAX_TOKENS` tokens.
    Both halves are load-bearing: ``(Select House Cmte upheld election
    challenge, Hogan declared duly elected)`` carries no digit, and ``(Mary)``
    carries no prose, so neither test alone separates the two classes.
    """

    def _keep(match: re.Match[str]) -> str:
        return "" if _is_tenure_note(match.group(1)) else match.group(0)

    stripped = _PARENTHETICAL.sub(_keep, full_name)
    return stripped if stripped == full_name else " ".join(stripped.split())


def split_by_given_name(
    row_tokens: Collection[str],
    candidates: Mapping[str, Collection[str]],
    *,
    ignore_full: Collection[str] = (),
) -> tuple[set[str], set[str]]:
    """``(compatible, rejected)`` candidate ids, split on given-name agreement with a row.

    Single-sourced (usa-wa#277) when **two** consumers asked the same question — *can this WSL
    member be the person this roster row names?* — and had drifted: the roster succession
    resolver's member lookup and the identity join's WSL lookup. The resolver was deleted in
    #471; the identity join is the one consumer now.

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
    surname. Every candidate is surname-matched by construction, so
    counting it is free for all of them, and a rival whose given name merely *is* that surname
    would be promoted into the tier that then rejects the true subject.

    A blank candidate given name is always compatible, however its siblings match: absence of
    the signal is never evidence against a match (#240). Two candidates agreeing in full stay
    compatible — this narrows, it never breaks a tie by fiat; reporting the tie is the
    caller's job.

    Callers pass **already-folded** tokens, prepared their own way: the identity join strips
    position suffixes so the guard reads the same string its fold does.
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


def folded_tokens(full_name: str) -> list[str]:
    """The ordered folded tokens of a free-form upstream name.

    Split only on whitespace and grouping punctuation (parens / commas), then fold each
    token — so intra-surname apostrophes and hyphens stay *inside* the token and are
    stripped by :func:`fold_token`, matching the WSL side. A whole-name split on every
    non-alnum would shred ``"Ortiz-Self"`` into ``ortiz`` + ``self`` and never match the
    WSL surname ``ortizself``.

    Public because the split rule has a second consumer: the roster identity join's
    given-name guard (#240) needs the *atomic* tokens, not :func:`surname_match_set`'s
    concatenations.
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
