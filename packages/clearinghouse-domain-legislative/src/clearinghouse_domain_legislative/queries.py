"""Read-side query helpers for the identity cluster.

The local identity tables (Person/Organization/Role/Assignment) are a query-
latency mirror of Power Map. The sidecar marks a row not-live on either of two PM-
parity axes (usa-wa#42) and keeps the row as provenance rather than hard-deleting
it:

- ``archived_at`` — PM archived the entity (its reversible "inactive" signal,
  mirrored from PM's ``archived_at``, usa-wa#40/#41). The PM id is still live.
- ``deleted_at`` — PM deleted the entity with no surviving merge-winner
  (usa-wa#31/#36). The PM id is gone.

Either marker keeps its PM anchor and is otherwise untouched, so a non-live row
would leak into the read fan-out unless callers filter it out.

:func:`live_only` is the one guardrail every *live* read routes through, so the
``archived_at IS NULL AND deleted_at IS NULL`` predicate is spelled once and the
audit/provenance escape hatch (``include_hidden=True``) is explicit at the call
site (usa-wa#38).
"""

from sqlalchemy import Select

from clearinghouse_domain_legislative.identity import LifecycleMixin


def live_only(stmt: Select, *models: type[LifecycleMixin], include_hidden: bool = False) -> Select:
    """Filter non-live (archived or deleted) rows out of a SELECT, one model hop at a time.

    Apply once per :class:`LifecycleMixin` model the statement reads or joins
    through — a live role hanging off an *archived* org is dropped only if the org
    hop is filtered too, so pass every lifecycle model in the query::

        live_only(
            select(Role).join(Organization, Role.organization_id == Organization.id),
            Role,
            Organization,
        )

    ``include_hidden=True`` is the audit/provenance escape hatch: it returns
    ``stmt`` unchanged. Passing no models raises — a silent no-op would leak
    non-live rows, the exact bug this helper exists to prevent.
    """
    if include_hidden:
        return stmt
    if not models:
        raise ValueError("live_only requires at least one model to filter")
    for model in models:
        stmt = stmt.where(model.is_live())
    return stmt
