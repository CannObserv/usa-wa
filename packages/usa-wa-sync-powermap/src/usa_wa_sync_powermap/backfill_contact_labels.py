"""One-off backfill: re-observe produced orgs so PM picks up phone ``display_label``
and object-shape ``org_acronyms``.

The first org-observation run (2026-06-19) submitted 30 committee phones with no
``display_label`` (usa-wa#31) and 34 acronyms in the bare-string shape PM 422-rejects.
``to_observation``/``to_enrich_observation`` now synthesize a label and emit the
``{acronym}`` object, but only *future* observations carry the fix — the underlying
phone/acronym values are unchanged, so neither the feed nor the sweep re-emits the
already-anchored rows (and ``needs_enrich`` gates on identifier presence, which the
committees already have, so the enrich never re-fires). This backfill closes that gap
with a one-off re-observation of every produced org that holds a phone **or** an
acronym (#33), exercising PM's round-trip update path:

- anchored rows → the enrich observation (keyed by ``pm_org_id``);
- a produced-but-unanchored row → the full observe payload (identifier-keyed).

Both now carry the labelled ``contact_methods``. It is a thin
``python -m usa_wa_sync_powermap.backfill_contact_labels`` operator surface — no
operator token (shell access is the trust boundary, as with the redrive CLI), and
``--dry-run`` previews the cohort without submitting. Safe to re-run: re-observing
an unchanged entity is idempotent in PM.

Status (#34): the sidecar now self-heals this class of drift on its own — the
anchored-cohort reconcile re-enqueues an ENRICH whenever a row's carry payload
differs from the last one it settled (a local fingerprint, see
``SyncEngine._enrich_payload_drifted``). So this CLI is no longer the *only*
recovery path; it remains a force-push convenience for an immediate, bounded,
operator-driven backfill (e.g. right after a shape fix, without waiting for the
next reconcile cadence).

Examples::

    python -m usa_wa_sync_powermap.backfill_contact_labels --dry-run
    python -m usa_wa_sync_powermap.backfill_contact_labels
"""

import argparse
import asyncio
import json
import sys
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.database import get_session_factory
from clearinghouse_core.logging import configure_logging, get_logger
from clearinghouse_domain_legislative.identity import Organization
from clearinghouse_sync_powermap.client import DeliveryBlockedError, PayloadRejectedError
from clearinghouse_sync_powermap.descriptors import EntityDescriptor
from clearinghouse_sync_powermap.engine import TRANSIENT_EXCEPTIONS
from clearinghouse_sync_powermap.pmclient import GeneratedPowerMapClient
from usa_wa_sync_powermap.config import get_sidecar_settings
from usa_wa_sync_powermap.descriptors import OrganizationDescriptor

logger = get_logger(__name__)

#: Source whose orgs carry WSL-sourced phones — the only producer of contact rows
#: today. Scopes the cohort so a future phone-bearing source isn't swept in silently.
_SOURCE = "usa_wa_legislature"
#: Per-row delivery failures isolated so one bad row doesn't abort the run: transport
#: blips (retry on the next run). A ``PayloadRejectedError`` (422) is caught separately
#: and counted as ``rejected``. A ``DeliveryBlockedError`` (401/403) is deliberately
#: **not** caught — it's a global credential failure, not a per-row condition, so no
#: other row will succeed; letting it propagate aborts fast. Bugs propagate too — the
#: engine's stance: never mask a real bug.
_DELIVERY_FAILURES = TRANSIENT_EXCEPTIONS


async def backfill_contact_labels(
    session: AsyncSession,
    descriptor: EntityDescriptor,
    client: Any,
    *,
    dry_run: bool = False,
) -> dict:
    """Re-observe every produced org that holds a phone or acronym so PM adopts the
    new label and the object-shape acronym.

    Selects the WSL cohort (``source == usa_wa_legislature`` AND
    (``phone IS NOT NULL`` OR ``acronym IS NOT NULL``)), builds each row's observation
    through ``descriptor``
    (enrich when anchored, else full observe), and posts it. A previously-unanchored
    row that PM anchors has its anchor captured; an already-anchored row is left
    untouched. Each row is isolated: a transport blip or a PM rejection is counted
    and skipped, never aborting the run. A global auth block (``DeliveryBlockedError``)
    and real bugs propagate — no point posting every remaining row to a dead endpoint.
    Returns a JSON-able outcome breakdown that sums to ``scanned``; ``dry_run`` counts
    the cohort without posting (and needs no client).
    """
    rows = (
        (
            await session.execute(
                select(Organization).where(
                    Organization.source == _SOURCE,
                    or_(Organization.phone.is_not(None), Organization.acronym.is_not(None)),
                )
            )
        )
        .scalars()
        .all()
    )
    summary = {
        "scanned": len(rows),
        "accepted": 0,
        "rejected": 0,
        "failed": 0,
        "skipped": 0,
        "dry_run": dry_run,
    }
    if dry_run:
        return summary
    for row in rows:
        if not await descriptor.dependencies_ready(session, row):
            # A PM prerequisite (e.g. parent org) isn't anchored — the same gate the
            # engine enforces before delivery. Skip rather than post a malformed obs.
            summary["skipped"] += 1
            logger.warning("contact_label_backfill_skipped", extra={"source_id": row.source_id})
            continue
        if descriptor.anchor_value(row) is not None:
            payload = await descriptor.to_enrich_observation(session, row)
        else:
            payload = await descriptor.to_observation(session, row)
        try:
            result = await client.post_observation(descriptor.observe_path, payload)
        except PayloadRejectedError as exc:
            summary["rejected"] += 1
            logger.warning(
                "contact_label_backfill_rejected",
                extra={"source_id": row.source_id, "error": str(exc)},
            )
            continue
        except _DELIVERY_FAILURES as exc:
            summary["failed"] += 1
            logger.warning(
                "contact_label_backfill_failed",
                extra={"source_id": row.source_id, "error": repr(exc)},
            )
            continue
        if result.anchored:
            summary["accepted"] += 1
            if descriptor.anchor_value(row) is None:
                # Capture the anchor only for a genuinely new row; never re-point an
                # existing anchor (PM echoes the same id for an enrich re-observe).
                descriptor.set_anchor(row, result.pm_id)
        elif result.rejected:
            summary["rejected"] += 1
        else:
            summary["failed"] += 1
        logger.info(
            "contact_label_backfill_submitted",
            extra={
                "source_id": row.source_id,
                "disposition": result.disposition,
                "anchored": result.anchored,
            },
        )
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m usa_wa_sync_powermap.backfill_contact_labels",
        description=(
            "Re-observe produced orgs so PM adopts phone display_labels and "
            "object-shape acronyms (#31, #33)."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Count the phone/acronym-bearing cohort without submitting any observation.",
    )
    return parser


async def _run(dry_run: bool) -> dict:
    """Open a session (+ PM client when submitting), run the backfill, and commit
    any anchor writes. A ``dry_run`` reads only — no client is constructed."""
    settings = get_sidecar_settings()
    factory = get_session_factory()
    if dry_run:
        async with factory() as session:
            return await backfill_contact_labels(
                session, OrganizationDescriptor(), None, dry_run=True
            )
    if not settings.powermap_api_key:
        raise RuntimeError("POWERMAP_API_KEY is not set — required to submit observations.")
    client = GeneratedPowerMapClient(settings.powermap_base_url, settings.powermap_api_key)
    try:
        async with factory() as session:
            result = await backfill_contact_labels(session, OrganizationDescriptor(), client)
            await session.commit()
            return result
    finally:
        await client.aclose()


def main(argv: list[str] | None = None) -> int:
    """Parse args, run the backfill, and print the summary as JSON.

    Exit codes: ``0`` clean (or dry-run); ``1`` some rows rejected/failed; ``2`` a
    global auth block (``DeliveryBlockedError``) aborted the run — reported as a
    one-line diagnostic instead of a raw traceback, so the JSON contract holds.
    """
    configure_logging()
    args = _build_parser().parse_args(argv)
    try:
        result = asyncio.run(_run(args.dry_run))
    except DeliveryBlockedError as exc:
        json.dump(
            {"error": "delivery blocked — check POWERMAP_API_KEY", "detail": str(exc)}, sys.stdout
        )
        sys.stdout.write("\n")
        return 2
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")
    return 1 if result.get("rejected", 0) or result.get("failed", 0) else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
