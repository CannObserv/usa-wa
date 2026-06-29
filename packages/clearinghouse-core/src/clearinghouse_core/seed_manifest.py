"""Frozen-seed tamper-evidence manifest convention (#54).

For a checked-in seed file (e.g. #39's harvested committee-meeting seed), the
primary tamper evidence is git itself: blobs are content-addressed and the commit
SHA pins the reviewed bytes. So a `.sha256` sidecar is *redundant inside the
repo*. It earns its place only when the seed is consumed **outside** git — loaded
into the DB, shipped to an Archiver — where a loader must confirm the bytes it
ingests match what was reviewed. The loader calls :func:`verified_digest`, which
checks the sidecar and returns the raw digest to write straight into
``FetchEvent.content_hash`` — unifying repo-seed and fetched-source under the one
integrity baseline (item 1).

Canonical form: the digest is over the seed's **uncompressed** content — the same
bytes that get ingested and stored in ``RawPayload.body``. If a seed is compressed
in-repo (large XML), the caller passes the decompressed bytes as ``content`` while
``seed_path`` names the on-disk (possibly compressed) file; the sidecar still
attests to the canonical content, not the compression envelope.

The `.sha256` file uses GNU ``sha256sum`` format (``<hex>  <name>\\n``) so it is
also verifiable from the shell (``sha256sum -c seed.xml.sha256``) and matches the
spike harvester's output. The `.meta.json` carries human-reviewable metadata
(hash, size, plus caller ``extra`` such as biennium / counts) so a seed update is
reviewable without decompressing the blob.
"""

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

SHA256_SIDECAR_SUFFIX = ".sha256"
META_SIDECAR_SUFFIX = ".meta.json"


class SeedIntegrityError(Exception):
    """A seed's content does not match its recorded sidecar digest."""


@dataclass(frozen=True)
class SeedManifest:
    """The integrity facts written alongside a seed file."""

    sha256: str
    size_bytes: int
    extra: dict = field(default_factory=dict)


def digest(content: bytes) -> bytes:
    """Raw 32-byte sha256 of ``content`` — the exact form the runner writes to
    ``FetchEvent.content_hash``, so a seed-ingest path can reuse it directly."""
    return hashlib.sha256(content).digest()


def sha256_hex(content: bytes) -> str:
    """Hex sha256 of ``content`` (the sidecar / meta representation)."""
    return hashlib.sha256(content).hexdigest()


def build_manifest(content: bytes, *, extra: dict | None = None) -> SeedManifest:
    """Compute the integrity facts for ``content`` without writing anything."""
    return SeedManifest(sha256=sha256_hex(content), size_bytes=len(content), extra=extra or {})


def _sidecar(seed_path: Path, suffix: str) -> Path:
    return seed_path.with_name(seed_path.name + suffix)


def write_sidecars(seed_path: Path, content: bytes, *, extra: dict | None = None) -> SeedManifest:
    """Write ``<seed>.sha256`` and ``<seed>.meta.json`` for ``content``.

    ``content`` is the canonical (uncompressed) seed bytes; ``seed_path`` is the
    on-disk file the sidecars sit beside. Returns the :class:`SeedManifest`.
    """
    manifest = build_manifest(content, extra=extra)
    _sidecar(seed_path, SHA256_SIDECAR_SUFFIX).write_text(f"{manifest.sha256}  {seed_path.name}\n")
    _sidecar(seed_path, META_SIDECAR_SUFFIX).write_text(
        json.dumps(
            {"sha256": manifest.sha256, "size_bytes": manifest.size_bytes, **manifest.extra},
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    return manifest


def read_sha256_sidecar(seed_path: Path) -> str:
    """Return the hex digest recorded in ``<seed>.sha256`` (sha256sum format)."""
    text = _sidecar(seed_path, SHA256_SIDECAR_SUFFIX).read_text()
    return text.split()[0]


def verify(seed_path: Path, content: bytes) -> bool:
    """True iff ``content`` hashes to the digest recorded in the `.sha256` sidecar."""
    return sha256_hex(content) == read_sha256_sidecar(seed_path)


def verified_digest(seed_path: Path, content: bytes) -> bytes:
    """Verify ``content`` against its sidecar and return the raw digest.

    The ingest seam: a seed loader calls this, gets the 32-byte digest to write
    into ``FetchEvent.content_hash``, and is guaranteed the bytes match the
    reviewed seed. Raises :class:`SeedIntegrityError` on any mismatch — fail
    closed rather than ingest unverified bytes.
    """
    if not verify(seed_path, content):
        raise SeedIntegrityError(
            f"{seed_path.name}: content sha256 does not match recorded sidecar digest"
        )
    return digest(content)
