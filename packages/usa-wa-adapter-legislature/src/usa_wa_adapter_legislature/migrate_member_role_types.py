"""One-shot reclassify of the generic ``member`` Role slug to PM's catalog slugs (usa-wa#110).

Two producers (``committee_span_emit``, ``sponsor_span_emit``) historically stamped every
membership Role with the generic ``role_type='member'``. PM's role_types catalog (power-map#268)
refines that into ``committee_member`` (committee membership) and ``party_member`` (party
membership), so the local classifier sat permanently diverged from PM's ``role_type_slug`` — the
#109 no-op gate read a genuine diff (``member`` != ``committee_member``) and re-enqueued the row
every reconcile forever (~305 auto-attached re-sends/cycle, the #110 role cohort).

The emitters now stamp the catalog slug on **new** rows, but ``get_or_create_role`` is
SELECT-or-INSERT (it never rewrites an existing row's classifier) and the daily refresh only
re-drives the *current* cohort — so historical/defunct committee Roles would keep the stale slug.
This CLI reclassifies **every** anchored-or-not ``member`` Role once, mapping by its deterministic
``source_id`` prefix:

- ``committee-member-role:*`` → ``committee_member``
- ``party-role:*``            → ``party_member``

A ``member`` Role with any other prefix is left untouched and counted ``skipped_unknown_prefix``
(warned) — the mapping is intentionally exhaustive over the two known producers, not a guess.
Reclassifying the local classifier makes ``to_observation`` send the matching slug, so the next
reconcile's gate reads a true no-op and adopts PM's clock → the churn stops.

``role_type`` is a plain canonical column (Roles carry no provenance ledger) → **app role**.
Idempotent — a second run finds nothing to change. ``--dry-run`` previews; exit ``0``.

    python -m usa_wa_adapter_legislature.migrate_member_role_types --dry-run
    python -m usa_wa_adapter_legislature.migrate_member_role_types
"""

import argparse
import asyncio
import json
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from clearinghouse_core.database import get_session_factory
from clearinghouse_core.logging import configure_logging, get_logger
from clearinghouse_domain_legislative.identity import Role

logger = get_logger(__name__)

_SOURCE = "usa_wa_legislature"
_STALE_SLUG = "member"

#: ``source_id`` prefix → PM catalog slug. Kept in lockstep with the emitters'
#: ``committee_member_role_source_id`` / ``party_role_source_id`` builders and their
#: ``_MEMBER_ROLE_TYPE`` constants (usa-wa#110).
_PREFIX_TO_SLUG = {
    "committee-member-role:": "committee_member",
    "party-role:": "party_member",
}


def _target_slug(source_id: str) -> str | None:
    for prefix, slug in _PREFIX_TO_SLUG.items():
        if source_id.startswith(prefix):
            return slug
    return None


async def migrate_member_role_types(session: AsyncSession) -> dict:
    """Reclassify every ``member`` Role to its catalog slug by ``source_id`` prefix.

    Executes in the caller's transaction; does not commit. Idempotent.
    """
    rows = list(
        (
            await session.execute(
                select(Role).where(Role.source == _SOURCE, Role.role_type == _STALE_SLUG)
            )
        )
        .scalars()
        .all()
    )
    reclassified: dict[str, int] = {}
    skipped_unknown = 0
    for role in rows:
        slug = _target_slug(role.source_id)
        if slug is None:
            skipped_unknown += 1
            logger.warning(
                "member_role_unknown_prefix",
                extra={"source_id": role.source_id, "role_id": str(role.id)},
            )
            continue
        role.role_type = slug
        reclassified[slug] = reclassified.get(slug, 0) + 1
        logger.info(
            "member_role_reclassified",
            extra={"source_id": role.source_id, "role_type": slug},
        )
    await session.flush()
    return {
        "checked": len(rows),
        "reclassified": reclassified,
        "reclassified_total": sum(reclassified.values()),
        "skipped_unknown_prefix": skipped_unknown,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m usa_wa_adapter_legislature.migrate_member_role_types",
        description="Reclassify generic `member` Roles to PM catalog slugs to stop churn (#110).",
    )
    parser.add_argument("--dry-run", action="store_true", help="preview without committing")
    return parser


async def _run(args: argparse.Namespace) -> dict:
    async with get_session_factory()() as session:
        result = await migrate_member_role_types(session)
        if args.dry_run:
            await session.rollback()
            result = {**result, "dry_run": True}
        else:
            await session.commit()
        return result


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = _build_parser().parse_args(argv)
    result = asyncio.run(_run(args))
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
