"""Fakes for the object-store client, shared by the #434 backup and restore tests.

Adapted from CannObserv/broker's ``tests/gcs_fakes.py``, and like it they owe their
fidelity to the SDK rather than to the tests (replicator#7 CR #1, #2): a fake that
raised where the real client returned ``False`` let a preflight look tested while
unable to see the one misconfiguration it existed for. Every method does what
``google-cloud-storage`` does; where the real behaviour surprises, it says so.

The calls this repo makes, and nothing it does not:

- ``Blob.upload_from_filename(..., if_generation_match=0)`` — a create, never a put;
  ``PreconditionFailed`` (412) when the object already exists.
- ``Client.list_blobs(bucket, prefix=, max_results=)`` — lazy: the request happens on
  iteration, and a missing bucket raises ``NotFound`` there, not at the call.
- ``Bucket.get_blob(name)`` — ``None`` for an absent object, not ``NotFound``.
- ``Blob.download_to_file(handle)`` — ``NotFound`` for an object deleted under it.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from google.api_core.exceptions import NotFound, PreconditionFailed

#: When the fake bucket "created" every object unless a test says otherwise.
CREATED = datetime(2026, 10, 1, 10, 30, tzinfo=UTC)


class FakeBlob:
    """Just enough ``google.cloud.storage.Blob`` to answer this repo's calls."""

    def __init__(self, bucket: FakeBucket, name: str) -> None:
        self._bucket = bucket
        self.name = name
        self.metadata: dict[str, str] | None = None
        self.size: int | None = None
        self.time_created: datetime | None = None

    def upload_from_filename(
        self,
        filename: str,
        content_type: str | None = None,
        if_generation_match: int | None = None,
        timeout: float | None = None,
    ) -> None:
        if if_generation_match == 0 and self.name in self._bucket.objects:
            # Evaluated against the object's generation before anything is written,
            # which is why an identity holding no storage.objects.delete still gets a
            # 412 here rather than a 403.
            raise PreconditionFailed("object already exists")
        self._bucket.put(self.name, Path(filename).read_bytes(), dict(self.metadata or {}))
        self._bucket.content_types[self.name] = content_type
        self._bucket.preconditions.append(if_generation_match)

    def download_to_file(self, handle: BinaryIO, timeout: float | None = None) -> None:
        if self.name not in self._bucket.objects:
            raise NotFound("no such object")
        handle.write(self._bucket.objects[self.name])


class FakeBucket:
    def __init__(self, name: str = "a-backup-bucket") -> None:
        self.name = name
        self.objects: dict[str, bytes] = {}
        self.metadata: dict[str, dict[str, str]] = {}
        self.created: dict[str, datetime] = {}
        self.content_types: dict[str, str | None] = {}
        self.preconditions: list[int | None] = []

    def put(
        self,
        name: str,
        body: bytes,
        metadata: dict[str, str] | None = None,
        created: datetime = CREATED,
    ) -> None:
        """Seed or store an object, as an upload would."""
        self.objects[name] = body
        self.metadata[name] = dict(metadata or {})
        self.created[name] = created

    def blob(self, name: str) -> FakeBlob:
        return FakeBlob(self, name)

    def get_blob(self, name: str, timeout: float | None = None) -> FakeBlob | None:
        if name not in self.objects:
            return None
        return self._described(name)

    def _described(self, name: str) -> FakeBlob:
        blob = FakeBlob(self, name)
        # A listing or a get returns the full resource, metadata included.
        blob.metadata = dict(self.metadata.get(name, {})) or None
        blob.size = len(self.objects[name])
        blob.time_created = self.created.get(name)
        return blob


class FakeClient:
    """A client over one bucket, with the listing the preflight probes through."""

    def __init__(self, bucket: FakeBucket | None = None, *, missing: bool = False) -> None:
        self._bucket = bucket if bucket is not None else FakeBucket()
        # "This bucket is not there" — the state a misspelled USA_WA_BACKUP_BUCKET
        # puts the job in, and the one the listing preflight exists to report.
        self._missing = missing
        self.listings: list[dict] = []

    def bucket(self, name: str) -> FakeBucket:
        assert name == self._bucket.name, f"unexpected bucket {name!r}"
        return self._bucket

    def list_blobs(
        self,
        bucket_or_name: str | FakeBucket,
        max_results: int | None = None,
        prefix: str | None = None,
        timeout: float | None = None,
    ) -> Iterator[FakeBlob]:
        """Lazy, like the real one — the request happens when it is iterated."""
        name = bucket_or_name if isinstance(bucket_or_name, str) else bucket_or_name.name
        assert name == self._bucket.name, f"unexpected bucket {name!r}"
        self.listings.append({"max_results": max_results, "prefix": prefix})

        def _iter() -> Iterator[FakeBlob]:
            if self._missing:
                raise NotFound(f"bucket {name} not found")
            names = sorted(k for k in self._bucket.objects if not prefix or k.startswith(prefix))
            if max_results is not None:
                names = names[:max_results]
            for key in names:
                yield self._bucket._described(key)

        return _iter()
