"""Boundary basis: where a tenure boundary's date came from (usa-wa#501 D6, #369).

Every tenure span carries a basis for each edge, set by the writer that put the
edge where it is. The basis is never read back off the date's geometry: a dated
event can land exactly on a biennium edge (11 of 533 do), and reading "on the
floor" as "quantized" is how the clip once mistook Woodall's real 1957-01-01
seating for a biennium default (#501 correction 5).

The basis says WHERE a date came from; the interval
(:attr:`~clearinghouse_domain_legislative.tenure_spans.TenureSpan.valid_from_latest`
and ``valid_to_earliest``) says HOW PRECISELY it is known. One closed vocabulary
serves both edges; a start is never ``open``.
"""

from __future__ import annotations

#: A source states this tenure's date: an operator event (a day), or a parsed
#: roster window (a month, a year, or an election opening a start).
BASIS_DATED = "dated"

#: A rule moved this edge onto ANOTHER tenure's boundary — counterpart clipping
#: (#360), a chamber move (#490), a departed split's return (#267). An inference,
#: not evidence (``39749e1``), so nothing may read it back as a stated date.
BASIS_DERIVED = "derived"

#: The biennium edge of the first or last attestation, and nothing narrower.
BASIS_OBSERVED = "observed"

#: The tenure is still running (``valid_to`` is null). An end basis only.
BASIS_OPEN = "open"

#: The closed vocabulary, in the order the datapackage declares it (#501 D8).
BOUNDARY_BASES = (BASIS_DATED, BASIS_DERIVED, BASIS_OBSERVED, BASIS_OPEN)

#: What a start may be: everything but ``open``.
START_BASES = (BASIS_DATED, BASIS_DERIVED, BASIS_OBSERVED)
