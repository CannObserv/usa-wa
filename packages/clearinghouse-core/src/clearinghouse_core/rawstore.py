"""Raw-tier file store (#304): content-addressed objects + per-run manifests.

The file analog of the Postgres provenance pair, feeding the #302 pipeline:

- **objects** — pristine wire bodies stored once under their sha256
  (``<root>/<source>/objects/<sha[:2]>/<sha>``), immutable, deduplicated. The
  filename *is* the integrity baseline, as ``FetchEvent.content_hash`` was.
- **run manifests** — every fetch of a run recorded in one JSON document
  (``<root>/<source>/runs/<run_id>.json``), written atomically on close, so a
  crashed harvest leaves stray objects at worst, never a partial ledger.
- **latest.json** — a rebuildable index (resource_id → newest ok fetch) for
  freshness/TTL decisions; only successful fetches advance it.

skip_unchanged parity: a byte-identical re-fetch is recorded in the manifest
(the ledger sees the pull) while the object is stored once. Retention: the
tracked sources are archival (#54) — nothing here deletes; manifests are small
and kept indefinitely.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from clearinghouse_core.logging import get_logger

logger = get_logger(__name__)

RAW_ROOT_ENV = "USA_WA_RAW_ROOT"
_DEFAULT_ROOT = "raw"


def get_raw_root() -> Path:
    """The raw store root: ``USA_WA_RAW_ROOT``, defaulting to ``raw/`` under the cwd."""
    return Path(os.environ.get(RAW_ROOT_ENV, _DEFAULT_ROOT))


def _utc_iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass(frozen=True)
class RawFetch:
    """One recorded fetch: the manifest entry, plus whether bytes were newly stored."""

    resource_id: str
    sha256: str | None
    bytes: int
    fetched_at: str
    url: str
    status: str
    content_type: str | None
    newly_stored: bool


@dataclass
class VerifyResult:
    """Outcome of a :func:`verify_store` pass over manifest-referenced objects."""

    objects_verified: int = 0
    bytes_verified: int = 0
    mismatched: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    exhausted_budget: bool = False
    last_key: tuple[str, str] | None = None

    @property
    def clean(self) -> bool:
        """True when the pass found no mismatched and no missing objects."""
        return not self.mismatched and not self.missing


@dataclass(frozen=True)
class RecordOutcome:
    """What one :func:`record_fetch` did: exactly one of the three shapes —
    a payload landed, the resource was TTL-fresh, or the fetch errored."""

    payload: Any | None
    skipped_fresh: bool
    error: bool


class RawStore:
    """One source's slice of the raw store. Cheap to construct; directories on demand."""

    def __init__(self, root: Path | str, source: str) -> None:
        self.root = Path(root)
        self.source = source
        self.source_dir = self.root / source
        self.objects_dir = self.source_dir / "objects"
        self.runs_dir = self.source_dir / "runs"

    def put_object(self, body: bytes) -> tuple[str, bool]:
        """Store ``body`` under its sha256. Returns ``(sha256, newly_stored)``.

        Deliberately trusts an existing file's name over its bytes: a corrupted
        object on disk is NOT repaired by a later genuine re-fetch — the
        integrity sweep detects the mismatch and repair is a manual act, so a
        tampered store never self-launders under routine harvesting.
        """
        sha = hashlib.sha256(body).hexdigest()
        path = self.object_path(sha)
        if path.exists():
            return sha, False
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
        tmp.write_bytes(body)
        tmp.replace(path)
        return sha, True

    def object_path(self, sha256: str) -> Path:
        """Sharded on-disk path of the object stored under ``sha256``."""
        return self.objects_dir / sha256[:2] / sha256

    def open_run(self, run_id: str | None = None) -> RawRun:
        """Start recording one harvest run (buffered; visible on ``close``)."""
        return RawRun(self, run_id=run_id)

    def manifest_paths(self) -> list[Path]:
        """Every closed run manifest of this source, oldest first."""
        if not self.runs_dir.is_dir():
            return []
        return sorted(self.runs_dir.glob("*.json"))

    def latest(self) -> dict[str, dict]:
        """The resource_id → newest-ok-fetch index. Empty when nothing succeeded yet."""
        path = self.source_dir / "latest.json"
        if not path.is_file():
            return {}
        return json.loads(path.read_text())

    def is_fresh(self, resource_id: str, *, ttl_days: float, now: datetime | None = None) -> bool:
        """True when the newest ok fetch of ``resource_id`` is within ``ttl_days``."""
        entry = self.latest().get(resource_id)
        if entry is None:
            return False
        fetched = datetime.strptime(entry["fetched_at"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=UTC
        )
        now = now or datetime.now(UTC)
        return now - fetched <= timedelta(days=ttl_days)

    def _update_latest(self, entries: list[dict], run_id: str) -> None:
        # Read-modify-write under an exclusive per-source flock: two runs
        # closing concurrently on one source (live harvest + #305 export, the
        # overlap raw_export blesses) must not clobber each other's entries.
        self.source_dir.mkdir(parents=True, exist_ok=True)
        with open(self.source_dir / ".latest.lock", "w") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                self._update_latest_locked(entries, run_id)
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)

    def _update_latest_locked(self, entries: list[dict], run_id: str) -> None:
        path = self.source_dir / "latest.json"
        latest = self.latest()
        for entry in entries:
            if entry["status"] != "ok":
                continue
            current = latest.get(entry["resource_id"])
            if current is not None and current["fetched_at"] >= entry["fetched_at"]:
                # A later run recording an older fetch (the #305 corpus export)
                # must not regress the index past the live harvest.
                continue
            latest[entry["resource_id"]] = {
                "sha256": entry["sha256"],
                "fetched_at": entry["fetched_at"],
                "run_id": run_id,
            }
        tmp = path.with_name(f".latest.{secrets.token_hex(4)}.tmp")
        tmp.write_text(json.dumps(latest, indent=2, sort_keys=True) + "\n")
        tmp.replace(path)


class RawRun:
    """Recorder for one harvest run. Entries buffer in memory; :meth:`close` is the
    single write that makes the run visible (temp file + rename)."""

    def __init__(self, store: RawStore, *, run_id: str | None = None) -> None:
        self.store = store
        self.run_id = run_id or (
            datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + f"-{secrets.token_hex(3)}"
        )
        self.started_at = _utc_iso(datetime.now(UTC))
        self._entries: list[dict] = []

    @property
    def manifest_path(self) -> Path:
        return self.store.runs_dir / f"{self.run_id}.json"

    def record(
        self,
        resource_id: str,
        body: bytes | None,
        *,
        url: str,
        status: str = "ok",
        content_type: str | None = None,
        fetched_at: datetime | None = None,
        extra: dict | None = None,
    ) -> RawFetch:
        """Record one fetch; stores ``body`` when present. ``status`` mirrors the
        FetchEvent vocabulary (``ok`` | ``err`` | ``skipped``); ``extra`` merges
        additional keys into the manifest entry (e.g. the #305 export's
        ``unbaselined`` marker)."""
        sha: str | None = None
        newly = False
        size = 0
        if body is not None:
            sha, newly = self.store.put_object(body)
            size = len(body)
        fetch = RawFetch(
            resource_id=resource_id,
            sha256=sha,
            bytes=size,
            fetched_at=_utc_iso(fetched_at or datetime.now(UTC)),
            url=url,
            status=status,
            content_type=content_type,
            newly_stored=newly,
        )
        self._entries.append(
            {
                "resource_id": fetch.resource_id,
                "sha256": fetch.sha256,
                "bytes": fetch.bytes,
                "fetched_at": fetch.fetched_at,
                "url": fetch.url,
                "status": fetch.status,
                "content_type": fetch.content_type,
                **(extra or {}),
            }
        )
        return fetch

    def close(self) -> Path:
        """Write the manifest atomically and update ``latest.json``."""
        self.store.runs_dir.mkdir(parents=True, exist_ok=True)
        document = {
            "source": self.store.source,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "closed_at": _utc_iso(datetime.now(UTC)),
            "entries": self._entries,
        }
        path = self.manifest_path
        tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
        tmp.write_text(json.dumps(document, indent=2) + "\n")
        tmp.replace(path)
        self.store._update_latest(self._entries, self.run_id)
        return path


async def record_fetch(
    run: RawRun,
    store: RawStore,
    resource_id: str,
    url: str,
    fetcher: Callable[[], Awaitable[Any]],
    counters: dict[str, int],
    ttl_days: float,
    *,
    log_event: str,
) -> RecordOutcome:
    """The shared Phase-A loop body: TTL fresh-skip, fetch, per-resource error
    containment, dedup-aware record. One implementation for every harvester —
    the three hand-rolled copies diverged (#302 CR).

    ``counters`` must carry ``fetched``/``unchanged``/``skipped_fresh``/``errors``.
    ``fetcher`` returns an adapter fetch object exposing ``.wire`` (bytes) and
    optionally ``.content_type``. An error is contained: recorded ``err``,
    logged under ``log_event``, counted — never raised.
    """
    if ttl_days and store.is_fresh(resource_id, ttl_days=ttl_days):
        run.record(resource_id, None, url=url, status="skipped")
        counters["skipped_fresh"] += 1
        return RecordOutcome(payload=None, skipped_fresh=True, error=False)
    try:
        payload = await fetcher()
    except Exception:
        logger.exception(log_event, extra={"resource_id": resource_id})
        run.record(resource_id, None, url=url, status="err")
        counters["errors"] += 1
        return RecordOutcome(payload=None, skipped_fresh=False, error=True)
    recorded = run.record(
        resource_id,
        payload.wire,
        url=url,
        content_type=getattr(payload, "content_type", None),
    )
    counters["fetched"] += 1
    if not recorded.newly_stored:
        counters["unchanged"] += 1
    return RecordOutcome(payload=payload, skipped_fresh=False, error=False)


def verify_store(
    root: Path | str,
    source: str | None = None,
    *,
    byte_budget: int | None = None,
    after: tuple[str, str] | None = None,
) -> VerifyResult:
    """Re-hash every manifest-referenced object against its name.

    A mismatch is corruption or tamper (the #54 posture); a missing object is a
    manifest pointing at bytes that are gone. ``byte_budget`` bounds one pass
    (the sweep's rolling-slice idiom); ``exhausted_budget`` says the pass was
    partial, not that the store is clean. ``after`` resumes strictly past a
    ``(source, sha)`` key from an earlier pass's ``last_key`` — the caller owns
    persisting the cursor and wrapping at the tail.
    """
    root = Path(root)
    sources = [source] if source else sorted(p.name for p in root.iterdir() if p.is_dir())
    result = VerifyResult()
    expected: dict[tuple[str, str], int] = {}
    for src in sources:
        store = RawStore(root, src)
        for manifest_path in store.manifest_paths():
            manifest = json.loads(manifest_path.read_text())
            for entry in manifest["entries"]:
                if entry["sha256"]:
                    expected[(src, entry["sha256"])] = entry["bytes"]
    for (src, sha), _size in sorted(expected.items()):
        if after is not None and (src, sha) <= after:
            continue
        if byte_budget is not None and result.bytes_verified >= byte_budget:
            result.exhausted_budget = True
            break
        result.last_key = (src, sha)
        path = RawStore(root, src).object_path(sha)
        if not path.is_file():
            result.missing.append(sha)
            continue
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != sha:
            result.mismatched.append(sha)
        result.objects_verified += 1
        result.bytes_verified += len(body)
    return result
