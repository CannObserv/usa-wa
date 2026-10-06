"""Deterministic ``source_id`` keys for the WSL-derived Roles (#412).

A party's Member Role and a committee's Member Role. Pure strings, read by the #302
pipeline's ``roles`` model; the Postgres member normalizer minted identical keys until
#412 PR F. They moved out of ``usa_wa_adapter_legislature.normalize.members`` because that
module was the Postgres write path, which PR F deleted. The seat keys live in
:mod:`usa_wa_common.seats`.
"""

from __future__ import annotations


def party_role_source_id(slug: str) -> str:
    """Deterministic ``source_id`` for a party's ``Member`` Role (one per party Org)."""
    return f"party-role:{slug}"


def committee_member_role_source_id(committee_source_id: str) -> str:
    """Deterministic ``source_id`` for a committee's ``Member`` Role (one per committee)."""
    return f"committee-member-role:{committee_source_id}"
