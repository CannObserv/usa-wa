"""How a tenure holds its role (usa-wa#501 D5, #362's Popolo/OCD ``Membership``).

A closed vocabulary, non-null on every tenure. Only a ``principal`` counts toward a
seat's capacity (``max_holders``), so a substitute serving under a principal on
leave is not a second holder of the seat — the clip never pairs them, and the
occupancy gate never counts them.

``ex_officio`` and ``acting`` are declared with no rows yet: #479 populates the
first (the Lt. Governor as President of the Senate).
"""

from __future__ import annotations

#: Holds the role in one's own right — every party and committee tenure, and every
#: seat tenure but the few below.
MEMBERSHIP_PRINCIPAL = "principal"

#: Serves in the seat while its principal is on leave (the military substitutes:
#: Kreidler 1991, Wyss 2005, M. Braun 2017).
MEMBERSHIP_SUBSTITUTE = "substitute"

#: Holds the role by virtue of another office.
MEMBERSHIP_EX_OFFICIO = "ex_officio"

#: Performs the role temporarily without holding it.
MEMBERSHIP_ACTING = "acting"

#: The closed vocabulary, in the order the datapackage declares it (#501 D8).
MEMBERSHIP_TYPES = (
    MEMBERSHIP_PRINCIPAL,
    MEMBERSHIP_SUBSTITUTE,
    MEMBERSHIP_EX_OFFICIO,
    MEMBERSHIP_ACTING,
)
