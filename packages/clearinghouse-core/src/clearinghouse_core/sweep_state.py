"""Integrity-sweep cursor state (#55).

The provenance integrity sweep (:mod:`clearinghouse_core.integrity`) walks the
:class:`~clearinghouse_core.provenance.RawPayload` archive a bounded slice at a
time and resumes across runs, so per-run cost stays flat as the #39 archival
docket volume grows. The resume point is a single ULID watermark: the highest
``RawPayload.id`` verified last run. ``RawPayload.id`` is a ULID (native
``uuid``, big-endian, time-first), so ``id > cursor`` walks the archive in the
same order the sweep already uses.

The cursor can't live on :class:`RawPayload` itself: #54 ``REVOKE UPDATE`` makes
the payload tables physically append-only for the app role, so an in-place
``verified_at`` stamp is impossible. It lives here instead — one row, keyed by
``scope``, mutable under the app role's DML grant on this (non-provenance) table.
"""

from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from ulid import ULID as _ULID

from clearinghouse_core.db.ulid import ULID
from clearinghouse_core.models import Base, TimestampMixin

SCHEMA = "clearinghouse_core"

SWEEP_SCOPE = "raw_payload"
"""The single logical stream swept today — the RawPayload archive."""


def _new_ulid() -> _ULID:
    """Default factory for the ULID PK column."""
    return _ULID()


class IntegritySweepState(Base, TimestampMixin):
    """Rolling-cursor watermark for one integrity-sweep stream (#55).

    ``cursor`` is the ULID of the highest ``RawPayload.id`` verified in the last
    run — same type as the id it tracks — or ``NULL`` to start the next run from
    the beginning of the archive (a fresh coverage cycle). One row per ``scope``;
    ``scope`` is a stable label (``"raw_payload"`` today), leaving room for a
    second stream without a schema change.
    """

    __tablename__ = "integrity_sweep_state"
    __table_args__ = (
        UniqueConstraint("scope", name="uq_integrity_sweep_state_scope"),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    cursor: Mapped[_ULID | None] = mapped_column(ULID(), nullable=True)
