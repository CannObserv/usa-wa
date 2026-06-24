"""Read-side query helpers for the identity cluster.

The local identity tables (Person/Organization/Role/Assignment) are a query-
latency mirror of Power Map. The sidecar stamps a ``retired_at`` tombstone in two
cases — when PM deletes an entity with no surviving merge-winner (usa-wa#31/#36),
and when PM *archives* it (its "inactive" signal, mirrored from the read model's
``archived_at``, usa-wa#40) — and keeps the row as provenance rather than hard-
deleting it. A retired row therefore still carries its PM anchor and is otherwise
untouched, so it would leak into the read fan-out unless callers filter it out.

:func:`exclude_retired` is the one guardrail every *live* read routes through, so
the ``retired_at IS NULL`` predicate is spelled once and the audit/provenance
escape hatch (``include_retired=True``) is explicit at the call site (usa-wa#38).
"""

from sqlalchemy import Select

from clearinghouse_domain_legislative.identity import RetirableMixin


def exclude_retired(
    stmt: Select, *models: type[RetirableMixin], include_retired: bool = False
) -> Select:
    """Filter retired (soft-deleted) rows out of a SELECT, one model hop at a time.

    Apply once per :class:`RetirableMixin` model the statement reads or joins
    through — a live role hanging off a *retired* org is dropped only if the org
    hop is filtered too, so pass every retirable model in the query::

        exclude_retired(
            select(Role).join(Organization, Role.organization_id == Organization.id),
            Role,
            Organization,
        )

    ``include_retired=True`` is the audit/provenance escape hatch: it returns
    ``stmt`` unchanged. Passing no models raises — a silent no-op would leak
    retired rows, the exact bug this helper exists to prevent.
    """
    if include_retired:
        return stmt
    if not models:
        raise ValueError("exclude_retired requires at least one model to filter")
    for model in models:
        stmt = stmt.where(model.not_retired())
    return stmt
