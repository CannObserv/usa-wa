"""``/api/v1`` — the read-only surface over the published data (#184, #313).

Two slices, one router:

* :mod:`~usa_wa_api.api.v1.ops` — the run ledger (#178), source coverage (#180)
  and provenance chains. The consumer the telemetry tables were missing.
* :mod:`~usa_wa_api.api.v1.products` — persons, organizations, roles and
  assignments (which *are* the tenure spans), served from the `serving` schema:
  the deployment's own projection of the datasets it publishes (#313).

**Versioned, and versioned from the first route.** Everything here is published
through OpenAPI the moment it ships, so the prefix exists to give a second shape
somewhere to live rather than because a second shape is planned.

**The pre-existing probes stay unversioned.** ``/health``, ``/ready`` and
``/health/sync`` are deployment contracts — systemd, the load balancer, the
operator's shell — not product API. Moving them under ``/api/v1`` would break
those consumers to buy consistency nobody asked for. ``/health/jobs`` is new and
is product surface, so it goes under the prefix; that the two now differ in path
is the honest reflection of their differing contracts.
"""

from fastapi import APIRouter

from usa_wa_api.api.v1 import ops, products

API_V1_PREFIX = "/api/v1"
"""Mount point for every route in this package. Nothing here is unversioned."""

router = APIRouter(prefix=API_V1_PREFIX)
router.include_router(ops.router)
router.include_router(products.router)

__all__ = ["API_V1_PREFIX", "router"]
