"""The raw-store half of the #434 backup: a create-only mirror of the #304 store.

The raw store is the input every dataset is rebuilt from, and it is not
re-harvestable — sources drift and disappear, which is why it is archival (#54).
Its shape suits a create-only bucket exactly: objects are named by their sha256 and
never rewritten, and a run manifest lands once, by rename, after its objects.

- **What is mirrored:** ``<source>/objects/<aa>/<sha256>`` and
  ``<source>/runs/<run_id>.json``, under ``raw/`` in the bucket at the same relative
  path. Not ``latest.json`` — a mutable index no create-only object could follow,
  which a restore rebuilds from the manifests (``RawStore.rebuild_latest``) — and
  not the dot-files: temp files mid-rename, locks, the sweep's and the export's
  cursors.
- **What is uploaded:** each night, whatever the bucket does not already list. An
  object is hashed first and refused if its bytes no longer match its name: that is
  the integrity sweep's finding, and copying it would launder it.
- **No host in the path.** The objects are content-addressed and the run ids carry
  a random suffix, so one mirror serves any host; a new host's first run finds
  everything already there.
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from google.api_core.exceptions import NotFound

from clearinghouse_core.rawstore import RawStore
from usa_wa_api.backup.gcs import (
    LIST_TIMEOUT_SECONDS,
    UPLOAD_TIMEOUT_SECONDS,
    BackupError,
    create_object,
    list_names,
    sha256_file,
)

RAW_PREFIX = "raw"

_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_RE = re.compile(r"^[a-z0-9_]+$")
_RUN_RE = re.compile(r"^[A-Za-z0-9_-]+\.json$")


@dataclass
class MirrorResult:
    """One mirror pass, counted."""

    local: int = 0
    present: int = 0
    uploaded: int = 0
    unchanged: int = 0
    planned: int = 0
    remote_only: int = 0
    mismatched: list[str] = field(default_factory=list)


@dataclass
class FetchResult:
    """One fetch of the mirror back to disk, counted."""

    fetched: int = 0
    bytes: int = 0
    latest_entries: dict[str, int] = field(default_factory=dict)


def _is_object(parts: tuple[str, ...]) -> bool:
    """``<source>/objects/<aa>/<sha256>``."""
    return (
        len(parts) == 4
        and parts[1] == "objects"
        and bool(_SHA_RE.match(parts[3]))
        and parts[2] == parts[3][:2]
    )


def _is_manifest(parts: tuple[str, ...]) -> bool:
    """``<source>/runs/<run_id>.json``."""
    return len(parts) == 3 and parts[1] == "runs" and bool(_RUN_RE.match(parts[2]))


def _mirrored(parts: tuple[str, ...]) -> bool:
    return (
        bool(parts)
        and bool(_SOURCE_RE.match(parts[0]))
        and (_is_object(parts) or _is_manifest(parts))
    )


def local_inventory(root: Path) -> dict[str, Path]:
    """Every mirrorable file under ``root``, by the object name it is mirrored to."""
    if not root.is_dir():
        return {}
    inventory: dict[str, Path] = {}
    for source in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        for sub in ("objects", "runs"):
            base = source / sub
            if not base.is_dir():
                continue
            for path in sorted(base.rglob("*")):
                if not path.is_file() or path.is_symlink():
                    continue
                parts = path.relative_to(root).parts
                if _mirrored(parts):
                    inventory["/".join((RAW_PREFIX, *parts))] = path
    return inventory


def mirror(
    client: Any, bucket: str, root: Path, *, host: str, dry_run: bool = False
) -> MirrorResult:
    """Upload every local file the bucket does not list yet; never delete, never
    overwrite. ``dry_run`` hashes and plans but uploads nothing."""
    result = MirrorResult()
    local = local_inventory(root)
    remote = list_names(client, bucket, f"{RAW_PREFIX}/")
    result.local = len(local)
    result.remote_only = len(remote - local.keys())
    for key, path in local.items():
        if key in remote:
            result.present += 1
            continue
        digest = sha256_file(path)
        if "/objects/" in key and digest != path.name:
            result.mismatched.append(key)
            continue
        if dry_run:
            result.planned += 1
            continue
        content_type = "application/json" if key.endswith(".json") else "application/octet-stream"
        outcome = create_object(
            client,
            bucket,
            key,
            path,
            sha256=digest,
            metadata={"source_host": host},
            content_type=content_type,
        )
        setattr(result, outcome, getattr(result, outcome) + 1)
    return result


def private_dir(dest: Path) -> None:
    """Create ``dest`` 0700, or accept an existing one only if it is this user's own,
    a real directory, and closed to everyone else.

    A restore runs as root and writes what it fetched: a directory another user
    made first would let them read it, or plant a link for root to write through.
    """
    dest.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = dest.lstat()
    if not stat.S_ISDIR(st.st_mode):
        raise BackupError(f"{dest} is not a directory; refusing it")
    if st.st_uid != os.geteuid():
        raise BackupError(f"{dest} is owned by uid {st.st_uid}, not this user; refusing it")
    if st.st_mode & 0o077:
        raise BackupError(
            f"{dest} is not private (mode {stat.S_IMODE(st.st_mode):o}); "
            "use a new directory, or chmod 700 it"
        )


def _write_new(path: Path, blob: Any) -> None:
    """Download ``blob`` to ``path``, created ``O_EXCL | O_NOFOLLOW`` at 0600."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            blob.download_to_file(handle, timeout=UPLOAD_TIMEOUT_SECONDS)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def fetch_mirror(client: Any, bucket: str, dest: Path) -> FetchResult:
    """Bring the whole mirror back into ``dest``, an empty private directory, and
    rebuild each source's ``latest.json``.

    Every object must hash to its name and every manifest to its recorded sha256;
    the first that does not stops the fetch, since a store restored around a bad
    object is a store whose integrity sweep fails the first night.
    """
    private_dir(dest)
    if any(dest.iterdir()):
        raise BackupError(f"{dest} is not empty; restore the raw store into a new directory")
    result = FetchResult()
    sources: set[str] = set()
    blobs = client.list_blobs(bucket, prefix=f"{RAW_PREFIX}/", timeout=LIST_TIMEOUT_SECONDS)
    for blob in blobs:
        parts = PurePosixPath(blob.name).parts[1:]
        if not _mirrored(tuple(parts)):
            raise BackupError(f"{blob.name} is outside the raw mirror's layout; refusing it")
        path = dest.joinpath(*parts)
        try:
            _write_new(path, blob)
        except NotFound as exc:
            raise BackupError(f"gs://{bucket}/{blob.name} vanished mid-fetch") from exc
        digest = sha256_file(path)
        if _is_object(tuple(parts)) and digest != parts[-1]:
            raise BackupError(f"{blob.name} does not hash to its name ({digest})")
        recorded = (blob.metadata or {}).get("sha256")
        if recorded != digest:
            raise BackupError(f"{blob.name}: sha256 {digest} is not the recorded {recorded}")
        sources.add(parts[0])
        result.fetched += 1
        result.bytes += path.stat().st_size
    if not result.fetched:
        raise BackupError(f"no raw objects under gs://{bucket}/{RAW_PREFIX}/")
    for source in sorted(sources):
        result.latest_entries[source] = RawStore(dest, source).rebuild_latest()
    return result
