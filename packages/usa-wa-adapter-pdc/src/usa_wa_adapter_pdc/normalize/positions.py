"""Pure helpers for the PDC House-position normalizer.

Deterministic ``source_id`` builders, the PDC ``position`` → PM ``qualifier`` mapping,
the ``person_wa_pdc`` identifier scheme, and the name-folding primitives that back the
within-LD match of a PDC winner to the existing WSL :class:`Person`.

Name folding is **local** (a Layer-3 adapter must not import the Layer-4 sidecar's
``normalize_name``). The match strategy is a token-set test, not surname extraction:
PDC ``filer_name`` is inconsistently formatted (``"Strom Peterson"``,
``"JACOBSEN CYNTHIA P (Cyndy Jacobsen)"``, ``"J.T. Wilcox (JT Wilcox)"``), so rather than
guess which token is the surname, we fold every alpha token and test whether the WSL
member's clean ``LastName`` is among them — robust within an LD's ≤2 winners.
"""

from __future__ import annotations

import re
import unicodedata

#: Local ``PersonIdentifier.scheme`` for the PDC person id. The person descriptor maps a
#: Person's ``usa_wa_pdc`` source (and this child scheme) to the PM ``person_wa_pdc``
#: identifier_type; here the identifier is a *child* row on the WSL-sourced Person, carried
#: to PM as an ``additional_identifier``.
PDC_PERSON_ID_SCHEME = "wa_pdc"

#: WA House positions (the only ones this cut resolves — ballot has Position 1 / 2 per LD).
_VALID_POSITIONS = {"1", "2"}

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _unaccent(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def canonical_position(raw: object) -> str | None:
    """Map a PDC ``position`` (``"1"`` / ``"2"``, possibly int/padded) to the PM seat
    ``qualifier`` (``"Position 1"`` / ``"Position 2"``, power-map#263). Anything else
    (blank, ``0``, ``3``, non-numeric) → ``None`` (not a House seat we can key)."""
    if raw is None:
        return None
    text = str(raw).strip()
    if text not in _VALID_POSITIONS:
        return None
    return f"Position {text}"


def house_seat_role_source_id(ld_number: int, qualifier: str) -> str:
    """Deterministic ``source_id`` for a House ``state_representative`` seat Role (one per
    ``(LD, position)``) — aligns 1:1 with PM's seat match key."""
    slug = qualifier.lower().replace(" ", "-")
    return f"seat:house:ld-{ld_number}:{slug}"


def house_seat_assignment_source_id(member_id: str, biennium: str) -> str:
    """Deterministic ``Assignment.source_id`` for a House chamber seat — role-independent
    (the role is a *value* of the assignment), symmetric with P1b's Senate
    ``{member_id}:chamber-senate:{biennium}``."""
    return f"{member_id}:chamber-house:{biennium}"


def pdc_person_identifier_source_id(pdc_person_id: str) -> str:
    """Deterministic ``PersonIdentifier.source_id`` for the PDC id child row."""
    return f"{pdc_person_id}:{PDC_PERSON_ID_SCHEME}"


def fold_token(token: str) -> str:
    """Fold one name token for matching: casefold, unaccent, strip non-alphanumerics.

    ``"García"`` → ``"garcia"``, ``"O'Brien"`` → ``"obrien"``."""
    return _NON_ALNUM.sub("", _unaccent(token.casefold()))


def _folded_sequence(filer_name: str) -> list[str]:
    """The ordered folded tokens of a PDC ``filer_name``.

    Split only on whitespace and grouping punctuation (parens / commas), then fold each
    token — so intra-surname apostrophes and hyphens stay *inside* the token and are
    stripped by :func:`fold_token`, matching the WSL side. A whole-name split on every
    non-alnum would shred ``"Ortiz-Self"`` into ``ortiz`` + ``self`` and never match the
    WSL surname ``ortizself``."""
    return [folded for raw in re.split(r"[\s(),]+", filer_name) if (folded := fold_token(raw))]


def surname_match_set(filer_name: str) -> set[str]:
    """The set of folded name keys a PDC ``filer_name`` matches on — atomic folded tokens
    (single words; single-letter initials survive but won't false-match a surname) **plus
    every consecutive-run concatenation** of them.

    The WSL side folds a member's ``LastName`` with :func:`fold_token`, which strips *all*
    non-alphanumerics **including spaces** — so a multi-word / particle surname collapses to
    one token (``"Van De Wege"`` → ``vandewege``) while the space-split PDC name yields
    ``{van, de, wege}``. Adding the consecutive joins (``van``, ``vande``, ``vandewege``, …)
    makes the joined WSL surname testable by membership without a fragile substring match.
    The WSL member's folded ``LastName`` is tested against this set to confirm a within-LD
    match."""
    tokens = _folded_sequence(filer_name)
    keys = set(tokens)
    for start in range(len(tokens)):
        joined = ""
        for token in tokens[start:]:
            joined += token
            keys.add(joined)
    return keys
