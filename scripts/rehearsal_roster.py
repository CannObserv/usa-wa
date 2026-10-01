"""Synthesize the #135 rehearsal's partial roster: a subset of one biennium's sponsors,
recorded as the next biennium's ``sponsors:`` wire in a SCRATCH raw store.

    python scripts/rehearsal_roster.py --root <scratch>/raw --from 2025-26 --to 2027-28 --keep 0.6

The rollover's partial case — the new biennium's roster half-published — cannot be fetched
before it happens, so ``scripts/rollover-rehearsal.sh partial`` builds from this instead. The
subset is deterministic (a hash of each member's ``Id``), so two rehearsals of the scenario
build from the same roster, and every kept ``<Member>`` is byte-for-byte the outgoing wire's:
staging parses it exactly as it parses a fetched one.

It cannot write anywhere but a rehearsal: ``--root``'s parent must hold the ``.rehearsal``
marker the wrapper writes, which no production directory has. The manifest entry says what
it is — a ``rehearsal://`` URL, ``rehearsal: partial`` and the wire it was cut from — so it is
never mistaken for a fetch. Exit 2 = refused (no marker, or no source wire); nothing written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from clearinghouse_core.rawstore import RawStore
from usa_wa_adapter_legislature.coverage import WSL_SOURCE_SLUG
from usa_wa_adapter_legislature.resources import SPONSORS_RESOURCE_PREFIX

MARKER = ".rehearsal"
"""The file ``scripts/rollover-rehearsal.sh`` writes in its scratch dir, beside ``raw/``."""

_NAMESPACES = {
    "soap": "http://schemas.xmlsoap.org/soap/envelope/",
    "xsi": "http://www.w3.org/2001/XMLSchema-instance",
    "xsd": "http://www.w3.org/2001/XMLSchema",
    "": "http://WSLWebServices.leg.wa.gov/",
}
_WSL = "{" + _NAMESPACES[""] + "}"


class Refused(Exception):
    """Nothing was written; the message says why."""


def _kept(member_id: str, keep: float) -> bool:
    """Deterministic, and spread across both chambers rather than one end of the alphabet."""
    return int(hashlib.sha256(member_id.encode()).hexdigest(), 16) % 1000 < keep * 1000


def synthesize(root: Path, *, source: str, target: str, keep: float) -> dict[str, int]:
    """Record ``sponsors:<target>`` as the ``keep`` share of ``sponsors:<source>``'s members."""
    if not (root.resolve().parent / MARKER).is_file():
        raise Refused(f"{root} is not a rehearsal raw store: no {MARKER} beside it")
    store = RawStore(root, WSL_SOURCE_SLUG)
    source_id = f"{SPONSORS_RESOURCE_PREFIX}{source}"
    newest = store.latest().get(source_id)
    if newest is None or not newest.get("sha256"):
        raise Refused(f"{source_id} has no stored wire in {root}")

    for prefix, uri in _NAMESPACES.items():
        ET.register_namespace(prefix, uri)
    wire = store.object_path(newest["sha256"]).read_bytes()
    envelope = ET.fromstring(wire)  # noqa: S314 — our own stored wire, expat entity-hardened
    total = kept = 0
    for result in envelope.iter(f"{_WSL}GetSponsorsResult"):
        for member in list(result.findall(f"{_WSL}Member")):
            total += 1
            if _kept(member.findtext(f"{_WSL}Id", default=""), keep):
                kept += 1
            else:
                result.remove(member)

    run = store.open_run()
    run.record(
        f"{SPONSORS_RESOURCE_PREFIX}{target}",
        ET.tostring(envelope, encoding="utf-8", xml_declaration=True),
        url=f"rehearsal://synthesized/{source_id}?keep={keep}",
        content_type="text/xml",
        extra={"rehearsal": "partial", "synthesized_from": source_id},
    )
    run.close()
    return {"kept": kept, "of": total}


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; prints one JSON summary line."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", required=True, type=Path, help="The scratch raw store root.")
    parser.add_argument("--from", dest="source", required=True, help="Outgoing biennium.")
    parser.add_argument("--to", dest="target", required=True, help="Incoming biennium.")
    parser.add_argument("--keep", type=float, default=0.6, help="Share of members kept (0-1).")
    args = parser.parse_args(argv)
    try:
        summary = synthesize(args.root, source=args.source, target=args.target, keep=args.keep)
    except Refused as exc:
        print(f"rehearsal_roster: refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"resource_id": f"{SPONSORS_RESOURCE_PREFIX}{args.target}", **summary}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
