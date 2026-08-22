"""One-shot: retire outbox rejections that a later re-enqueue already replaced (#258).

Runs after a rejection wave has been fixed in code and the affected rows re-enqueued. Those
old ``REJECTED`` entries are settled history, but ``REJECTED`` is the operator's to-do list
and the sidecar alerts on its *rise* — so a permanent pile holds the count static, which
reads as "nothing new", and the next real rejection hides inside it. Each replaced entry
moves to ``SUPERSEDED``; the row is kept, so the incident stays legible.

Local status write on the ``sync`` schema → app role. No PM traffic and no operator token:
the shell is the trust boundary, as for the other one-shots. Idempotent — a second run finds
nothing, because the rows it moved are no longer ``REJECTED``. Exit ``0`` always: there is no
partial state to report, only a count.

    python -m usa_wa_sync_powermap.supersede_rejections --dry-run
"""

from clearinghouse_core.job import JobContext, JobResult, run_job
from clearinghouse_sync_powermap.engine.maintenance import supersede_stale_rejections

JOB_SLUG = "outbox-supersede-rejections"


async def _supersede_job(ctx: JobContext) -> JobResult:
    """Harness handler: the harness owns the session and the commit (``commit=True``), so
    a dry run needs no rollback — :func:`supersede_stale_rejections` writes nothing."""
    moved = await supersede_stale_rejections(ctx.require_session(), dry_run=ctx.dry_run)
    return JobResult(counters={"superseded": moved})


def main(argv: list[str] | None = None) -> int:
    """Move superseded outbox rejections out of the operator's backlog."""
    return run_job(
        JOB_SLUG,
        _supersede_job,
        argv=argv,
        prog="python -m usa_wa_sync_powermap.supersede_rejections",
        description=(
            "Retire REJECTED outbox entries a later PENDING re-enqueue already replaced "
            "(#258), so the REJECTED backlog means 'still needs an operator' again."
        ),
        dry_run_help="count the entries that would move, write nothing",
    )


if __name__ == "__main__":
    raise SystemExit(main())
