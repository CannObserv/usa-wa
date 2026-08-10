"""One-shot subscription prune: ``python -m usa_wa_sync_powermap.prune_subscriptions``.

The reclaim half of #73 Axis 1. :func:`build_reconciler` narrowed the subscription set
to the **mirror set** — jurisdiction ``lineage`` via PM discovery ∪ OUR locally-anchored
producer rows — so new syncs no longer subscribe the ~1,000 PM-only strangers the old
whole-subtree walk pulled in. But :meth:`SubscriptionReconciler.sync_subscriptions` is
additive (never unsubscribes), so those strangers stay registered-but-inert: the feed
keeps delivering their changes and the reconciler keeps fetching-then-skipping them.

This CLI is the deliberate cleanup: diff PM's registered subscription set against the
freshly-discovered mirror set and unsubscribe the difference. Guarded against a discovery
collapse mass-unsubscribing everything — an empty desired set aborts (``empty_desired``),
and a stale fraction over ``--max-prune-fraction`` aborts (``prune_floor``). The default
floor is permissive (0.9) because the FIRST run legitimately removes ~half the set; only a
wipe-almost-everything run (which would signal a broken discovery) aborts.

Strangers were never mirrored (no local cache row), so pruning removes only the PM
subscription — nothing local is evicted. Idempotent: a second run finds nothing stale.
No operator token (shell access is the trust boundary, as with the other reconcile CLIs);
``--dry-run`` previews the diff without unsubscribing.

Examples::

    python -m usa_wa_sync_powermap.prune_subscriptions --dry-run
    python -m usa_wa_sync_powermap.prune_subscriptions
"""

import argparse

from clearinghouse_core.database import get_session_factory
from clearinghouse_core.job import JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger
from clearinghouse_sync_powermap.engine import SyncEngine
from clearinghouse_sync_powermap.subscriptions import DEFAULT_MAX_PRUNE_FRACTION
from usa_wa_sync_powermap.config import get_sidecar_settings
from usa_wa_sync_powermap.jobs import never, run_pm_job
from usa_wa_sync_powermap.registry import build_descriptors, build_pm_client, build_reconciler

logger = get_logger(__name__)

#: Stable ledger identity (#178) — a module path can move without orphaning run history.
JOB_SLUG = "pm-subscription-prune"

#: Guardrail abort (empty desired set / prune floor) — took no action.
EXIT_ABORTED = 3


def _add_args(parser: argparse.ArgumentParser) -> None:
    """Contribute this job's own flag to the harness's shared parser."""
    parser.add_argument(
        "--max-prune-fraction",
        type=float,
        default=DEFAULT_MAX_PRUNE_FRACTION,
        help=(
            "Abort if more than this fraction of the registered set would be pruned "
            f"(default {DEFAULT_MAX_PRUNE_FRACTION}) — a near-total wipe signals a "
            "discovery collapse, not a real cleanup."
        ),
    )


async def _run(args: argparse.Namespace) -> dict:
    """Build the PM client + reconciler and run the prune. PM reads are needed even for
    a dry-run (discovery + list_subscriptions), so the api key is always required."""
    settings = get_sidecar_settings()
    if not settings.powermap_api_key:
        raise RuntimeError("POWERMAP_API_KEY is not set — required to read/modify subscriptions.")
    factory = get_session_factory()
    client = build_pm_client(settings)
    engine = SyncEngine(build_descriptors(settings), client)
    reconciler = build_reconciler(client, engine, settings)
    try:
        async with factory() as session:
            return await reconciler.prune_subscriptions(
                session,
                max_prune_fraction=args.max_prune_fraction,
                dry_run=args.dry_run,
            )
    finally:
        await client.aclose()


async def _prune_job(ctx: JobContext) -> JobResult:
    """Harness handler. ``failed_when=never``: this job's only non-zero outcomes are a
    guardrail abort and an auth block — it has no per-row rejection tally."""
    return await run_pm_job(lambda: _run(ctx.args), failed_when=never)


def main(argv: list[str] | None = None) -> int:
    """Prune stale PM subscriptions.

    Exit codes (unchanged, :mod:`usa_wa_sync_powermap.jobs`): ``0`` clean or dry-run;
    ``2`` a global auth block; ``3`` a guardrail abort (empty desired set / prune floor —
    took no action), ledgered as ``degraded``.
    """
    return run_job(
        JOB_SLUG,
        _prune_job,
        argv=argv,
        prog="python -m usa_wa_sync_powermap.prune_subscriptions",
        description=(
            "Unsubscribe PM entities outside the mirror set — the reclaim half of #73 "
            "Axis 1 (the ~1,000 strangers the old whole-subtree walk left subscribed)."
        ),
        extra_args=_add_args,
        commit=False,
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
