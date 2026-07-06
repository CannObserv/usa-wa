"""Role-type catalog mirror — local-side copy of Power Map's role_types catalog.

Power Map is the system of record for the ``role_types`` classifier (power-map#261
seeds ``state_representative`` / ``state_senator``; power-map#268 exposes the catalog
at ``GET /api/v1/role-types``). usa-wa mirrors
``{slug, display_name, expects_jurisdiction, requires_qualifier}`` locally so the
:class:`RoleDescriptor` can decide a Role observation's **shape at runtime** —
seat-mode (structural tuple) vs title-mode — from PM's own catalog rather than a
hardcoded slug map (retires the usa-wa#68 ``SEAT_ROLE_TYPE_SLUGS`` constant), and refuse a
positionless ``requires_qualifier`` seat (power-map#273) pre-flight.

The mirror is refreshed by the sidecar's catalog sync
(:func:`usa_wa_sync_powermap.role_type_catalog.sync_role_type_catalog`).
``expects_jurisdiction`` is PM's advisory hint that the office is normally attached with
a jurisdiction (power-map#271 renamed this field from ``is_seat``; PM does not *enforce*
it on ``resolve_role``), which is exactly the signal usa-wa needs to pick the seat
observation shape.
"""

from sqlalchemy import Boolean, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from ulid import ULID as _ULID

from clearinghouse_core.db.ulid import ULID
from clearinghouse_core.models import Base, TimestampMixin

SCHEMA = "canonical"


def _new_ulid() -> _ULID:
    return _ULID()


class RoleType(Base, TimestampMixin):
    """Local mirror of one PM ``role_types`` row (power-map#268).

    Natural key is ``slug`` (the stable value a producer sends as
    ``RoleObservationRequest.role_type`` and reads back on ``RoleDetail.role_type_slug``).
    ``pm_role_type_id`` anchors the PM row; ``expects_jurisdiction`` drives the
    seat-vs-title observation decision in the sync descriptor.
    """

    __tablename__ = "role_types"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_role_types_slug"),
        {"schema": SCHEMA},
    )

    id: Mapped[_ULID] = mapped_column(ULID(), primary_key=True, default=_new_ulid)
    # PM anchor (populated by the catalog sync). Null = not yet synced from PM.
    pm_role_type_id: Mapped[_ULID | None] = mapped_column(ULID(), nullable=True, index=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    # PM's advisory hint that this role type is normally attached with a jurisdiction
    # (power-map#271 rename of ``is_seat``) — the signal usa-wa uses to emit a seat-mode
    # observation (structural tuple, no title).
    expects_jurisdiction: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # PM's ENFORCED constraint (power-map#273): a districted-seat observation of this type
    # that arrives without a ``qualifier`` is REJECTED("qualifier_required") rather than
    # minting a positionless seat (#267). ``state_representative`` = True (per-position),
    # ``state_senator`` = False (one senator/LD, NULL qualifier valid). usa-wa mirrors it so
    # the descriptor can refuse a positionless requires_qualifier seat pre-flight (#71).
    # Unlike ``expects_jurisdiction`` (advisory), this is a hard PM constraint.
    requires_qualifier: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
