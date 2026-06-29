"""Tests for the frozen-seed tamper-evidence manifest convention (#54).

A checked-in seed's integrity is primarily its git commit SHA. The `.sha256` /
`.meta.json` sidecars exist for ingest *outside* git: a loader verifies the
sidecar against the seed bytes, then feeds the same digest into
FetchEvent.content_hash — unifying repo-seed and fetched-source under one baseline.
"""

import hashlib
import json

import pytest

from clearinghouse_core import seed_manifest


def test_digest_matches_runner_form():
    """seed_manifest.digest is the same raw 32 bytes the runner writes to content_hash."""
    content = b"<seed>committee data</seed>"
    assert seed_manifest.digest(content) == hashlib.sha256(content).digest()
    assert len(seed_manifest.digest(content)) == 32


def test_write_sidecars_emits_sha256sum_format_and_meta(tmp_path):
    """The .sha256 follows sha256sum's `<hex>  <name>` form; .meta.json carries metadata."""
    seed = tmp_path / "committee_seed_2023-24.xml"
    content = b"<seed/>"
    seed.write_bytes(content)

    manifest = seed_manifest.write_sidecars(seed, content, extra={"biennium": "2023-24"})

    sha_text = (tmp_path / "committee_seed_2023-24.xml.sha256").read_text()
    assert sha_text == f"{manifest.sha256}  committee_seed_2023-24.xml\n"

    meta = json.loads((tmp_path / "committee_seed_2023-24.xml.meta.json").read_text())
    assert meta["sha256"] == hashlib.sha256(content).hexdigest()
    assert meta["size_bytes"] == len(content)
    assert meta["biennium"] == "2023-24"


def test_verify_true_on_match_false_on_tamper(tmp_path):
    seed = tmp_path / "s.xml"
    content = b"original"
    seed.write_bytes(content)
    seed_manifest.write_sidecars(seed, content)

    assert seed_manifest.verify(seed, content) is True
    assert seed_manifest.verify(seed, b"tampered") is False


def test_verified_digest_returns_bytes_and_raises_on_mismatch(tmp_path):
    """verified_digest is the unifying seam: raw digest for content_hash, or raise."""
    seed = tmp_path / "s.xml"
    content = b"payload"
    seed.write_bytes(content)
    seed_manifest.write_sidecars(seed, content)

    assert seed_manifest.verified_digest(seed, content) == hashlib.sha256(content).digest()

    with pytest.raises(seed_manifest.SeedIntegrityError):
        seed_manifest.verified_digest(seed, b"different")


def test_read_sha256_sidecar_parses_hex(tmp_path):
    seed = tmp_path / "s.xml"
    content = b"abc"
    seed.write_bytes(content)
    seed_manifest.write_sidecars(seed, content)

    assert seed_manifest.read_sha256_sidecar(seed) == hashlib.sha256(content).hexdigest()
