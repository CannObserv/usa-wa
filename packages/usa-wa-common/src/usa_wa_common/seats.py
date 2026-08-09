"""WA legislative seat keying — LDs, House positions, span discriminators (#189).

How a Washington legislative seat is *named*: the district number, the ballot
``Position 1``/``Position 2`` qualifier, the deterministic seat-Role ``source_id``, and the
tenure-span discriminator that encodes both. Every one of these is a fact about the seat, not
about the feed that mentioned it — but they lived in `usa_wa_adapter_pdc.normalize.positions`
(the PDC House-position normalizer) and `usa_wa_adapter_legislature.normalize.members` (the
WSL SOAP member normalizer) because those were the first callers, so five `usa-wa-adapter-sos`
modules imported a peer adapter to key a seat.

Pure. No wire, no session.
"""

from __future__ import annotations

#: WA House positions (the only ones this cut resolves — ballot has Position 1 / 2 per LD).
_VALID_POSITIONS = {"1", "2"}


def district_number(district: str | None) -> int | None:
    """Parse a legislative-district string (e.g. ``"33"``, ``" 5 "``) to its LD number, or
    ``None`` for a blank/malformed value (no district → no seat). The single parse site — both
    the LD slug and the Senate seat's ``source_id`` derive from this."""
    if district is None:
        return None
    text = district.strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def ld_slug(district: str | None) -> str | None:
    """LD string → the local LD jurisdiction slug ``usa-wa-ld-<n>`` (unpadded, matching the
    synced PM jurisdictions), or ``None`` for a blank/malformed district."""
    number = district_number(district)
    return f"usa-wa-ld-{number}" if number is not None else None


def canonical_position(raw: object) -> str | None:
    """Map a raw House position (``"1"`` / ``"2"``, possibly int/padded) to the PM seat
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


def house_span_discriminator(ld_number: int, qualifier: str) -> str:
    """The tenure-span ``discriminator`` for a House Position seat (#79): ``ld-5-position-1``.

    Colon-free so the span ``source_id`` (``{member}:{kind}:{discriminator}:{start}``) stays a
    clean 4-part key — symmetric with the Senate seat span. Encoding ``(LD, position)`` means a
    redistricting LD renumber opens a new span (a genuinely different seat), which is the
    deliberate discriminator semantics :mod:`clearinghouse_domain_legislative.tenure_spans`
    documents."""
    position_digit = qualifier.rsplit(" ", 1)[-1]
    return f"ld-{ld_number}-position-{position_digit}"


def parse_house_span_discriminator(discriminator: str) -> tuple[int, str]:
    """Recover ``(ld_number, qualifier)`` from a House span discriminator (inverse of
    :func:`house_span_discriminator`) — the span-emit role resolver keys the seat Role on it."""
    _ld, ld_number, _position, position_digit = discriminator.split("-")
    return int(ld_number), f"Position {position_digit}"
