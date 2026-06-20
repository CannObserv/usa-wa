"""One-off backfill: re-observe produced orgs so PM picks up phone ``display_label``.

The first org-observation run (2026-06-19) submitted 30 committee phones with no
``display_label`` (usa-wa#31). ``to_observation``/``to_enrich_observation`` now
synthesize a label, but only *future* observations carry it — the phone value
itself is unchanged, so neither the feed nor the sweep re-emits the already-anchored
rows. This backfill closes that gap with a one-off re-observation of every produced
org that holds a phone, exercising PM's round-trip update path:

- anchored rows → the enrich observation (keyed by ``pm_org_id``);
- a produced-but-unanchored row → the full observe payload (identifier-keyed).

Both now carry the labelled ``contact_methods``. It is a thin
``python -m usa_wa_sync_powermap.backfill_contact_labels`` operator surface — no
operator token (shell access is the trust boundary, as with the redrive CLI), and
``--dry-run`` previews the cohort without submitting. Safe to re-run: re-observing
an unchanged entity is idempotent in PM.

Examples::

    python -m usa_wa_sync_powermap.backfill_contact_labels --dry-run
    python -m usa_wa_sync_powermap.backfill_contact_labels
"""

import argparse
import asyncio
import json
import sys
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.database import get_session_factory
from clearinghouse_core.logging import configure_logging, get_logger
from clearinghouse_domain_legislative.identity import Organization
from clearinghouse_sync_powermap.descriptors import EntityDescriptor
from clearinghouse_sync_powermap.pmclient import GeneratedPowerMapClient
from usa_wa_sync_powermap.config import get_sidecar_settings
from usa_wa_sync_powermap.descriptors import OrganizationDescriptor

logger = get_logger(__name__)


async def backfill_contact_labels(
    session: AsyncSession,
    descriptor: EntityDescriptor,
    client: Any,
    *,
    dry_run: bool = False,
) -> dict:
    """Re-observe every produced org that holds a phone so PM adopts the new label.

    Selects the contact-bearing cohort (``phone IS NOT NULL``), builds each row's
    observation through ``descriptor`` (enrich when anchored, else full observe),
    and posts it. On an anchoring disposition the anchor is (re)written — a no-op
    for already-anchored rows, but it captures one for a previously-unanchored row.
    Returns a JSON-able summary; ``dry_run`` counts the cohort without posting.
    """
    rows = (
        (await session.execute(select(Organization).where(Organization.phone.is_not(None))))
        .scalars()
        .all()
    )
    submitted = 0
    anchored = 0
    for row in rows:
        if dry_run:
            continue
        if descriptor.anchor_value(row) is not None:
            payload = await descriptor.to_enrich_observation(session, row)
        else:
            payload = await descriptor.to_observation(session, row)
        result = await client.post_observation(descriptor.observe_path, payload)
        submitted += 1
        if result.anchored:
            descriptor.set_anchor(row, result.pm_id)
            anchored += 1
        logger.info(
            "contact_label_backfill_submitted",
            extra={
                "source_id": row.source_id,
                "disposition": result.disposition,
                "anchored": result.anchored,
            },
        )
    return {
        "scanned": len(rows),
        "submitted": submitted,
        "anchored": anchored,
        "dry_run": dry_run,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m usa_wa_sync_powermap.backfill_contact_labels",
        description="Re-observe produced orgs so PM adopts phone contact display_labels (#31).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count the contact-bearing cohort without submitting any observation.",
    )
    return parser


async def _run(dry_run: bool) -> dict:
    """Open a session + PM client, run the backfill, and commit any anchor writes."""
    settings = get_sidecar_settings()
    if not dry_run and not settings.powermap_api_key:
        raise RuntimeError("POWERMAP_API_KEY is not set — required to submit observations.")
    factory = get_session_factory()
    client = GeneratedPowerMapClient(settings.powermap_base_url, settings.powermap_api_key)
    try:
        async with factory() as session:
            result = await backfill_contact_labels(
                session, OrganizationDescriptor(), client, dry_run=dry_run
            )
            if not dry_run:
                await session.commit()
            return result
    finally:
        await client.aclose()


def main(argv: list[str] | None = None) -> int:
    """Parse args, run the backfill, and print the summary as JSON. Returns exit code."""
    configure_logging()
    args = _build_parser().parse_args(argv)
    result = asyncio.run(_run(args.dry_run))
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
