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
"""

from __future__ import annotations

import re

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
