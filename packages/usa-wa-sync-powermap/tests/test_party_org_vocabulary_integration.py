"""Integration — the party slug vocabulary against Power Map's live Orgs (#227 CR #58).

Run with ``uv run pytest -m integration``. Excluded from the default tier so the offline
suite stays hermetic.

``PARTY_SLUGS`` claims each slug is the ``org_wa_party`` identifier value of a real Org
(power-map#270/#442/#443) — that is what lets a party be addressed by identifier rather than
by name-match or ULID. Nothing offline can hold PM to that: a PM-side rename or an
unexecuted seed leaves the vocabulary emitting slugs with no Org, and #228 would produce
party spans that resolve to nothing. The failure is silent by construction, so it needs a
test that actually asks PM.

Paced at the project's courtesy floor (#85): an unpaced sweep of even eight ids earns a 429.
"""

from __future__ import annotations

import asyncio
import os

import httpx
import pytest

from usa_wa_common.parties import PARTY_SLUGS

#: Also marked ``db`` although this test touches no database: ``scripts/tests/test_unit_tier.py``
#: pins that every ``integration`` test carries ``db`` too, because the bare ``-m 'not db'``
#: form would otherwise silently re-enable the integration tier. The overlap is load-bearing
#: for the marker algebra, not a claim about what this test needs.
pytestmark = [pytest.mark.integration, pytest.mark.db]

PM_BASE = "https://power-map.exe.xyz"
IDENTIFIER_TYPE = "org_wa_party"
#: Seconds between PM calls — mirrors ``POWERMAP_MIN_REQUEST_INTERVAL``'s intent (#85).
REQUEST_INTERVAL = 0.5


async def _org_for_slug(client: httpx.AsyncClient, api_key: str, slug: str) -> dict | None:
    await asyncio.sleep(REQUEST_INTERVAL)
    response = await client.get(
        f"{PM_BASE}/api/v1/orgs/search",
        params={
            "q": "",
            "identifier_type": IDENTIFIER_TYPE,
            "identifier_value": slug,
            "limit": 2,
        },
        headers={"X-API-Key": api_key},
    )
    response.raise_for_status()
    data = response.json().get("data") or []
    return data[0] if data else None


async def test_every_declared_party_slug_addresses_exactly_one_live_org() -> None:
    api_key = os.environ.get("POWERMAP_API_KEY")
    if not api_key:
        pytest.skip("POWERMAP_API_KEY not set")

    missing: list[str] = []
    async with httpx.AsyncClient(timeout=30) as client:
        for slug in sorted(PARTY_SLUGS):
            if await _org_for_slug(client, api_key, slug) is None:
                missing.append(slug)

    assert not missing, (
        f"declared party slugs with no PM Org under {IDENTIFIER_TYPE}: {missing}. "
        "Either the seed has not been executed against production or PM renamed the "
        "identifier value; #228's party spans would resolve to nothing."
    )
