"""The roster edition the code knows, and the check that fetched bytes are that edition (#421).

Pure, so both archive tiers shared one definition: the raw-store harvest
(:mod:`usa_wa_adapter_legislature.roster_pdf.raw_harvest`) and the Postgres adapter #412 PR F
deleted. The raw harvest may not import that adapter (#412 PR D's contract), so the check
lives here rather than in ``fetch_one``.
"""

from __future__ import annotations

from clearinghouse_core.logging import get_logger
from usa_wa_adapter_legislature.roster_pdf.extraction import extract_revision_date

logger = get_logger(__name__)

#: The revision shipped with this source. Override when a newer edition is published — and bump
#: it on ``main`` once that edition is archived: the monthly re-check (#237) compares against it,
#: so it keeps alerting until this names the edition the Legislature currently publishes.
DEFAULT_REVISION = "2025-06-05"


class RosterRevisionMismatch(ValueError):
    """The fetched document stamps a different ``Revision Date`` than the key it would archive to.

    Not an outage and not a retry: a **new edition has been published**. Archiving it under the
    requested key would mislabel the bytes, and every citation minted from them would name an
    edition that never attested the fact. Re-run with the new ``--revision``.
    """


def verify_edition(wire: bytes, revision: str, *, url: str) -> str | None:
    """Raise :class:`RosterRevisionMismatch` unless ``wire`` stamps ``revision``; return the stamp.

    A stamp we cannot read is a warning here, not a refusal — only a *disagreement* raises
    (CR findings 1 and 8 on #225). ``None`` hands the caller the decision: the raw harvest
    exits 4 on it (#421 CR 4, CR 6) — the stamp is its only stale-edition guard and the
    monthly re-check's only view of a new edition.
    """
    stamped = extract_revision_date(wire)
    if stamped is None:
        logger.warning("roster_revision_unreadable", extra={"expected": revision, "url": url})
    elif stamped != revision:
        raise RosterRevisionMismatch(
            f"document stamps Revision Date {stamped}, not {revision} — a new edition is "
            f"published; re-run with --revision {stamped}"
        )
    return stamped
