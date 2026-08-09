"""Fitness tests for the ``/api/v1`` read surface (#184).

Three properties that are cheap to check and expensive to lose:

1. **Read-only means read-only.** The API runs as the *app* role and the provenance
   tables carry ``REVOKE UPDATE`` (#54), so a route that mutates fails at the
   database, in production. Asserting the method set is the earliest place to catch
   a ``POST`` added to a router whose whole premise is that it has none.
2. **Every route declares a response model.** An undeclared return type publishes an
   empty schema, which makes the OpenAPI document — the actual contract — useless.
3. **The OpenAPI document generates.** A response model that Pydantic can build but
   ``model_json_schema`` cannot render is a runtime 500 on ``/openapi.json``, not a
   test failure, unless something asks.

No database: these read the route table off the app object.
"""

from fastapi.routing import APIRoute

from usa_wa_api.api.main import app
from usa_wa_api.api.v1 import API_V1_PREFIX

V1_ROUTES = [r for r in app.routes if isinstance(r, APIRoute) and r.path.startswith(API_V1_PREFIX)]


def test_the_v1_surface_is_mounted():
    assert V1_ROUTES, "no /api/v1 routes registered"


def test_every_v1_route_is_read_only():
    offenders = sorted(
        f"{sorted(r.methods)} {r.path}" for r in V1_ROUTES if not r.methods <= {"GET", "HEAD"}
    )
    assert offenders == [], f"/api/v1 is a read surface; mutating routes found: {offenders}"


def test_every_v1_route_declares_a_response_model():
    undeclared = sorted(r.path for r in V1_ROUTES if r.response_model is None)
    assert undeclared == [], f"routes without a response_model: {undeclared}"


def test_openapi_schema_generates_and_includes_the_v1_paths():
    schema = app.openapi()
    paths = schema["paths"]
    for route in V1_ROUTES:
        assert route.path in paths, f"{route.path} missing from the OpenAPI document"
        assert set(paths[route.path]) <= {"get"}, f"{route.path} publishes a non-GET operation"


def test_the_unversioned_probes_stay_where_operators_and_systemd_expect_them():
    """``/health``, ``/ready`` and ``/health/sync`` predate the versioned surface and
    are probe contracts, not product API. Versioning them would break the deployment."""
    paths = {r.path for r in app.routes if isinstance(r, APIRoute)}
    assert {"/health", "/ready", "/health/sync"} <= paths
