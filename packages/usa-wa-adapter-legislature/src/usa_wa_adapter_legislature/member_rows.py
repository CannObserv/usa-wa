"""Predicates over a WSL ``Member`` row, shared by every reader of the member wire (#412).

The span projectors, roster hygiene and the #302 pipeline all screen the same rows the
same way. The screen moved out of ``usa_wa_adapter_legislature.normalize.members`` — the
Postgres write path, deleted in #412 PR F — so the pipeline could apply it without
importing that module.
"""

from __future__ import annotations

from typing import Any


def is_person(member: dict[str, Any]) -> bool:
    """True when a ``Member`` row is a named legislator (both first + last present).

    Filters the name-blanked stubs ``GetSponsors`` returns for a superseded / departed
    (member, chamber-tenure) — a real ``Id`` but no name/district/party (step 0 finding)."""
    return bool((member.get("FirstName") or "").strip() and (member.get("LastName") or "").strip())
