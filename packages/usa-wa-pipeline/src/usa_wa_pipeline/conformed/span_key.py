"""The published assignment's structural key, serialized (usa-wa#370).

A published assignment carries **no identifier of its own**. #302 gave assignments
deterministic structural keys and no registry, so the row is named by the tuple

    (entity_id, role_key, span_kind, span_discriminator, span_start_biennium)

which is unique across the conformed set (8,395 / 8,395 on the 2026-09-11 snapshot).

power-map#490 needs that tuple as **one string** so it can re-key its assignment
crosswalk off usa-wa's retiring Postgres ULIDs and onto the key the dataset
actually has. It shipped on both `assignments` and `pm_anchors` for the duration
of the cutover; usa-wa#314 retired the latter once power-map#525 had re-keyed. Its applier measures
retraction-as-absence in the dataset's own key space; before this column there was
nothing for an assignment anchor to be absent *from* (the crosswalk's `usa_wa_id`
appears in no published column, so crosswalk-membership and dataset-membership
overlapped on 0 of 8,777 assignment rows).

**One serializer.** PM asked for a producer-serialized column precisely so the two
sides never disagree about how five fields become one string. While the cutover ran
there were two sinks and the rule had teeth: `anchor_export` did not re-derive the
key but joined each anchored canonical row to the built `assignments` table and
copied it, making divergence unrepresentable rather than unlikely. usa-wa#314
retired that export once power-map#525 re-keyed its crosswalk onto this column, so
`assignments` is the sole sink and the key is the sole handle PM holds on an
assignment row.

**The key is not stable across a re-segmentation, and that is deliberate** —
power-map#490 asked for it stated on both sides. ``span_start_biennium`` is part
of the identity, so a change to where a tenure *begins* mints a new key and
retires the old one; usa-wa#289, which rejoined a member's split party spans,
reaches a subscriber as an archive plus a create rather than an update. That is
the honest shape: the two rows assert different facts about when the membership
started, and pretending one mutated into the other would hide the correction.
PM reports the pair as a supersession candidate for triage rather than applying
both silently.

The separator is ``|`` rather than ``:`` because ``role_key`` already contains
colons (``committee-member-role:31635``). A value carrying the separator is
**refused**, not escaped: escaping would make the key unreadable in a report and
put a decoder on PM's side, while a refusal fails the build the day a new
vocabulary needs the character — which is when the decision should be re-made.
"""

from __future__ import annotations

#: Joins the five fields. Absent from every published value today (the live
#: charset across all five is ``-0-9:A-Za-z``), and kept that way by the refusal
#: in :func:`span_key`.
SPAN_KEY_SEPARATOR = "|"

#: The tuple's fields, in the dataset's own column order — so a reader can line a
#: key up against the row it names without consulting a spec.
SPAN_KEY_FIELDS = (
    "entity_id",
    "role_key",
    "span_kind",
    "span_discriminator",
    "span_start_biennium",
)


def span_key(
    *,
    entity_id: str,
    role_key: str,
    span_kind: str,
    span_discriminator: str,
    span_start_biennium: str,
) -> str:
    """The five structural fields as one string, or ``ValueError``.

    Keyword-only: five same-typed positional strings is exactly the signature a
    caller silently transposes, and a transposed key is a valid-looking string that
    names a different assignment.
    """
    values = (entity_id, role_key, span_kind, span_discriminator, span_start_biennium)
    for name, value in zip(SPAN_KEY_FIELDS, values, strict=True):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"span_key field {name!r} is empty — the key would be ambiguous")
        if SPAN_KEY_SEPARATOR in value:
            raise ValueError(
                f"span_key field {name!r} contains the separator "
                f"{SPAN_KEY_SEPARATOR!r} ({value!r}) — two different tuples would "
                "serialize identically. Move the separator rather than escaping it."
            )
    return SPAN_KEY_SEPARATOR.join(values)
