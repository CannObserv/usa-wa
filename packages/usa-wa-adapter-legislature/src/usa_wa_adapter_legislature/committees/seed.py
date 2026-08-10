"""Frozen Joint/`Other` committee seed (#39) — (de)serialization + default path.

The seed is the harvest's deliverable: a checked-in, reviewed snapshot of the durable
Joint/`Other` committee set, so a fresh deploy materializes them with **zero WSL
traffic** and dormant bodies (absent from any recent meeting window) survive. The
canonical bytes are deterministic — sorted keys, rows ordered by ``source_id`` — so the
``seed_manifest`` sidecars attest to a stable digest across re-harvests of an unchanged
cohort.

Only WSL-sourced identity fields live in the seed; parent linkage is **not** stored —
the whole class re-parents to the legislature anchor on ingest (Joint and Other alike),
and PM owns any finer org-tree curation.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_SEED_PATH = Path(__file__).parent / "data" / "joint_other_committees_seed.json"


@dataclass(frozen=True)
class SeedCommittee:
    """One durable Joint/`Other` committee, as frozen in the seed."""

    source_id: str
    name: str
    short_name: str | None
    acronym: str | None
    phone: str | None


def serialize_seed(committees: list[SeedCommittee], *, bienniums: list[str]) -> bytes:
    """Canonical UTF-8 seed bytes — deterministic for stable hashing.

    Rows are sorted by ``source_id`` and keys sorted, so an unchanged cohort always
    serializes byte-identically (and the manifest digest is stable). ``bienniums`` is
    the harvest range that produced the seed, recorded for review."""
    rows = sorted((asdict(c) for c in committees), key=lambda r: r["source_id"])
    doc = {"bienniums": list(bienniums), "committees": rows}
    return (json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def deserialize_seed(content: bytes) -> list[SeedCommittee]:
    """Parse seed bytes back into :class:`SeedCommittee` rows."""
    doc = json.loads(content.decode("utf-8"))
    fields = SeedCommittee.__dataclass_fields__
    return [SeedCommittee(**{f: row.get(f) for f in fields}) for row in doc["committees"]]
