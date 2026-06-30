"""Shared field cleaning for the WSL normalizers.

WSL string fields arrive padded or blank ("", "   "); normalizers must collapse all
"absent" forms to a single ``None`` so readers never see two truth values for missing
(the blank-``Acronym`` / whitespace-``Phone`` cases). Used by both ``committees.py``
and ``committee_meetings.py`` so the committee and Joint/`Other` classes clean identically.
"""

from __future__ import annotations

from typing import Any


def clean_field(value: Any) -> str | None:
    """Strip a WSL string field; empty / whitespace-only / non-str → ``None``."""
    if not isinstance(value, str):
        return None
    return value.strip() or None
