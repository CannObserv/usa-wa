"""The bucket-facing half of the #434 backup, shared with the restore.

Built on CannObserv/watcher's ``src/ops/backup.py`` (itself after broker#4), so the
properties carry over unchanged:

- **Create, never overwrite or delete.** ``if_generation_match=0`` in code, and
  ``objectCreator`` + ``objectViewer`` at IAM with no ``delete``: retention is the
  bucket's lifecycle rule, so a compromised host cannot erase its own history. A 412
  is ``unchanged`` only when the object's recorded sha256 is this file's — anything
  else is a name collision, and a failure.
- **The preflight lists, it does not ask ``exists()``.** The SDK swallows a missing
  bucket's 404 there and returns ``False`` (replicator#7 CR #1).
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from google.api_core.exceptions import NotFound, PreconditionFailed

BUCKET_ENV = "USA_WA_BACKUP_BUCKET"
#: The database dumps' per-host prefix; defaults to this host's name.
PREFIX_ENV = "USA_WA_BACKUP_PREFIX"
#: Where the GCS SDK finds its key. The unit points it at its credential copy.
KEY_PATH_ENV = "GOOGLE_APPLICATION_CREDENTIALS"
#: Set by systemd for a unit with ``LoadCredential=``.
CREDENTIALS_DIRECTORY_ENV = "CREDENTIALS_DIRECTORY"

LIST_TIMEOUT_SECONDS = 60.0
UPLOAD_TIMEOUT_SECONDS = 600.0


class BackupError(Exception):
    """Anything that means a backup was not shipped, or a restore not made."""


def iso(at: datetime) -> str:
    """ISO 8601, UTC, second precision, ``Z``."""
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    """The file's sha256, read in 1 MiB chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def misplaced_key(environ: Mapping[str, str]) -> str | None:
    """Why the GCS key path is not this run's credential copy; ``None`` when it is.

    Under the unit the path is ``%d/gcs``, the private copy systemd made. But an
    ``EnvironmentFile=`` beats ``Environment=``, so a stray line in
    ``/etc/usa-wa/backup.env`` aims the job at the root-only original, and the SDK
    then fails "Permission denied" — which reads as a reason to loosen the key's
    mode, the one thing the sandbox exists to prevent. Outside a unit (a test, a
    hand run) there is no copy to compare against.
    """
    directory = environ.get(CREDENTIALS_DIRECTORY_ENV, "").strip()
    if not directory:
        return None
    key = environ.get(KEY_PATH_ENV, "").strip()
    if key and Path(key).is_relative_to(directory):
        return None
    return (
        f"{KEY_PATH_ENV} is {key!r}, not this run's credential copy under {directory}: "
        "remove it from /etc/usa-wa/backup.env, which overrides the unit's own"
    )


def preflight(client: Any, bucket: str) -> None:
    """Prove the bucket is there and listable by this identity.

    A one-object listing, advanced: the listing is lazy, and a missing bucket raises
    only when it is iterated.
    """
    listing = client.list_blobs(bucket, max_results=1, timeout=LIST_TIMEOUT_SECONDS)
    try:
        next(iter(listing), None)
    except NotFound as exc:
        raise BackupError(f"bucket {bucket!r} not found, or not listable: {exc}") from exc


def list_names(client: Any, bucket: str, prefix: str) -> set[str]:
    """Every object name under ``prefix``."""
    return {
        blob.name for blob in client.list_blobs(bucket, prefix=prefix, timeout=LIST_TIMEOUT_SECONDS)
    }


def create_object(
    client: Any,
    bucket: str,
    key: str,
    path: Path,
    *,
    sha256: str,
    metadata: Mapping[str, str],
    content_type: str = "application/octet-stream",
) -> str:
    """Create ``key`` from ``path``; ``"uploaded"``, or ``"unchanged"`` if this very
    file is already there. The sha256 always rides along as metadata — it is what a
    restore checks the download against."""
    blob = client.bucket(bucket).blob(key)
    blob.metadata = {**metadata, "sha256": sha256}
    try:
        blob.upload_from_filename(
            str(path),
            content_type=content_type,
            if_generation_match=0,
            timeout=UPLOAD_TIMEOUT_SECONDS,
        )
    except PreconditionFailed:
        existing = client.bucket(bucket).get_blob(key, timeout=LIST_TIMEOUT_SECONDS)
        if existing is None:
            raise BackupError(f"{key} already exists, and could not be read back") from None
        if (existing.metadata or {}).get("sha256") == sha256:
            return "unchanged"
        raise BackupError(f"{key} already exists with different contents") from None
    return "uploaded"
