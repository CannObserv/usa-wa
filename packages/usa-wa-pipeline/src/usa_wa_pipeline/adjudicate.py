"""Identity adjudication CLI (#308): the only path that merges or moves.

    python -m usa_wa_pipeline.adjudicate merge --kind person \
        --loser <ULID> --survivor <ULID> --note "…"
    python -m usa_wa_pipeline.adjudicate move --kind person \
        --key <natural-key> --to <ULID> --note "…"
    python -m usa_wa_pipeline.adjudicate unmerge --kind person \
        --entity <ULID> --note "…"

Corrections are always adjudications (sticky registry, spec § tradeoffs): a
merge sets the loser's ``merged_into`` tombstone — the signal the published
crosswalk carries and PM's mapping layer follows — and a move re-points one
natural key. Every action writes a ``registry.adjudications`` row with its
note; there is no delete (a wrong adjudication is corrected by another
adjudication, so the trail stays whole — a wrong merge specifically by
``unmerge``, never by a reverse merge, which would cycle the tombstones).
``--note`` is mandatory: an unexplained identity decision is exactly what the
trail exists to prevent.
"""

from __future__ import annotations

import argparse

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.job import JobContext, JobResult, run_job
from clearinghouse_core.logging import get_logger
from clearinghouse_core.registry import RegistryAdjudication, RegistryEntity, RegistryKey

logger = get_logger(__name__)

#: Stable ledger identity (#178).
JOB_SLUG = "registry-adjudicate"


async def _require_entity(session: AsyncSession, kind: str, entity_id: str) -> RegistryEntity:
    entity = await session.get(RegistryEntity, entity_id)
    if entity is None or entity.kind != kind:
        raise ValueError(f"no {kind} entity {entity_id!r} in the registry")
    return entity


async def _require_live_entity(session: AsyncSession, kind: str, entity_id: str) -> RegistryEntity:
    entity = await _require_entity(session, kind, entity_id)
    if entity.merged_into is not None:
        raise ValueError(
            f"{kind} entity {entity_id!r} is tombstoned (merged into "
            f"{entity.merged_into!s}) — a merge survivor or move destination "
            "must be live; correct a wrong merge with the unmerge verb"
        )
    return entity


async def adjudicate_merge(
    session: AsyncSession, kind: str, *, loser: str, survivor: str, note: str
) -> None:
    """Tombstone ``loser`` into ``survivor`` and record the decision.

    The survivor must be LIVE (CR 1): a reverse merge (B→A after A→B) would
    create a tombstone cycle that drops both entities from conformed and loops
    the published crosswalk — the correction for a wrong merge is
    :func:`adjudicate_unmerge`. Requiring a live survivor also keeps chains
    shallow only in the forward direction (A→B then B→C stays legal).
    """
    if loser == survivor:
        raise ValueError("loser and survivor are the same entity")
    loser_row = await _require_entity(session, kind, loser)
    await _require_live_entity(session, kind, survivor)
    if loser_row.merged_into is not None:
        raise ValueError(f"{loser!r} is already merged into {loser_row.merged_into!s}")
    loser_row.merged_into = survivor
    session.add(
        RegistryAdjudication(
            kind=kind,
            action="merge",
            subject_entity_id=loser,
            target_entity_id=survivor,
            note=note,
        )
    )
    await session.flush()
    logger.info("registry_merge", extra={"kind": kind, "loser": loser, "survivor": survivor})


async def adjudicate_unmerge(
    session: AsyncSession, kind: str, *, entity: str, note: str
) -> list[str]:
    """Clear ``entity``'s tombstone and record the decision — the sanctioned
    recovery for a wrong merge (CR 1). Keys that were moved off this entity
    stay where they are; the returned (and logged) inventory names them —
    every ``move`` adjudication whose subject is this entity — so the operator
    can move them back deliberately instead of mining the ledger mid-incident
    (CR 41). A revived entity left keyless stays out of conformed and trips
    the parity probes until the moves are resolved."""
    row = await _require_entity(session, kind, entity)
    if row.merged_into is None:
        raise ValueError(f"{kind} entity {entity!r} is not merged — nothing to unmerge")
    former_survivor = str(row.merged_into)
    row.merged_into = None
    session.add(
        RegistryAdjudication(
            kind=kind,
            action="unmerge",
            subject_entity_id=entity,
            target_entity_id=former_survivor,
            note=note,
        )
    )
    await session.flush()
    moved_away = sorted(
        {
            key
            for (key,) in (
                await session.execute(
                    select(RegistryAdjudication.natural_key).where(
                        RegistryAdjudication.kind == kind,
                        RegistryAdjudication.action == "move",
                        RegistryAdjudication.subject_entity_id == entity,
                    )
                )
            ).all()
            if key is not None
        }
    )
    logger.info(
        "registry_unmerge",
        extra={
            "kind": kind,
            "entity": entity,
            "was_into": former_survivor,
            "keys_moved_away": moved_away[:50],
        },
    )
    return moved_away


async def adjudicate_move(
    session: AsyncSession, kind: str, *, natural_key: str, to_entity: str, note: str
) -> None:
    """Re-point one natural key at ``to_entity`` and record the decision.

    The destination must be live (CR 22): landing a key on a merged-away
    entity would be a re-point whose crosswalk row immediately says "merged
    away" — almost always a typo'd ULID."""
    await _require_live_entity(session, kind, to_entity)
    key_row = (
        await session.execute(
            select(RegistryKey).where(
                RegistryKey.kind == kind, RegistryKey.natural_key == natural_key
            )
        )
    ).scalar_one_or_none()
    if key_row is None:
        raise ValueError(f"no registered {kind} key {natural_key!r}")
    adjudication = RegistryAdjudication(
        kind=kind,
        action="move",
        subject_entity_id=str(key_row.entity_id),
        target_entity_id=to_entity,
        natural_key=natural_key,
        note=note,
    )
    session.add(adjudication)
    await session.flush()
    await session.execute(
        update(RegistryKey)
        .where(RegistryKey.id == key_row.id)
        .values(entity_id=to_entity, registered_by=f"adjudication:{adjudication.id}")
    )
    await session.flush()
    logger.info("registry_move", extra={"kind": kind, "natural_key": natural_key, "to": to_entity})


def _add_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("verb", choices=["merge", "move", "unmerge"])
    parser.add_argument("--kind", required=True, choices=["person", "org"])
    parser.add_argument("--note", required=True, help="Why — recorded on the adjudication row.")
    parser.add_argument("--loser", help="merge: the entity to tombstone.")
    parser.add_argument("--survivor", help="merge: the entity that lives on.")
    parser.add_argument("--key", help="move: the natural key to re-point.")
    parser.add_argument("--to", dest="to_entity", help="move: the destination entity.")
    parser.add_argument("--entity", help="unmerge: the tombstoned entity to revive.")


async def _adjudicate_job(ctx: JobContext) -> JobResult:
    session = ctx.require_session()
    if ctx.args.verb == "merge":
        if not (ctx.args.loser and ctx.args.survivor):
            raise SystemExit("merge needs --loser and --survivor")
        await adjudicate_merge(
            session,
            ctx.args.kind,
            loser=ctx.args.loser,
            survivor=ctx.args.survivor,
            note=ctx.args.note,
        )
    elif ctx.args.verb == "unmerge":
        if not ctx.args.entity:
            raise SystemExit("unmerge needs --entity")
        await adjudicate_unmerge(session, ctx.args.kind, entity=ctx.args.entity, note=ctx.args.note)
    else:
        if not (ctx.args.key and ctx.args.to_entity):
            raise SystemExit("move needs --key and --to")
        await adjudicate_move(
            session,
            ctx.args.kind,
            natural_key=ctx.args.key,
            to_entity=ctx.args.to_entity,
            note=ctx.args.note,
        )
    return JobResult.ok({"action": ctx.args.verb})


def main(argv: list[str] | None = None) -> int:
    """Apply one identity adjudication. ``--dry-run`` previews (harness rolls back)."""
    return run_job(
        JOB_SLUG,
        _adjudicate_job,
        argv=argv,
        prog="python -m usa_wa_pipeline.adjudicate",
        description="Merge/unmerge entities or move a key, with a recorded note (#308).",
        extra_args=_add_args,
    )


if __name__ == "__main__":
    raise SystemExit(main())
