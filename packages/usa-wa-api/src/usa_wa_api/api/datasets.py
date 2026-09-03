"""The published-dataset surface (#311): static files + the publication probe.

- ``GET /datasets/{path}`` serves the publisher's output tree
  (``catalog.json``, ``<name>/<version>/data.csv|datapackage.json``) straight
  off ``USA_WA_DATASETS_ROOT`` — resolved per request so tests and redeploys
  need no app rebuild. Traversal-guarded; a missing root or file is a plain
  404 (an unpublished box is not an error).
- ``GET /health/datasets`` is the pipeline's ops probe — the successor to
  ``/health/sync`` as "is the nightly chain moving": per-dataset latest
  version, rows, and age; 200 with ``published: false`` before the first
  publish (absence is the finding, the #180 posture).
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(tags=["datasets"])

DATASETS_ROOT_ENV = "USA_WA_DATASETS_ROOT"
_DEFAULT_ROOT = "data/datasets"

_MEDIA_TYPES = {".json": "application/json", ".csv": "text/csv"}


def _root() -> Path:
    return Path(os.environ.get(DATASETS_ROOT_ENV, _DEFAULT_ROOT))


@router.get("/health/datasets")
def health_datasets() -> dict:
    """Publication health: catalog age + per-dataset version/rows/age.

    Plain ``def`` (#302 CR): the handler reads the catalog off disk, and a
    sync handler runs in Starlette's threadpool instead of blocking the loop.
    """
    catalog_path = _root() / "catalog.json"
    if not catalog_path.is_file():
        return {"published": False, "datasets": []}
    catalog = json.loads(catalog_path.read_text())
    now = datetime.now(UTC)

    def age(stamp: str) -> float:
        generated = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
        return (now - generated).total_seconds()

    return {
        "published": True,
        "generated_at": catalog["generated_at"],
        "age_seconds": age(catalog["generated_at"]),
        "datasets": [
            {
                "name": entry["name"],
                "tier": entry["tier"],
                "latest_version": entry["latest_version"],
                "rows": entry["rows"],
                "age_seconds": age(entry["generated_at"]),
            }
            for entry in catalog["datasets"]
        ],
    }


@router.get("/datasets/{path:path}")
def serve_dataset_file(path: str) -> FileResponse:
    """One published file, straight off disk. 404 for anything not published.

    Plain ``def`` for the same threadpool reason as the health probe; garbage
    input that makes ``Path`` itself choke (an embedded NUL) is a 404, not a
    500 (#302 CR).
    """
    root = _root().resolve()
    try:
        target = (root / path).resolve()
        published = target.is_relative_to(root) and target.is_file()
    except (ValueError, OSError):
        published = False
    if not published:
        raise HTTPException(status_code=404, detail="no such published file")
    return FileResponse(
        target, media_type=_MEDIA_TYPES.get(target.suffix, "application/octet-stream")
    )
