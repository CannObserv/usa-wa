"""Fitness tests for the ``/api/v1`` read surface (#184).

Four properties that are cheap to check and expensive to lose:

1. **Read-only means read-only.** The API runs as the *app* role and the provenance
   tables carry ``REVOKE UPDATE`` (#54), so a route that mutates fails at the
   database, in production. Asserting the method set is the earliest place to catch
   a ``POST`` added to a router whose whole premise is that it has none.
2. **Every route declares a response model.** An undeclared return type publishes an
   empty schema, which makes the OpenAPI document — the actual contract — useless.
3. **The OpenAPI document generates.** A response model that Pydantic can build but
   ``model_json_schema`` cannot render is a runtime 500 on ``/openapi.json``, not a
   test failure, unless something asks.
4. **``docs/API.md`` states the routes that exist.** A published route inventory is
   the first thing a consumer reads and the last thing anyone remembers to update;
   the #167 ratchet applied to the route table, so a route added, renamed or removed
   fails the suite until the doc follows.

No database: these read the route table off the app object and parse one markdown file.
"""

import re
from pathlib import Path

from fastapi.routing import APIRoute

from usa_wa_api.api.main import app
from usa_wa_api.api.v1 import API_V1_PREFIX

V1_ROUTES = [r for r in app.routes if isinstance(r, APIRoute) and r.path.startswith(API_V1_PREFIX)]

API_DOC = Path(__file__).resolve().parents[3] / "docs" / "API.md"

#: A route-inventory row: ``| GET | `/api/v1/persons` | … |``. Anchored on the leading
#: pipe so a path quoted in the surrounding prose is not mistaken for a row — the
#: distinction ``test_docs_timer_drift`` had to learn the hard way.
DOC_ROW_RE = re.compile(r"^\|\s*(?P<method>[A-Z]+)\s*\|\s*`(?P<path>/[^`]*)`\s*\|")


def documented_routes() -> set[tuple[str, str]]:
    """``{(method, path)}`` from every route-inventory row in ``docs/API.md``."""
    return {
        (match["method"], match["path"])
        for line in API_DOC.read_text().splitlines()
        if (match := DOC_ROW_RE.match(line))
    }


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


# --- docs/API.md route inventory ---------------------------------------------


def test_the_docs_route_inventory_matches_the_app():
    """Every mounted route is documented, and every documented route exists.

    Both directions, because they fail differently: an undocumented route is
    invisible to consumers, while a documented-but-absent one is a 404 they were
    told to expect.
    """
    mounted = {
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods
        if method != "HEAD"
    }
    assert documented_routes() == mounted


def test_the_docs_row_parser_ignores_prose_and_headings():
    """A path quoted in prose is not an inventory row (the #167 lesson)."""
    assert DOC_ROW_RE.match("Live OpenAPI document: `GET /openapi.json`.") is None
    assert DOC_ROW_RE.match("| GET | `/api/v1/persons` | `Page` | People. |")


def test_the_docs_name_this_guard():
    """The doc points at the module that pins it, so a rename can't dangle."""
    assert Path(__file__).name in API_DOC.read_text()
