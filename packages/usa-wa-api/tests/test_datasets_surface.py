"""The /datasets surface + publication probe (#311)."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from usa_wa_api.api.main import app


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
def published(tmp_path, monkeypatch):
    root = tmp_path / "datasets"
    (root / "persons" / "v1").mkdir(parents=True)
    (root / "persons" / "v1" / "data.csv").write_text("entity_id,name_full\n01A,Dana\n")
    root.joinpath("catalog.json").write_text(
        json.dumps(
            {
                "checked_at": "2026-09-03T08:00:00.000000Z",
                "stale_after": "2026-09-04T08:45:00.000000Z",
                "datasets": [
                    {
                        "name": "persons",
                        "tier": "conformed",
                        "latest_version": "v1",
                        "rows": 1,
                        "bytes": 30,
                        "hash": "sha256:x",
                        "generated_at": "2026-09-03T08:00:00.000000Z",
                    }
                ],
            }
        )
    )
    monkeypatch.setenv("USA_WA_DATASETS_ROOT", str(root))
    return root


async def test_health_datasets_unpublished(client, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("USA_WA_DATASETS_ROOT", str(tmp_path / "nowhere"))
    response = await client.get("/health/datasets")
    assert response.status_code == 200
    assert response.json() == {"published": False, "datasets": []}


async def test_health_datasets_published(client, published) -> None:
    body = (await client.get("/health/datasets")).json()
    assert body["published"] is True
    [entry] = body["datasets"]
    assert entry["name"] == "persons"
    assert entry["latest_version"] == "v1"
    assert entry["age_seconds"] > 0


async def test_health_datasets_reports_the_heartbeat(client, published) -> None:
    """#386: the run time, its age, the deadline and the verdict — named apart from the mint age."""
    body = (await client.get("/health/datasets")).json()
    assert body["checked_at"] == "2026-09-03T08:00:00.000000Z"
    assert body["checked_age_seconds"] > 0
    assert body["stale_after"] == "2026-09-04T08:45:00.000000Z"
    assert body["stale"] is True
    # one `age_seconds` in the payload, and it is the dataset version's
    assert "age_seconds" not in body
    assert "generated_at" not in body


async def test_health_datasets_fresh_heartbeat_is_not_stale(client, published) -> None:
    catalog = json.loads((published / "catalog.json").read_text())
    now = datetime.now(UTC)
    catalog["checked_at"] = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    catalog["stale_after"] = (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    (published / "catalog.json").write_text(json.dumps(catalog))
    body = (await client.get("/health/datasets")).json()
    assert body["stale"] is False


async def test_health_datasets_reads_a_pre_386_catalog(client, published) -> None:
    """The catalog on disk predates this code until the next nightly run.

    Its top-level ``generated_at`` WAS the run time, so it is read as the
    heartbeat; it declares no threshold, so ``stale`` is ``null`` — "the catalog
    does not say", never a guessed ``False``."""
    catalog = json.loads((published / "catalog.json").read_text())
    catalog["generated_at"] = catalog.pop("checked_at")
    del catalog["stale_after"]
    (published / "catalog.json").write_text(json.dumps(catalog))
    body = (await client.get("/health/datasets")).json()
    assert body["checked_at"] == "2026-09-03T08:00:00.000000Z"
    assert body["stale_after"] is None
    assert body["stale"] is None


async def test_serves_catalog_and_files(client, published) -> None:
    catalog = await client.get("/datasets/catalog.json")
    assert catalog.status_code == 200
    assert catalog.headers["content-type"].startswith("application/json")
    data = await client.get("/datasets/persons/v1/data.csv")
    assert data.status_code == 200
    assert "Dana" in data.text


async def test_traversal_and_missing_are_404(client, published) -> None:
    # the percent-encoded form is the one that actually reaches the handler
    # un-collapsed — httpx normalizes literal dot segments client-side, so the
    # plain form never exercised the guard (#302 CR)
    assert (await client.get("/datasets/%2e%2e/%2e%2e/etc/passwd")).status_code == 404
    assert (await client.get("/datasets/../../etc/passwd")).status_code == 404
    assert (await client.get("/datasets/persons/v9/data.csv")).status_code == 404


async def test_symlink_escape_is_404(client, published, tmp_path) -> None:
    """resolve() must defeat a symlink pointing outside the published root."""
    outside = tmp_path / "secret.txt"
    outside.write_text("not published")
    (published / "persons" / "v1" / "link.txt").symlink_to(outside)
    assert (await client.get("/datasets/persons/v1/link.txt")).status_code == 404


async def test_garbage_path_is_404_not_500(client, published) -> None:
    """An embedded NUL makes Path.resolve() raise — that is a 404 (#302 CR)."""
    assert (await client.get("/datasets/%00")).status_code == 404
